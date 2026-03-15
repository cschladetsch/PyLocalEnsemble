package com.alice.app

object AliceCore {
    init { System.loadLibrary("alice_core") }

    external fun llmLoad(modelPath: String, nCtx: Int, nGpuLayers: Int): Int
    external fun llmUnload()
    external fun llmChat(messagesJson: String, systemPrompt: String, callback: LlmCallback): Int
    external fun ttsLoad(modelPath: String, voicesPath: String): Int
    external fun ttsSynthesizeStream(text: String, voice: String, speed: Float, callback: TtsCallback)

    interface LlmCallback {
        fun onToken(delta: String)
        fun onDone(fullReply: String)
        fun onError(message: String)
    }

    interface TtsCallback {
        fun onPcmChunk(pcm: ByteArray)
        fun onDone()
    }
}
