use jni::{
    objects::{JClass, JObject, JString},
    sys::{jfloat, jint},
    JNIEnv,
};
use parking_lot::Mutex;

use alice_core::{
    llm::engine::{LlmEngine, Message},
    tts::engine::TtsEngine,
};

// ---------------------------------------------------------------------------
// Global engine state
// ---------------------------------------------------------------------------

static LLM: Mutex<Option<LlmEngine>> = Mutex::new(None);
static TTS: Mutex<Option<TtsEngine>> = Mutex::new(None);

// ---------------------------------------------------------------------------
// LLM
// ---------------------------------------------------------------------------

/// Load a GGUF model.
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "system" fn Java_com_alice_app_AliceCore_llmLoad(
    mut env: JNIEnv,
    _class: JClass,
    model_path: JString,
    n_ctx: jint,
    n_gpu_layers: jint,
) -> jint {
    let path: String = match env.get_string(&model_path) {
        Ok(s) => s.into(),
        Err(e) => {
            log::error!("llmLoad: failed to read model_path: {e}");
            return -1;
        }
    };

    match LlmEngine::load(&path, n_ctx as u32, n_gpu_layers as u32) {
        Ok(engine) => {
            *LLM.lock() = Some(engine);
            log::info!("llmLoad: loaded model from {path}");
            0
        }
        Err(e) => {
            log::error!("llmLoad: {e}");
            -1
        }
    }
}

/// Release the loaded LLM.
#[no_mangle]
pub extern "system" fn Java_com_alice_app_AliceCore_llmUnload(
    _env: JNIEnv,
    _class: JClass,
) {
    *LLM.lock() = None;
    log::info!("llmUnload: engine released");
}

/// Run a chat inference.
///
/// `messages_json` is a JSON array of objects with `"role"` and `"content"` string fields.
/// Parsed without serde — minimal hand-rolled parser sufficient for this shape.
///
/// `callback` must implement:
///   `onToken(String)`, `onDone(String)`, `onError(String)`.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "system" fn Java_com_alice_app_AliceCore_llmChat(
    mut env: JNIEnv,
    _class: JClass,
    messages_json: JString,
    system_prompt: JString,
    callback: JObject,
) -> jint {
    let json: String = match env.get_string(&messages_json) {
        Ok(s) => s.into(),
        Err(e) => {
            call_on_error(&mut env, &callback, &format!("failed to read messages_json: {e}"));
            return -1;
        }
    };

    let sys: String = match env.get_string(&system_prompt) {
        Ok(s) => s.into(),
        Err(e) => {
            call_on_error(&mut env, &callback, &format!("failed to read system_prompt: {e}"));
            return -1;
        }
    };

    let messages = match parse_messages_json(&json) {
        Ok(m) => m,
        Err(e) => {
            call_on_error(&mut env, &callback, &format!("JSON parse error: {e}"));
            return -1;
        }
    };

    let mut guard = LLM.lock();
    let engine = match guard.as_mut() {
        Some(e) => e,
        None => {
            call_on_error(&mut env, &callback, "LLM not loaded");
            return -1;
        }
    };

    // We need the JNIEnv inside the closure but it is not Send.
    // Because we hold the Mutex and this function runs on the JNI thread,
    // using a raw pointer is safe here — the closure never outlives this frame.
    let env_ptr = &mut env as *mut JNIEnv;
    let cb_ptr = &callback as *const JObject;

    let result = engine.chat(&messages, &sys, |token: &str| {
        // SAFETY: closure is called synchronously on this thread.
        let e = unsafe { &mut *env_ptr };
        let cb = unsafe { &*cb_ptr };
        call_on_token(e, cb, token);
    });

    match result {
        Ok(full) => {
            call_on_done(&mut env, &callback, &full);
            0
        }
        Err(e) => {
            call_on_error(&mut env, &callback, &e.to_string());
            -1
        }
    }
}

// ---------------------------------------------------------------------------
// TTS
// ---------------------------------------------------------------------------

/// Load a Kokoro ONNX model. `voices_path` is reserved for future use.
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "system" fn Java_com_alice_app_AliceCore_ttsLoad(
    mut env: JNIEnv,
    _class: JClass,
    model_path: JString,
    _voices_path: JString,
) -> jint {
    let path: String = match env.get_string(&model_path) {
        Ok(s) => s.into(),
        Err(e) => {
            log::error!("ttsLoad: failed to read model_path: {e}");
            return -1;
        }
    };

    match TtsEngine::load(&path) {
        Ok(engine) => {
            *TTS.lock() = Some(engine);
            log::info!("ttsLoad: loaded model from {path}");
            0
        }
        Err(e) => {
            log::error!("ttsLoad: {e}");
            -1
        }
    }
}

