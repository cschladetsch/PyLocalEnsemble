// STT is handled by sherpa-onnx JNI on Android and by faster-whisper on server;
// this module reserved for future shared impl.

pub struct SttEngine;

impl SttEngine {
    pub fn new() -> Self {
        Self
    }

    pub fn transcribe(&self, _pcm: &[i16]) -> String {
        String::new()
    }
}

impl Default for SttEngine {
    fn default() -> Self {
        Self::new()
    }
}
