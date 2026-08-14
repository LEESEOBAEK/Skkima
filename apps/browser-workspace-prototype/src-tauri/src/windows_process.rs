use std::os::windows::process::CommandExt;
use std::process::Command;

const CREATE_NO_WINDOW: u32 = 0x08000000;

fn tasklist_row_pid(row: &str) -> Option<u32> {
    row.trim()
        .trim_start_matches('\u{feff}')
        .split("\",\"")
        .nth(1)?
        .trim_matches('"')
        .trim()
        .parse()
        .ok()
}

fn tasklist_has_exact_pid(output: &[u8], process_id: u32) -> bool {
    String::from_utf8_lossy(output)
        .lines()
        .filter_map(tasklist_row_pid)
        .any(|listed_pid| listed_pid == process_id)
}

pub(crate) fn process_is_running(process_id: u32) -> bool {
    let filter = format!("PID eq {process_id}");
    Command::new("tasklist.exe")
        .args(["/FI", &filter, "/FO", "CSV", "/NH"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .is_some_and(|output| tasklist_has_exact_pid(&output.stdout, process_id))
}

pub(crate) fn terminate_process_tree(process_id: u32) -> Result<(), String> {
    if process_id == 0 || process_id == std::process::id() {
        return Err("The process ID cannot be terminated.".to_owned());
    }

    let output = Command::new("taskkill.exe")
        .args(["/PID", &process_id.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map_err(|error| format!("Failed to start the process termination command: {error}"))?;
    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    Err(if stderr.is_empty() {
        format!(
            "The process termination command failed with code {:?}.",
            output.status.code()
        )
    } else {
        format!("Failed to terminate the process: {stderr}")
    })
}
#[cfg(test)]
mod tests {
    use super::{tasklist_has_exact_pid, tasklist_row_pid};

    #[test]
    fn parses_only_the_tasklist_pid_column() {
        assert_eq!(
            tasklist_row_pid(r#""powershell.exe","1234","Console","1","12,340 K""#),
            Some(1234)
        );
        assert_eq!(tasklist_row_pid("INFO: No tasks are running."), None);
    }

    #[test]
    fn tasklist_pid_matching_is_numeric_and_exact() {
        let output = br#""powershell.exe","12345","Console","1","1,234 K""#;
        assert!(tasklist_has_exact_pid(output, 12345));
        assert!(!tasklist_has_exact_pid(output, 1234));
        assert!(!tasklist_has_exact_pid(output, 234));
    }
}
