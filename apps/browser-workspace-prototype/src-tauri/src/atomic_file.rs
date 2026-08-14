use crate::windows_process::process_is_running;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use windows_sys::Win32::Storage::FileSystem::{
    MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
};

const REPLACE_TIMEOUT: Duration = Duration::from_secs(2);
const LOCK_TIMEOUT: Duration = Duration::from_secs(2);
const INITIAL_DELAY: Duration = Duration::from_millis(10);
const MAX_DELAY: Duration = Duration::from_millis(200);
const MALFORMED_LOCK_GRACE: Duration = Duration::from_secs(5);
const MAX_LOCK_AGE: Duration = Duration::from_secs(30 * 60);
static FILE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
struct LockOwner {
    process_id: u32,
    created_at_ms: u128,
    token: String,
}

pub(crate) struct ExclusiveFileLock {
    path: PathBuf,
    token: String,
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn unique_token() -> String {
    format!(
        "{}-{}-{}",
        std::process::id(),
        now_millis(),
        FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}

fn replace_file(temp_path: &Path, target_path: &Path) -> io::Result<()> {
    let temp_wide: Vec<u16> = temp_path.as_os_str().encode_wide().chain(Some(0)).collect();
    let target_wide: Vec<u16> = target_path
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            temp_wide.as_ptr(),
            target_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn retryable_replace_error(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::PermissionDenied
        || matches!(error.raw_os_error(), Some(5 | 32 | 33))
}

fn lock_contention_error(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::AlreadyExists || retryable_replace_error(error)
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", unique_token()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);

        let started_at = Instant::now();
        let mut delay = INITIAL_DELAY;
        loop {
            match replace_file(&temporary, path) {
                Ok(()) => return Ok(()),
                Err(error)
                    if retryable_replace_error(&error)
                        && started_at.elapsed() < REPLACE_TIMEOUT =>
                {
                    thread::sleep(delay);
                    delay = delay.saturating_mul(2).min(MAX_DELAY);
                }
                Err(error) => return Err(error),
            }
        }
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn lock_age(path: &Path) -> Option<Duration> {
    fs::metadata(path).ok()?.modified().ok()?.elapsed().ok()
}

fn stale_lock(path: &Path) -> bool {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(_) => return lock_age(path).is_some_and(|age| age >= MALFORMED_LOCK_GRACE),
    };
    match serde_json::from_slice::<LockOwner>(&bytes) {
        Ok(owner) => {
            let age_ms = now_millis().saturating_sub(owner.created_at_ms);
            !process_is_running(owner.process_id) || age_ms >= MAX_LOCK_AGE.as_millis()
        }
        Err(_) => lock_age(path).is_some_and(|age| age >= MALFORMED_LOCK_GRACE),
    }
}

impl ExclusiveFileLock {
    pub(crate) fn acquire(path: &Path) -> io::Result<Self> {
        Self::acquire_with_timeout(path, LOCK_TIMEOUT)
    }

    fn acquire_with_timeout(path: &Path, timeout: Duration) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let started_at = Instant::now();
        let mut delay = INITIAL_DELAY;
        let mut observed_lock = false;
        loop {
            let token = unique_token();
            let owner = LockOwner {
                process_id: std::process::id(),
                created_at_ms: now_millis(),
                token: token.clone(),
            };
            match OpenOptions::new().write(true).create_new(true).open(path) {
                Ok(mut file) => {
                    let write_result = (|| {
                        let bytes = serde_json::to_vec(&owner)
                            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
                        file.write_all(&bytes)?;
                        file.sync_all()
                    })();
                    if let Err(error) = write_result {
                        drop(file);
                        let _ = fs::remove_file(path);
                        return Err(error);
                    }
                    return Ok(Self {
                        path: path.to_path_buf(),
                        token,
                    });
                }
                Err(error) if lock_contention_error(&error) => {
                    let lock_exists = path.exists();
                    observed_lock |= lock_exists || error.kind() == io::ErrorKind::AlreadyExists;
                    if lock_exists && stale_lock(path) {
                        match fs::remove_file(path) {
                            Ok(()) => continue,
                            Err(remove_error) if remove_error.kind() == io::ErrorKind::NotFound => {
                                continue;
                            }
                            Err(_) => {}
                        }
                    }
                    if started_at.elapsed() >= timeout {
                        if !observed_lock && !path.exists() {
                            return Err(error);
                        }
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            format!("file lock acquisition timed out: {}", path.display()),
                        ));
                    }
                    thread::sleep(delay);
                    delay = delay.saturating_mul(2).min(MAX_DELAY);
                }
                Err(error) => return Err(error),
            }
        }
    }
}

impl Drop for ExclusiveFileLock {
    fn drop(&mut self) {
        let owned = fs::read(&self.path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<LockOwner>(&bytes).ok())
            .is_some_and(|owner| owner.token == self.token);
        if owned {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{atomic_write, ExclusiveFileLock, LockOwner};
    use std::fs;
    use std::path::PathBuf;
    use std::process;
    use std::thread;
    use std::time::Duration;

    fn test_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!("skkima-atomic-{label}-{}", process::id()))
    }

    #[test]
    fn concurrent_atomic_writes_leave_one_complete_file() {
        let root = test_root("write");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let target = root.join("registry.json");
        let payloads: Vec<String> = (0..8)
            .map(|index| format!(r#"{{"writer":{index},"complete":true}}"#))
            .collect();
        let handles: Vec<_> = payloads
            .iter()
            .cloned()
            .map(|payload| {
                let target = target.clone();
                thread::spawn(move || atomic_write(&target, payload.as_bytes()))
            })
            .collect();
        for handle in handles {
            handle.join().unwrap().unwrap();
        }
        assert!(payloads.contains(&fs::read_to_string(&target).unwrap()));
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn live_lock_blocks_a_second_owner() {
        let root = test_root("contention");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let path = root.join("registry.lock");
        let lock = ExclusiveFileLock::acquire(&path).unwrap();
        let error = ExclusiveFileLock::acquire_with_timeout(&path, Duration::from_millis(50))
            .err()
            .expect("second owner should time out");
        assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
        drop(lock);
        assert!(!path.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn abandoned_lock_is_recovered() {
        let root = test_root("recovery");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let path = root.join("registry.lock");
        fs::write(
            &path,
            serde_json::to_vec(&LockOwner {
                process_id: u32::MAX,
                created_at_ms: 0,
                token: "abandoned".to_owned(),
            })
            .unwrap(),
        )
        .unwrap();
        let lock = ExclusiveFileLock::acquire(&path).unwrap();
        drop(lock);
        assert!(!path.exists());
        fs::remove_dir_all(root).unwrap();
    }
}
