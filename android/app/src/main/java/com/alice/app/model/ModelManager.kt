package com.alice.app.model

import android.content.Context
import java.io.File

/**
 * Checks that all required on-device model files are present in external storage.
 *
 * Expected directory layout under `getExternalFilesDir("models")`:
 *
 * ```
 * models/
 *   llm/
 *     model.gguf                 ← GGUF quantised LLM (e.g. Llama-3 8B Q4_K_M)
 *   tts/
 *     kokoro.onnx                ← Kokoro TTS ONNX model
 *     voices.bin                 ← Voice embedding table (custom binary, see TtsEngine)
 *   stt/
 *     encoder-epoch-99-avg-1.onnx
 *     decoder-epoch-99-avg-1.onnx
 *     joiner-epoch-99-avg-1.onnx
 *     tokens.txt
 *   image/
 *     sd_qnn.bin                 ← QNN compiled context binary for Stable Diffusion
 * ```
 */
object ModelManager {

    // ── Expected relative paths ────────────────────────────────────────────────

    const val LLM_MODEL        = "llm/model.gguf"
    const val TTS_MODEL        = "tts/kokoro.onnx"
    const val TTS_VOICES       = "tts/voices.bin"
    const val STT_ENCODER      = "stt/encoder-epoch-99-avg-1.onnx"
    const val STT_DECODER      = "stt/decoder-epoch-99-avg-1.onnx"
    const val STT_JOINER       = "stt/joiner-epoch-99-avg-1.onnx"
    const val STT_TOKENS       = "stt/tokens.txt"
    const val IMAGE_QNN_BIN    = "image/sd_qnn.bin"

    // ── Public API ─────────────────────────────────────────────────────────────

    /** Returns the root models directory in external storage. */
    fun modelsDir(context: Context): File = context.getExternalFilesDir("models")!!

    /**
     * Check presence of all required model files.
     *
     * This does not verify file integrity — it only confirms that each file exists
     * and is non-empty. Call from a background coroutine (IO dispatcher).
     */
    fun check(context: Context): ModelStatus {
        val base = modelsDir(context)

        val llmReady = base.resolve(LLM_MODEL).isNonEmpty()

        val ttsReady = base.resolve(TTS_MODEL).isNonEmpty() &&
                base.resolve(TTS_VOICES).isNonEmpty()

        val sttReady = base.resolve(STT_ENCODER).isNonEmpty() &&
                base.resolve(STT_DECODER).isNonEmpty() &&
                base.resolve(STT_JOINER).isNonEmpty() &&
                base.resolve(STT_TOKENS).isNonEmpty()

        val imageReady = base.resolve(IMAGE_QNN_BIN).isNonEmpty()

        return ModelStatus(
            llmReady   = llmReady,
            ttsReady   = ttsReady,
            sttReady   = sttReady,
            imageReady = imageReady,
        )
    }

    /** Convenience: absolute path for a model file given its relative constant. */
    fun path(context: Context, relativePath: String): String =
        modelsDir(context).resolve(relativePath).absolutePath

    // ── Private helpers ────────────────────────────────────────────────────────

    private fun File.isNonEmpty(): Boolean = exists() && length() > 0L
}

/** Snapshot of which model families are ready to use. */
data class ModelStatus(
    val llmReady:   Boolean,
    val ttsReady:   Boolean,
    val sttReady:   Boolean,
    val imageReady: Boolean,
) {
    val allReady: Boolean get() = llmReady && ttsReady && sttReady && imageReady
}
