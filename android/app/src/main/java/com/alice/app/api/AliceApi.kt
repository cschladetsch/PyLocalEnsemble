package com.alice.app.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object AliceApi {

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun streamChat(
        message: String,
        serverUrl: String,
        onDelta: (String) -> Unit,
        onDone: (reply: String, autoImage: Boolean) -> Unit
    ) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("message", message).toString()
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("$serverUrl/chat")
            .post(body)
            .build()

        client.newCall(request).execute().use { response ->
            val source = response.body?.source() ?: return@withContext
            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("data:")) continue
                val payload = line.removePrefix("data:").trim()
                if (payload.isEmpty()) continue
                val json = JSONObject(payload)
                when {
                    json.optBoolean("done", false) -> {
                        val reply = json.optString("reply", "")
                        val autoImage = json.optBoolean("auto_image", false)
                        withContext(Dispatchers.Main) { onDone(reply, autoImage) }
                        break
                    }
                    json.has("delta") -> {
                        val delta = json.getString("delta")
                        withContext(Dispatchers.Main) { onDelta(delta) }
                    }
                }
            }
        }
    }

    suspend fun streamTts(
        text: String,
        serverUrl: String,
        onChunk: (String) -> Unit
    ) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("text", text).toString()
            .toRequestBody(jsonMediaType)
        val request = Request.Builder()
            .url("$serverUrl/tts/stream")
            .post(body)
            .build()

        client.newCall(request).execute().use { response ->
            val source = response.body?.source() ?: return@withContext
            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("data:")) continue
                val payload = line.removePrefix("data:").trim()
                if (payload.isEmpty()) continue
                val json = JSONObject(payload)
                if (json.has("chunk")) {
                    val chunk = json.getString("chunk")
                    withContext(Dispatchers.Main) { onChunk(chunk) }
                }
            }
        }
    }

    suspend fun generateImage(extra: String, serverUrl: String): String =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("extra", extra).toString()
                .toRequestBody(jsonMediaType)
            val request = Request.Builder()
                .url("$serverUrl/image")
                .post(body)
                .build()

            client.newCall(request).execute().use { response ->
                val json = JSONObject(response.body?.string() ?: "{}")
                json.optString("url", "")
            }
        }

    suspend fun switchPersona(name: String, serverUrl: String) =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$serverUrl/persona/$name")
                .post("".toRequestBody(null))
                .build()
            client.newCall(request).execute().use { /* consume and close */ }
        }

    suspend fun clearHistory(serverUrl: String) =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$serverUrl/history")
                .delete()
                .build()
            client.newCall(request).execute().use { /* consume and close */ }
        }

    suspend fun getInfo(serverUrl: String): Map<String, Any> =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$serverUrl/info")
                .get()
                .build()

            client.newCall(request).execute().use { response ->
                val json = JSONObject(response.body?.string() ?: "{}")
                val map = mutableMapOf<String, Any>()
                for (key in json.keys()) {
                    map[key] = json.get(key)
                }
                map
            }
        }
}
