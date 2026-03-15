package com.alice.app.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alice.app.AliceCore
import com.alice.app.audio.TtsPlayer
import com.alice.app.inference.ImageEngine
import com.alice.app.inference.LlmEngine
import com.alice.app.inference.Message
import com.alice.app.inference.TtsEngine
import com.alice.app.model.ModelManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class ChatViewModel : ViewModel() {

    private val llmEngine   = LlmEngine()
    private val ttsEngine   = TtsEngine()
    private val imageEngine = ImageEngine()
    private val ttsPlayer   = TtsPlayer()

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    private val _isThinking = MutableStateFlow(false)
    val isThinking: StateFlow<Boolean> = _isThinking.asStateFlow()

    private val _currentImage = MutableStateFlow<android.graphics.Bitmap?>(null)
    val currentImage: StateFlow<android.graphics.Bitmap?> = _currentImage.asStateFlow()

    // Kept for UI compatibility — null in offline mode (no remote URL).
    val currentImageUrl: StateFlow<String?> = MutableStateFlow<String?>(null).asStateFlow()

    // ── Initialisation ────────────────────────────────────────────────────────

    // NOTE: Context is needed for ModelManager.path(); pass an ApplicationContext
    // via a factory (AndroidViewModel) in production. The paths below use placeholders
    // that assume an AndroidViewModel subclass supplies `getApplication<Application>()`.
    //
    // For now, model paths are constants relative to a known external storage layout.
    // Replace `"/sdcard/Android/data/com.alice.app/files/models"` with
    // `ModelManager.modelsDir(application).absolutePath` when refactoring to
    // AndroidViewModel.
    private val modelsRoot = "/sdcard/Android/data/com.alice.app/files/models"

    init {
        viewModelScope.launch(Dispatchers.IO) {
            val llmResult = AliceCore.llmLoad(
                modelPath   = "$modelsRoot/${ModelManager.LLM_MODEL}",
                nCtx        = 4096,
                nGpuLayers  = 0,   // 0 = CPU only; increase if device supports GPU layers
            )
            if (llmResult != 0) {
                android.util.Log.e("ChatViewModel", "llmLoad failed (code $llmResult)")
            }

            val ttsResult = AliceCore.ttsLoad(
                modelPath  = "$modelsRoot/${ModelManager.TTS_MODEL}",
                voicesPath = "$modelsRoot/${ModelManager.TTS_VOICES}",
            )
            if (ttsResult != 0) {
                android.util.Log.e("ChatViewModel", "ttsLoad failed (code $ttsResult)")
            }
        }
    }

    // ── Public API ────────────────────────────────────────────────────────────

    fun sendMessage(text: String) {
        if (text.isBlank() || _isThinking.value) return

        _messages.update { it + Message(role = "user", content = text) }
        _isThinking.value = true

        // Add a placeholder assistant message that grows as token deltas arrive.
        _messages.update { it + Message(role = "assistant", content = "") }

        viewModelScope.launch {
            try {
                val history = _messages.value
                    .dropLast(1)          // exclude the empty placeholder
                    .takeLast(20)         // keep a reasonable context window

                var fullReply = ""

                llmEngine.streamChat(
                    messages     = history,
                    systemPrompt = "You are Alice, a helpful and friendly AI assistant.",
                )
                .flowOn(Dispatchers.Default)
                .collect { delta ->
                    fullReply += delta
                    _messages.update { list ->
                        val last = list.lastOrNull()
                        if (last?.role == "assistant") {
                            list.dropLast(1) + last.copy(content = last.content + delta)
                        } else {
                            list
                        }
                    }
                }

                // Finalize with the complete reply.
                _messages.update { list ->
                    val last = list.lastOrNull()
                    if (last?.role == "assistant") {
                        list.dropLast(1) + last.copy(content = fullReply)
                    } else {
                        list
                    }
                }
                _isThinking.value = false

                // Fire-and-forget: TTS and image generation are non-critical.
                playTts(fullReply)
                generateImage(fullReply)

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

    fun clearHistory() {
        _messages.value = emptyList()
        _currentImage.value = null
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun playTts(text: String) {
        ttsPlayer.clear()
        ttsPlayer.start()
        viewModelScope.launch(Dispatchers.IO) {
            try {
                ttsEngine.synthesizeStream(text).collect { pcmChunk ->
                    ttsPlayer.enqueuePcm(pcmChunk)
                }
            } catch (_: Exception) { /* TTS is non-critical */ }
        }
    }

    private fun generateImage(context: String) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val bitmap = imageEngine.generateImage(prompt = context)
                if (bitmap != null) {
                    _currentImage.value = bitmap
                }
            } catch (_: Exception) { /* image is non-critical */ }
        }
    }

    override fun onCleared() {
        super.onCleared()
        ttsPlayer.clear()
        AliceCore.llmUnload()
    }
}
