// Gradle module for the Rust Android binding.
// No Android plugin — this module only drives the Cargo build and copies the .so.

tasks.register<Exec>("cargoBuildRelease") {
    description = "Build alice-core-android for aarch64-linux-android via cargo-ndk"
    group = "build"

    workingDir = projectDir
    commandLine(
        "cargo", "ndk",
        "--target", "aarch64-linux-android",
        "--platform", "26",
        "--",
        "build", "--release",
    )

    doLast {
        val src = file("target/aarch64-linux-android/release/libalice_core_android.so")
        val dst = file("${rootDir}/android/app/src/main/jniLibs/arm64-v8a/libalice_core.so")
        dst.parentFile.mkdirs()
        src.copyTo(dst, overwrite = true)
        logger.lifecycle("Copied $src → $dst")
    }
}
