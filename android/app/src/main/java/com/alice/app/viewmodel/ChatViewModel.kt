package com.alice.app.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alice.app.api.AliceApi
import com.alice.app.audio.TtsPlayer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class Message(
    val role: String,   // "user" or "assistant"
    val content: String
)

class ChatViewModel : ViewModel() {

    val serverUrl: String = "http://10.0.2.2:8000"

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    private val _isThinking = MutableStateFlow(false)
    val isThinking: StateFlow<Boolean> = _isThinking.asStateFlow()

    private val _currentImageUrl = MutableStateFlow<String?>(null)
    val currentImageUrl: StateFlow<String?> = _currentImageUrl.asStateFlow()

    private val ttsPlayer = TtsPlayer()

    fun sendMessage(text: String) {
        if (text.isBlank() || _isThinking.value) return

        _messages.update { it + Message(role = "user", content = text) }
        _isThinking.value = true

        // Add a placeholder assistant message that grows as deltas arrive
        _messages.update { it + Message(role = "assistant", content = "") }

        viewModelScope.launch {
            try {
                AliceApi.streamChat(
                    message = text,
                    serverUrl = serverUrl,
                    onDelta = { delta ->
                        _messages.update { list ->
                            val last = list.lastOrNull()
                            if (last?.role == "assistant") {
                                list.dropLast(1) + last.copy(content = last.content + delta)
                            } else {
                                list
                            }
                        }
                    },
                    onDone = { reply, autoImage ->
                        // Finalise the assistant message with the complete reply
                        _messages.update { list ->
                            val last = list.lastOrNull()
                            if (last?.role == "assistant") {
                                list.dropLast(1) + last.copy(content = reply)
                            } else {
                                list
                            }
                        }
                        _isThinking.value = false

                        if (autoImage) {
                            fetchImage(reply)
                        }

                        playTts(reply)
                    }
                )
            } catch (e: Exception) {
                _messages.update { list ->
                    val last = list.lastOrNull()
                    if (last?.role == "assistant" && last.content.isEmpty()) {
                        list.dropLast(1) + last.copy(content = "[Error: ${e.message}]")
                    } else {
                        list
                    }
                }
                _isThinking.value = false
            }
        }
    }

    private fun fetchImage(context: String) {
        viewModelScope.launch {
            try {
                val url = AliceApi.generateImage(extra = context, serverUrl = serverUrl)
                if (url.isNotBlank()) {
                    _currentImageUrl.value = "$serverUrl$url"
                }
            } catch (_: Exception) { /* image is non-critical */ }
        }
    }

    private fun playTts(text: String) {
        ttsPlayer.clear()
        ttsPlayer.start()
        viewModelScope.launch {
            try {
                AliceApi.streamTts(
                    text = text,
                    serverUrl = serverUrl,
                    onChunk = { base64Chunk ->
                        ttsPlayer.enqueueChunk(base64Chunk)
                    }
                )
            } catch (_: Exception) { /* TTS is non-critical */ }
        }
    }

    fun clearHistory() {
        viewModelScope.launch {
            try {
                AliceApi.clearHistory(serverUrl)
                _messages.value = emptyList()
                _currentImageUrl.value = null
            } catch (_: Exception) { /* best-effort */ }
        }
    }

    override fun onCleared() {
        super.onCleared()
        ttsPlayer.clear()
    }
}
