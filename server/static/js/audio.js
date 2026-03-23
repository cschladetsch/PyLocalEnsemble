// ── Audio & TTS & STT ────────────────────────────────────────────────────────
let _audioCtx = null, _nextStart = 0, _ttsNodes = [], _ttsGen = 0;
let _lastChunks = [];   // decoded AudioBuffers from last speak() for instant resay

function _stopTts() {
  _ttsGen++;
  _ttsNodes.forEach(n => { try { n.stop(0); } catch {} });
  _ttsNodes = [];
  _nextStart = 0;
  const skip = document.getElementById('skip-btn');
  if (skip) skip.disabled = true;
}

function skipVoice() {
  _demoSkip = true;
  _stopTts();
  if (chatAbort) { chatAbort.abort(); chatAbort = null; }
  if (imgAbort)  { imgAbort.abort();  imgAbort  = null; }
  fetch('/interrupt', { method: 'POST' }).catch(() => {});
}

function _ensureAudioCtx() {
  if (!_audioCtx || _audioCtx.state === 'closed') {
    _audioCtx = new AudioContext();
    _nextStart = 0;
  }
}

function _playAudioBuffer(audioBuf) {
  const src = _audioCtx.createBufferSource();
  src.buffer = audioBuf;
  src.connect(_audioCtx.destination);
  const now   = _audioCtx.currentTime;
  const start = Math.max(now + 0.02, _nextStart);
  src.start(start);
  _nextStart = start + audioBuf.duration;
  _ttsNodes.push(src);
  const skip = document.getElementById('skip-btn');
  if (skip) skip.disabled = false;
  src.onended = () => {
    if (_audioCtx && _audioCtx.currentTime >= _nextStart - 0.1) {
      if (skip) skip.disabled = true;
    }
  };
}

async function _scheduleChunk(b64wav, gen) {
  _ensureAudioCtx();
  const bytes = atob(b64wav);
  const buf = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
  try {
    const audioBuf = await _audioCtx.decodeAudioData(buf.buffer);
    if (gen !== _ttsGen) return;
    _lastChunks.push(audioBuf);
    _playAudioBuffer(audioBuf);
  } catch (e) {
    console.error('Audio decode failed:', e);
  }
}

function resay() {
  if (!_lastChunks.length && !lastReplyText) return;
  if (!_lastChunks.length) { speak(lastReplyText); return; }
  _stopTts();
  const gen = _ttsGen;
  _ensureAudioCtx();
  for (const audioBuf of _lastChunks) {
    if (gen !== _ttsGen) return;
    _playAudioBuffer(audioBuf);
  }
}

function toggleMute() {
  muted = !muted;
  if (muted) { _stopTts(); _stopGroupSpeech(); }
  document.getElementById('mute-btn').textContent = muted ? 'Unmute' : 'Mute';
}

async function speak(text, voice = null, speed = null, pitch = null, effects = null) {
  if (muted) return;
  _stopTts();
  _lastChunks = [];
  lastReplyText = text;
  const gen = _ttsGen;
  try {
    const body = { text };
    if (voice   !== null) body.voice   = voice;
    if (speed   !== null) body.speed   = speed;
    if (pitch   !== null) body.pitch   = pitch;
    if (effects !== null) body.effects = effects;
    const res = await fetch('/tts/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let gotFirst = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done || gen !== _ttsGen) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const d = JSON.parse(line.slice(6));
        if (d.error) { console.warn('TTS error:', d.error); return; }
        if (d.chunk) {
          if (gen !== _ttsGen) return;
          await _scheduleChunk(d.chunk, gen);
          if (!gotFirst) {
            gotFirst = true;
            document.getElementById('resay-btn').disabled = false;
          }
        }
      }
    }
  } catch (e) { if (gen === _ttsGen) console.warn('TTS stream error:', e); }
}

async function loadVoices() {
  try {
    const r = await fetch('/voices');
    const d = await r.json();
    const sel = document.getElementById('voice-select');
    if (sel) sel.innerHTML = d.voices.map(v => `<option value="${v}" ${v === d.current ? 'selected' : ''}>${v}</option>`).join('');
  } catch (e) { console.warn('Could not load voices:', e); }
}

async function switchVoice(voice) {
  await fetch('/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }) });
  if (lastReplyText) speak(lastReplyText);
}

async function _sttTranscribe(webmBlob, btn) {
  const inp = document.getElementById('inp');
  try {
    const res = await fetch('/stt', {
      method: 'POST',
      headers: { 'Content-Type': webmBlob.type || 'audio/webm' },
      body: webmBlob
    });
    const d = await res.json();
    if (d.text) {
      inp.value = d.text;
      send();
    } else {
      inp.placeholder = 'Could not hear anything — try again';
      setTimeout(() => inp.placeholder = 'Say something... or /image', 2500);
    }
  } catch (e) {
    console.warn('STT error:', e);
    inp.placeholder = 'Transcription failed';
    setTimeout(() => inp.placeholder = 'Say something... or /image', 2500);
  } finally {
    btn.textContent = 'Mic'; btn.disabled = false;
  }
}

async function loadMicDevices() {
  try {
    const tmp = await navigator.mediaDevices.getUserMedia({ audio: true });
    tmp.getTracks().forEach(t => t.stop());
    const devices = await navigator.mediaDevices.enumerateDevices();
    const sel = document.getElementById('mic-select');
    if (!sel) return;
    const saved = localStorage.getItem('micDeviceId');
    sel.innerHTML = '<option value="" disabled>── Audio input ──</option>' + devices
      .filter(d => d.kind === 'audioinput')
      .map(d => `<option value="${d.deviceId}" ${d.deviceId === saved ? 'selected' : ''}>${d.label || 'Mic ' + d.deviceId.slice(0,6)}</option>`)
      .join('');
    sel.onchange = () => localStorage.setItem('micDeviceId', sel.value);
  } catch (e) { console.warn('Could not enumerate audio devices:', e); }
}

async function toggleMic() {
  const btn = document.getElementById('mic-btn');
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }
  const deviceId = document.getElementById('mic-select')?.value;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true
    });
    audioChunks = [];
    const actx = new AudioContext();
    await actx.resume();
    const analyser = actx.createAnalyser();
    analyser.fftSize = 512;
    actx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    let hasSpeech = false, lastSpeech = Date.now();
    const silenceMs = window._sttSilenceMs || 3000;
    const silenceTimer = setInterval(() => {
      analyser.getByteTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((s, v) => s + (v - 128) ** 2, 0) / buf.length);
      if (rms > 5) { hasSpeech = true; lastSpeech = Date.now(); }
      if (hasSpeech && Date.now() - lastSpeech > silenceMs) {
        clearInterval(silenceTimer);
        if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
      }
    }, 100);
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      clearInterval(silenceTimer);
      stream.getTracks().forEach(t => t.stop());
      actx.close().catch(() => {});
      btn.textContent = 'STT…'; btn.disabled = true;
      btn.classList.remove('recording');
      await _sttTranscribe(new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' }), btn);
    };
    mediaRecorder.start(100);
    btn.textContent = 'Stop'; btn.classList.add('recording');
  } catch (e) { console.warn('Mic error:', e); }
}