/// Synthesize `text` sentence-by-sentence.
///
/// `voice` is a comma-separated list of f32 values forming the style embedding.
///
/// `callback` must implement:
///   `onPcmChunk(byte[])`, `onDone()`.
#[no_mangle]
pub extern "system" fn Java_com_alice_app_AliceCore_ttsSynthesizeStream(
    mut env: JNIEnv,
    _class: JClass,
    text: JString,
    voice: JString,
    speed: jfloat,
    callback: JObject,
) {
    let text_str: String = match env.get_string(&text) {
        Ok(s) => s.into(),
        Err(e) => {
            log::error!("ttsSynthesizeStream: failed to read text: {e}");
            return;
        }
    };

    let voice_str: String = match env.get_string(&voice) {
        Ok(s) => s.into(),
        Err(e) => {
            log::error!("ttsSynthesizeStream: failed to read voice: {e}");
            return;
        }
    };

    let voice_embedding: Vec<f32> = voice_str
        .split(',')
        .filter_map(|s| s.trim().parse::<f32>().ok())
        .collect();

    let sentences = alice_core::tts::chunker::split_sentences(&text_str);

    let guard = TTS.lock();
    let engine = match guard.as_ref() {
        Some(e) => e,
        None => {
            log::error!("ttsSynthesizeStream: TTS not loaded");
            return;
        }
    };

    for sentence in &sentences {
        match engine.synthesize(sentence, &voice_embedding, speed) {
            Ok(pcm) => {
                // Convert Vec<i16> → byte array (little-endian)
                let bytes: Vec<u8> = pcm
                    .iter()
                    .flat_map(|s| s.to_le_bytes())
                    .collect();

                match env.byte_array_from_slice(&bytes) {
                    Ok(arr) => {
                        let _ = env.call_method(
                            &callback,
                            "onPcmChunk",
                            "([B)V",
                            &[(&arr).into()],
                        );
                    }
                    Err(e) => {
                        log::error!("ttsSynthesizeStream: failed to create byte array: {e}");
                    }
                }
            }
            Err(e) => {
                log::error!("ttsSynthesizeStream: synthesis error: {e}");
            }
        }
    }

    let _ = env.call_method(&callback, "onDone", "()V", &[]);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn call_on_token(env: &mut JNIEnv, callback: &JObject, token: &str) {
    match env.new_string(token) {
        Ok(s) => {
            let _ = env.call_method(callback, "onToken", "(Ljava/lang/String;)V", &[(&s).into()]);
        }
        Err(e) => log::warn!("call_on_token: failed to create JString: {e}"),
    }
}

fn call_on_done(env: &mut JNIEnv, callback: &JObject, full_text: &str) {
    match env.new_string(full_text) {
        Ok(s) => {
            let _ = env.call_method(callback, "onDone", "(Ljava/lang/String;)V", &[(&s).into()]);
        }
        Err(e) => log::warn!("call_on_done: failed to create JString: {e}"),
    }
}

fn call_on_error(env: &mut JNIEnv, callback: &JObject, msg: &str) {
    log::error!("AliceCore JNI error: {msg}");
    match env.new_string(msg) {
        Ok(s) => {
            let _ =
                env.call_method(callback, "onError", "(Ljava/lang/String;)V", &[(&s).into()]);
        }
        Err(e) => log::warn!("call_on_error: failed to create JString: {e}"),
    }
}

// ---------------------------------------------------------------------------
// Minimal JSON parser for [{role, content}, ...]
// ---------------------------------------------------------------------------

fn parse_messages_json(json: &str) -> Result<Vec<Message>, String> {
    let json = json.trim();
    if !json.starts_with('[') || !json.ends_with(']') {
        return Err("expected a JSON array".into());
    }

    let inner = &json[1..json.len() - 1];
    let mut messages = Vec::new();

    // Split objects naively: find balanced { } blocks
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

/// Extract the value of a JSON string field by name.
/// Handles basic `"field": "value"` patterns; does not handle escaped quotes
/// inside values (sufficient for message role/content in typical use).
fn extract_string_field(obj: &str, field: &str) -> Option<String> {
    let key = format!("\"{}\"", field);
    let pos = obj.find(&key)?;
    let after_key = &obj[pos + key.len()..];
    // skip whitespace and colon
    let after_colon = after_key.trim_start().strip_prefix(':')?.trim_start();
    if !after_colon.starts_with('"') {
        return None;
    }
    let value_start = &after_colon[1..];
    // find closing quote (ignoring escaped quotes)
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
