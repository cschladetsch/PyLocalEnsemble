package com.alice.app.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class TtsPlayer {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val chunkChannel = Channel<ByteArray>(Channel.UNLIMITED)
    private var playbackJob: Job? = null
    private var audioTrack: AudioTrack? = null

    // Kokoro ONNX outputs signed-16 LE PCM at 24 000 Hz, mono.
    private val sampleRate    = 24000
    private val channelConfig = AudioFormat.CHANNEL_OUT_MONO
    private val encoding      = AudioFormat.ENCODING_PCM_16BIT

    fun start() {
        val minBufSize = AudioTrack.getMinBufferSize(sampleRate, channelConfig, encoding)
        val trackBufSize = maxOf(minBufSize, 8192)

        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(encoding)
                    .setChannelMask(channelConfig)
                    .build()
            )
            .setBufferSizeInBytes(trackBufSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack?.play()

        playbackJob = scope.launch {
            for (pcmData in chunkChannel) {
                if (!isActive) break
                audioTrack?.write(pcmData, 0, pcmData.size)
            }
        }
    }

    /**
     * Enqueue a raw PCM chunk for playback.
     *
     * [pcm] must be signed 16-bit little-endian samples at 24 000 Hz (the native
     * output format of the Kokoro ONNX TTS model running in the Rust core).
     * No header stripping or base64 decoding is required.
     */
    fun enqueuePcm(pcm: ByteArray) {
        if (pcm.isNotEmpty()) {
            chunkChannel.trySend(pcm)
        }
    }

    fun stop() {
        playbackJob?.cancel()
        audioTrack?.stop()
        chunkChannel.close()
    }

    fun clear() {
        stop()
        audioTrack?.release()
        audioTrack = null
    }
}
