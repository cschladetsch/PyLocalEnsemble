package com.alice.app.inference

import android.Manifest
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.annotation.RequiresPermission
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Speech-to-text engine backed by sherpa-onnx streaming recognition.
 *
 * NOTE: The sherpa-onnx AAR (`sherpa-onnx-android-*.aar`) must be placed in
 * `app/libs/` and referenced via the fileTree dependency in app/build.gradle.kts.
 * The AAR bundles its own JNI `.so` libraries for arm64-v8a.
 *
 * Model files (e.g. a streaming Zipformer or Paraformer) should be placed in
 * `context.getExternalFilesDir("models")/stt/` and paths passed to the config below.
 *
 * For offline/Whisper-based recognition swap [OnlineRecognizer] / [OnlineStream]
 * for [com.k2fsa.sherpa.onnx.OfflineRecognizer] / [OfflineStream] and adjust the
 * config accordingly.
 */
class SttEngine(private val context: Context) {

    private var recognizer: OnlineRecognizer? = null
    private var stream: OnlineStream? = null
    private var audioRecord: AudioRecord? = null
    private var listenJob: Job? = null

    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    private val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        .coerceAtLeast(4096)

    /** Initialise the sherpa-onnx recognizer. Call once before [startListening]. */
    fun init(modelDir: String) {
        // Adjust these paths / config fields to match the model you downloaded.
        val config = OnlineRecognizerConfig().apply {
            // Example: streaming Zipformer-RNN-T model layout
            featConfig.sampleRate = sampleRate
            featConfig.featureDim = 80
            modelConfig.transducer.encoder = "$modelDir/encoder-epoch-99-avg-1.onnx"
            modelConfig.transducer.decoder = "$modelDir/decoder-epoch-99-avg-1.onnx"
            modelConfig.transducer.joiner  = "$modelDir/joiner-epoch-99-avg-1.onnx"
            modelConfig.tokens = "$modelDir/tokens.txt"
            modelConfig.numThreads = 2
            modelConfig.provider = "cpu"
            decodingMethod = "greedy_search"
        }
        recognizer = OnlineRecognizer(config)
    }

    /**
     * Start streaming microphone audio to the recognizer.
     *
     * [onResult] is called on each finalized recognition result (partial results
     * are not surfaced; extend as needed).
     *
     * Requires [Manifest.permission.RECORD_AUDIO].
     */
    @RequiresPermission(Manifest.permission.RECORD_AUDIO)
    fun startListening(onResult: (String) -> Unit) {
        if (listenJob?.isActive == true) return

        val rec = recognizer ?: error("SttEngine.init() must be called before startListening()")
        stream = rec.createStream()

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize,
        )
        audioRecord?.startRecording()

        listenJob = CoroutineScope(Dispatchers.IO).launch {
            val buf = ShortArray(bufferSize / 2)
            while (isActive) {
                val read = audioRecord?.read(buf, 0, buf.size) ?: break
                if (read <= 0) continue

                val floats = FloatArray(read) { buf[it] / 32768f }
                stream?.acceptWaveform(floats, sampleRate)

                while (rec.isReady(stream)) {
                    rec.decode(stream)
                }

                val result = rec.getResult(stream)
                if (result.text.isNotBlank()) {
                    onResult(result.text)
                    // Reset stream to avoid growing context indefinitely.
                    stream?.release()
                    stream = rec.createStream()
                }
            }
        }
    }

    /** Stop recording and release resources. */
    fun stopListening() {
        listenJob?.cancel()
        listenJob = null
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        stream?.release()
        stream = null
    }

    /** Release all native resources. Call from ViewModel.onCleared(). */
    fun release() {
        stopListening()
        recognizer?.release()
        recognizer = null
    }
}
