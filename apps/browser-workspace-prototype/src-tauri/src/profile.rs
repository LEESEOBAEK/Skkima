use std::env;
use std::path::PathBuf;

/// App-level data is namespaced by build profile so the portfolio build never
/// reuses the operating application's plugin, skill, or browser profile data.
#[cfg(feature = "portfolio")]
pub const APP_DATA_NAMESPACE: &str = "Skkima-Portfolio";

#[cfg(not(feature = "portfolio"))]
pub const APP_DATA_NAMESPACE: &str = "Skkima";

pub fn app_data_root() -> Result<PathBuf, String> {
    env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|root| root.join(APP_DATA_NAMESPACE))
        .ok_or_else(|| "Windows 사용자 앱 데이터 폴더를 확인할 수 없습니다.".to_owned())
}
