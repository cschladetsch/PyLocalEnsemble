package com.alice.app.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.isActive
import java.nio.ByteBuffer
import java.nio.ByteOrder

class TtsPlayer {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val chunkChannel = Channel<ByteArray>(Channel.UNLIMITED)
    private var playbackJob: Job? = null
    private var audioTrack: AudioTrack? = null

    // WAV PCM parameters — must match backend output (16-bit, mono, 22050 Hz typical)
    private val sampleRate = 22050
    private val channelConfig = AudioFormat.CHANNEL_OUT_MONO
    private val encoding = AudioFormat.ENCODING_PCM_16BIT

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
     * Accepts a base64-encoded WAV chunk from the TTS SSE stream.
     * Strips the 44-byte WAV header before writing raw PCM to AudioTrack.
     */
    fun enqueueChunk(base64Chunk: String) {
        val bytes = Base64.decode(base64Chunk, Base64.DEFAULT)
        val pcm = stripWavHeader(bytes)
        if (pcm.isNotEmpty()) {
            chunkChannel.trySend(pcm)
        }
    }

    /**
     * Strips a standard 44-byte WAV header if present, returning raw PCM.
     * If the data doesn't start with "RIFF", it is assumed to already be raw PCM.
     */
    private fun stripWavHeader(data: ByteArray): ByteArray {
        if (data.size > 44) {
            val riff = String(data.copyOfRange(0, 4))
            if (riff == "RIFF") {
                val header = parseSampleRateFromWav(data)
                return data.copyOfRange(44, data.size)
            }
        }
        return data
    }

    private fun parseSampleRateFromWav(data: ByteArray): Int {
        // Sample rate is at bytes 24-27 in standard WAV header (little-endian)
        return ByteBuffer.wrap(data, 24, 4).order(ByteOrder.LITTLE_ENDIAN).int
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
