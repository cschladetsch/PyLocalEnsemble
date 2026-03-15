package com.alice.app.inference

import com.alice.app.AliceCore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn

data class Message(val role: String, val content: String)

/**
 * ViewModel-facing LLM engine.
 *
 * Serialises the message list to a JSON string without any Gson/Moshi dependency,
 * delegates to [AliceCore.llmChat], and exposes streaming tokens as a [Flow].
 */
class LlmEngine {

    /**
     * Stream a chat completion as a [Flow] of token deltas.
     *
     * The flow runs on [Dispatchers.Default] (off the main thread) and completes
     * when the model emits [AliceCore.LlmCallback.onDone] or throws on
     * [AliceCore.LlmCallback.onError].
     */
    fun streamChat(messages: List<Message>, systemPrompt: String): Flow<String> =
        callbackFlow {
            val callback = object : AliceCore.LlmCallback {
                override fun onToken(delta: String) {
                    trySend(delta)
                }

                override fun onDone(fullReply: String) {
                    close()
                }

                override fun onError(message: String) {
                    close(RuntimeException("LLM error: $message"))
                }
            }

            val json = messagesToJson(messages)
            AliceCore.llmChat(json, systemPrompt, callback)

            // awaitClose is required by callbackFlow; the channel is already closed
            // by the time we reach here via onDone/onError, so this is a no-op sentinel.
            awaitClose { }
        }.flowOn(Dispatchers.Default)

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Manually build a JSON array string from [messages].
     * Example output: [{"role":"user","content":"Hello!"}]
     *
     * Escapes `"` and `\` in content values so the JSON is well-formed.
     */
    private fun messagesToJson(messages: List<Message>): String {
        val sb = StringBuilder("[")
        messages.forEachIndexed { index, msg ->
            sb.append("{\"role\":")
            sb.appendJsonString(msg.role)
            sb.append(",\"content\":")
            sb.appendJsonString(msg.content)
            sb.append("}")
            if (index < messages.lastIndex) sb.append(",")
        }
        sb.append("]")
        return sb.toString()
    }

    private fun StringBuilder.appendJsonString(value: String) {
        append('"')
        for (ch in value) {
            when (ch) {
                '"'  -> append("\\\"")
                '\\' -> append("\\\\")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> append(ch)
            }
        }
        append('"')
    }
}
