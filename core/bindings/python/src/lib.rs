use std::sync::Arc;

use parking_lot::Mutex;
use pyo3::prelude::*;

use alice_core::{
    llm::engine::{LlmEngine, Message},
    tts::engine::TtsEngine,
};

// ---------------------------------------------------------------------------
// PyLlmEngine
// ---------------------------------------------------------------------------

/// Python-facing wrapper around `alice_core::llm::engine::LlmEngine`.
#[pyclass]
struct PyLlmEngine {
    inner: Arc<Mutex<LlmEngine>>,
}

#[pymethods]
impl PyLlmEngine {
    /// Load a GGUF model.
    ///
    /// Args:
    ///     model_path: Path to the .gguf file.
    ///     n_ctx: Context window size (tokens).
    ///     n_gpu_layers: Number of layers to offload to GPU (0 = CPU only).
    #[new]
    fn new(model_path: &str, n_ctx: u32, n_gpu_layers: u32) -> PyResult<Self> {
        let engine = LlmEngine::load(model_path, n_ctx, n_gpu_layers)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self {
            inner: Arc::new(Mutex::new(engine)),
        })
    }

    /// Run a chat inference.
    ///
    /// Args:
    ///     messages_json_str: JSON array of `{"role": ..., "content": ...}` objects.
    ///     system_prompt: System prompt prepended to the conversation.
    ///     callback: Python callable invoked with each decoded token string.
    ///               Signature: `callback(delta: str) -> None`.
    ///
    /// Returns:
    ///     The full generated response as a string.
    fn chat(
        &self,
        messages_json_str: &str,
        system_prompt: &str,
        callback: PyObject,
    ) -> PyResult<String> {
        let messages = parse_messages_json(messages_json_str)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?;

        let mut guard = self.inner.lock();

        // The closure must call back into Python; we acquire the GIL per call.
        let result = guard
            .chat(&messages, system_prompt, |token: &str| {
                Python::with_gil(|py| {
                    let _ = callback.call1(py, (token,));
                });
            })
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        Ok(result)
    }
}

// ---------------------------------------------------------------------------
// PyTtsEngine
// ---------------------------------------------------------------------------

/// Python-facing wrapper around `alice_core::tts::engine::TtsEngine`.
#[pyclass]
struct PyTtsEngine {
    inner: TtsEngine,
}

#[pymethods]
impl PyTtsEngine {
    /// Load a Kokoro ONNX model.
    ///
    /// Args:
    ///     model_path: Path to the `.onnx` file.
    #[new]
    fn new(model_path: &str) -> PyResult<Self> {
        let engine = TtsEngine::load(model_path)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self { inner: engine })
    }

    /// Synthesize text to PCM audio.
    ///
    /// Args:
    ///     text: Input text to synthesize.
    ///     voice_embedding_list: List of f32 values representing the style embedding.
    ///     speed: Speed multiplier (1.0 = normal).
    ///
    /// Returns:
    ///     List of i16 PCM samples at 24 kHz mono.
    fn synthesize(
        &mut self,
        text: &str,
        voice_embedding_list: Vec<f32>,
        speed: f32,
    ) -> PyResult<Vec<i16>> {
        self.inner
            .synthesize(text, &voice_embedding_list, speed)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }
}

// ---------------------------------------------------------------------------
// Module
// ---------------------------------------------------------------------------

/// Native extension module `alice_core_py`.
#[pymodule]
fn alice_core_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    alice_core::logging::init("rust-python");
    log::info!("alice_core_py module initialized");
    m.add_class::<PyLlmEngine>()?;
    m.add_class::<PyTtsEngine>()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Minimal JSON parser (mirrors android binding, no serde dep)
// ---------------------------------------------------------------------------

fn parse_messages_json(json: &str) -> Result<Vec<Message>, String> {
    let json = json.trim();
    if !json.starts_with('[') || !json.ends_with(']') {
        return Err("expected a JSON array".into());
    }

    let inner = &json[1..json.len() - 1];
    let mut messages = Vec::new();

    let mut depth = 0i32;
    let mut start = None;
    for (i, ch) in inner.char_indices() {
        match ch {
            '{' => {
                if depth == 0 {
                    start = Some(i);
                }
                depth += 1;
            }
            '}' => {
                depth -= 1;
                if depth == 0 {
                    if let Some(s) = start {
                        let obj = &inner[s..=i];
                        messages.push(parse_message_object(obj)?);
                    }
                    start = None;
                }
            }
            _ => {}
        }
    }

    Ok(messages)
}

fn parse_message_object(obj: &str) -> Result<Message, String> {
    let role = extract_string_field(obj, "role")
        .ok_or_else(|| format!("missing 'role' in: {obj}"))?;
    let content = extract_string_field(obj, "content")
        .ok_or_else(|| format!("missing 'content' in: {obj}"))?;
    Ok(Message { role, content })
}

fn extract_string_field(obj: &str, field: &str) -> Option<String> {
    let key = format!("\"{}\"", field);
    let pos = obj.find(&key)?;
    let after_key = &obj[pos + key.len()..];
    let after_colon = after_key.trim_start().strip_prefix(':')?.trim_start();
    if !after_colon.starts_with('"') {
        return None;
    }
    let value_start = &after_colon[1..];
    let mut chars = value_start.char_indices();
    let mut prev_backslash = false;
    loop {
        let (i, ch) = chars.next()?;
        if ch == '"' && !prev_backslash {
            return Some(value_start[..i].replace("\\\"", "\"").replace("\\n", "\n"));
        }
        prev_backslash = ch == '\\' && !prev_backslash;
    }
}
