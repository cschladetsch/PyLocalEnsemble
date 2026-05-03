// ── Web Audio / TTS / STT ────────────────────────────────────────────────────
// Loaded after app.js — depends on globals: muted, mediaRecorder, audioChunks,
// lastReplyText, chatAbort, imgAbort, _imgGenId, _demoSkip, _groupMode.

let _audioCtx = null, _nextStart = 0, _ttsNodes = [], _ttsGen = 0;
let _lastChunks = [];   // decoded AudioBuffers from last speak() for instant resay
let _groupSpeeches = [];
let _groupMomentum = {};

function _stopTts() {
  _ttsGen++;                    // invalidates any in-flight speak() loops
  _ttsNodes.forEach(n => { try { n.stop(0); } catch {} });
  _ttsNodes = [];
  _nextStart = 0;
  // _lastChunks is intentionally kept so resay() can replay after an interrupt
  const skip = document.getElementById('skip-btn');
  if (skip) skip.disabled = true;
}

function _stopGroupSpeech() {
  _groupSpeeches.forEach(speech => {
    speech.sources.forEach(src => { try { src.stop(0); } catch {} });
    try { speech.gain.disconnect(); } catch {}
  });
  _groupSpeeches = [];
}

function skipVoice() {
  _demoSkip = true;
  _stopTts();
  if (chatAbort) { chatAbort.abort(); chatAbort = null; }
  if (imgAbort)  { imgAbort.abort();  imgAbort  = null; _imgGenId++; }
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
    if (gen !== _ttsGen) return;   // stream was cancelled while decoding
    _lastChunks.push(audioBuf);
    _playAudioBuffer(audioBuf);
  } catch (e) {
    console.error('Audio decode failed:', e);
  }
}

function resay() {
  if (!_lastChunks.length && !lastReplyText) return;
  if (!_lastChunks.length) { speak(lastReplyText); return; }
  // Replay cached AudioBuffers — no server round-trip
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
  _lastChunks = [];             // discard cached chunks — new speech incoming
  lastReplyText = text;
  const gen = _ttsGen;          // snapshot — if _stopTts() fires, gen !== _ttsGen
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

// Like speak() but chains audio after current playback without resetting TTS state.
// Use to append more speech to an already-running or scheduled stream.
async function speakChain(text, voice = null, speed = null, pitch = null, effects = null) {
  if (muted || !text.trim()) return;
  const gen = _ttsGen;
  lastReplyText = lastReplyText ? lastReplyText + ' ' + text : text;
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
    while (true) {
      const { done, value } = await reader.read();
      if (done || gen !== _ttsGen) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const d = JSON.parse(line.slice(6));
        if (d.error) { console.warn('TTS chain error:', d.error); return; }
        if (d.chunk) {
          if (gen !== _ttsGen) return;
          await _scheduleChunk(d.chunk, gen);
          const resayBtn = document.getElementById('resay-btn');
          if (resayBtn) resayBtn.disabled = false;
        }
      }
    }
  } catch (e) { if (gen === _ttsGen) console.warn('TTS chain error:', e); }
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

// ── STT ──────────────────────────────────────────────────────────────────────

async function _sttTranscribe(webmBlob, btn) {
  const inp = document.getElementById('inp');
  try {
    console.log('STT blob:', webmBlob.size, 'bytes,', webmBlob.type);
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
    // trigger permission prompt so labels are populated
    const tmp = await navigator.mediaDevices.getUserMedia({ audio: true });
    tmp.getTracks().forEach(t => t.stop());
    const devices = await navigator.mediaDevices.enumerateDevices();
    const sel = document.getElementById('mic-select');
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

    // Silence-based auto-stop
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
      btn.textContent = 'STT…';
      btn.disabled = true;
      btn.classList.remove('recording');
      await _sttTranscribe(new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' }), btn);
    };

    mediaRecorder.start(100);
    btn.textContent = 'Stop';
    btn.classList.add('recording');
  } catch (e) {
    console.warn('Mic error:', e);
    alert('Microphone access denied or unavailable.');
  }
}

// ── Group TTS ─────────────────────────────────────────────────────────────────

function _resetGroupAudioState() {
  _groupMomentum = {};
}

function _decayGroupMomentum(now = performance.now()) {
  for (const [key, state] of Object.entries(_groupMomentum)) {
    const age = Math.max(0, (now - state.ts) / 1000);
    const value = Math.max(0, state.value - age * 0.18);
    if (value <= 0.01) delete _groupMomentum[key];
    else _groupMomentum[key] = { value, ts: now };
  }
}

