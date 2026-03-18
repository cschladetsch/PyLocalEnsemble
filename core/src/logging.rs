use std::{env, fs, path::PathBuf, sync::Once};

static INIT: Once = Once::new();

pub fn init(component: &str) {
    INIT.call_once(|| {
        let Some(log_dir) = env::var_os("ALICE_LOG_DIR") else {
            return;
        };

        let mut path = PathBuf::from(log_dir);
        if fs::create_dir_all(&path).is_err() {
            return;
        }
        path.push(format!("{}.log", component.replace(['/', '\\'], "-")));

        let level = match env::var("ALICE_LOG_LEVEL")
            .unwrap_or_else(|_| "INFO".to_string())
            .to_uppercase()
            .as_str()
        {
            "TRACE" => log::LevelFilter::Trace,
            "DEBUG" => log::LevelFilter::Debug,
            "WARN" => log::LevelFilter::Warn,
            "ERROR" => log::LevelFilter::Error,
            _ => log::LevelFilter::Info,
        };

        let dispatch = fern::Dispatch::new()
            .level(level)
            .format(|out, message, record| {
                out.finish(format_args!(
                    "{} {} [{}] {}",
                    chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                    record.level(),
                    record.target(),
                    message
                ))
            })
            .chain(match fern::log_file(path) {
                Ok(file) => file,
                Err(_) => return,
            });

        let _ = dispatch.apply();
    });
}
