mod jni_bridge;

/// Called by the Android runtime when the shared library is first loaded.
/// Initialises android_logger so that Rust `log` macros appear in logcat.
#[no_mangle]
pub extern "C" fn JNI_OnLoad(
    vm: jni::JavaVM,
    _reserved: *mut std::ffi::c_void,
) -> jni::sys::jint {
    android_logger::init_once(
        android_logger::Config::default()
            .with_max_level(log::LevelFilter::Debug)
            .with_tag("AliceCore"),
    );

    log::info!("AliceCore native library loaded");

    let _ = vm; // kept alive by the JVM
    jni::sys::JNI_VERSION_1_6
}
