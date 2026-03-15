package com.alice.app.inference

import com.alice.app.AliceCore
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/**
 * ViewModel-facing TTS engine.
 *
 * Calls [AliceCore.ttsSynthesizeStream] and exposes each synthesized sentence's
 * raw PCM data as a [Flow] of [ByteArray] chunks.
 *
 * Audio format: signed 16-bit little-endian PCM, mono, 24 000 Hz (Kokoro output rate).
 */
class TtsEngine {

    /**
     * Synthesize [text] using [voice] at [speed] (1.0 = normal).
     *
     * Emits one [ByteArray] per sentence. The flow completes after
     * [AliceCore.TtsCallback.onDone] is called by native code.
     */
    fun synthesizeStream(
        text: String,
        voice: String = "af_bella",
        speed: Float = 1.0f,
    ): Flow<ByteArray> = callbackFlow {
        val callback = object : AliceCore.TtsCallback {
            override fun onPcmChunk(pcm: ByteArray) {
                trySend(pcm)
            }

            override fun onDone() {
                close()
            }
        }

        // ttsSynthesizeStream is synchronous on the calling thread; the flow was launched
        // on Dispatchers.IO by the ViewModel so this does not block the main thread.
        AliceCore.ttsSynthesizeStream(text, voice, speed, callback)

        awaitClose { }
    }
}