function _groupSexinessScore(text = '') {
  const lower = text.toLowerCase();
  let score = 0;
  const weighted = [
    [/\b(fuck|cum|pussy|cock|cunt|throat|breed|ride|spread|wet|hard|orgasm)\b/g, 0.22],
    [/\b(moan|moaning|pant|panting|breath|shiver|throb|ache|heat|need|want|taste|lick|kiss)\b/g, 0.12],
    [/\b(good girl|good boy|come here|look at me|take it|touch me|use me|open for me)\b/g, 0.28],
  ];
  for (const [pattern, weight] of weighted) {
    const hits = lower.match(pattern);
    if (hits) score += hits.length * weight;
  }
  if (/[!?]/.test(text)) score += 0.08;
  if (/\byou\b/.test(lower)) score += 0.06;
  return Math.min(2.2, score);
}

function _groupDominanceFor(personaKey, text = '') {
  const now = performance.now();
  _decayGroupMomentum(now);
  const prev = _groupMomentum[personaKey]?.value || 0;
  const sexiness = _groupSexinessScore(text);
  const dominance = 1 + sexiness + prev * 0.35;
  _groupMomentum[personaKey] = { value: Math.min(2.5, prev * 0.45 + sexiness + 0.35), ts: now };
  return dominance;
}

function _createGroupSpeech(personaKey, dominance) {
  _ensureAudioCtx();
  const gain = _audioCtx.createGain();
  gain.gain.value = 1;
  gain.connect(_audioCtx.destination);
  const speech = {
    personaKey,
    dominance,
    gain,
    nextStart: _audioCtx.currentTime + 0.02,
    sources: [],
    active: true,
  };
  _groupSpeeches.push(speech);
  return speech;
}

function _applyGroupMix(focusSpeech) {
  _groupSpeeches = _groupSpeeches.filter(s => s.active);
  const now = _audioCtx ? _audioCtx.currentTime : 0;
  const strongerExisting = focusSpeech
    ? _groupSpeeches.some(s => s !== focusSpeech && s.active && s.dominance > focusSpeech.dominance)
    : false;

  for (const speech of _groupSpeeches) {
    let target = 1;
    if (focusSpeech && speech !== focusSpeech) {
      target = speech.dominance > focusSpeech.dominance ? 1 : 0.32;
    } else if (focusSpeech && speech === focusSpeech && strongerExisting) {
      target = 0.42;
    }
    speech.gain.gain.cancelScheduledValues(now);
    speech.gain.gain.linearRampToValueAtTime(target, now + 0.18);
  }
}

function _cleanupGroupSpeech(speech) {
  speech.active = false;
  _groupSpeeches = _groupSpeeches.filter(s => s.active);
  _applyGroupMix(null);
}

function _playGroupAudioBuffer(speech, audioBuf) {
  const src = _audioCtx.createBufferSource();
  src.buffer = audioBuf;
  src.connect(speech.gain);
  const now = _audioCtx.currentTime;
  const start = Math.max(now + 0.02, speech.nextStart);
  src.start(start);
  speech.nextStart = start + audioBuf.duration;
  speech.sources.push(src);
  src.onended = () => {
    speech.sources = speech.sources.filter(s => s !== src);
    if (!speech.sources.length && speech.nextStart <= (_audioCtx.currentTime + 0.05)) {
      _cleanupGroupSpeech(speech);
    }
  };
}

async function _speakGroup(personaKey, text, tts = {}) {
  if (muted || !_groupMode) return;
  const dominance = _groupDominanceFor(personaKey, text);
  const speech = _createGroupSpeech(personaKey, dominance);
  _applyGroupMix(speech);

  try {
    const body = { text };
    if (tts.voice   !== null && tts.voice   !== undefined) body.voice   = tts.voice;
    if (tts.speed   !== null && tts.speed   !== undefined) body.speed   = tts.speed;
    if (tts.effects !== null && tts.effects !== undefined) body.effects = tts.effects;
    const res = await fetch('/tts/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (speech.active) {
      const { done, value } = await reader.read();
      if (done || !speech.active || !_groupMode) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const d = JSON.parse(line.slice(6));
        if (d.error) return;
        if (d.chunk) {
          const bytes = atob(d.chunk);
          const raw = new Uint8Array(bytes.length);
          for (let i = 0; i < bytes.length; i++) raw[i] = bytes.charCodeAt(i);
          const audioBuf = await _audioCtx.decodeAudioData(raw.buffer);
          if (!speech.active || !_groupMode) return;
          _playGroupAudioBuffer(speech, audioBuf);
        }
      }
    }
  } catch (e) {
    if (speech.active) console.warn('Group TTS stream error:', e);
  } finally {
    if (!speech.sources.length) _cleanupGroupSpeech(speech);
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────
loadVoices();
loadMicDevices();
