let mid = 0, imgAbort = null, chatAbort = null, muted = false;
let mediaRecorder = null, audioChunks = [];
let lastReplyText = '', lastUserMsg = '';
let charName = 'Alice';
let llmReady = false;
let _activePersona = 'default';
let imgHistory = [];
let _demoMode = false, _demoTimer = null, _demoSkip = false, _demoPaused = false;

const ENTRANCE_LINES = [
  "Hello. I've been waiting for you...",
  "You came back. I knew you would.",
  "Ah. There you are.",
  "I've been thinking about you.",
  "You're here. Finally.",
  "I was beginning to wonder.",
  "Come in. I don't bite... unless asked.",
  "I hoped it would be you.",
  "Tell me everything.",
  "I've been saving my best words for you.",
  "Good. I was growing tired of my own company.",
  "You always keep me waiting just long enough.",
];

function entranceLine() {
  return ENTRANCE_LINES[Math.floor(Math.random() * ENTRANCE_LINES.length)];
}

// ── Web Audio streaming TTS ───────────────────────────────────────────────────
let _audioCtx = null, _nextStart = 0, _ttsNodes = [], _ttsGen = 0;
let _lastChunks = [];   // decoded AudioBuffers from last speak() for instant resay

function _stopTts() {
  _ttsGen++;                    // invalidates any in-flight speak() loops
  _ttsNodes.forEach(n => { try { n.stop(0); } catch {} });
  _ttsNodes = [];
  _nextStart = 0;
  // _lastChunks is intentionally kept so resay() can replay after an interrupt
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
    // Disable skip once the last scheduled node finishes
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
  const audioBuf = await _audioCtx.decodeAudioData(buf.buffer);
  if (gen !== _ttsGen) return;   // stream was cancelled while decoding
  _lastChunks.push(audioBuf);
  _playAudioBuffer(audioBuf);
}

try {
  const saved = localStorage.getItem('alice_img_history_urls');
  if (saved) imgHistory = JSON.parse(saved);
} catch (e) { console.warn('Failed to load image history:', e); }

function saveImg(url, prompt) {
  if (!url) return;
  imgHistory.unshift({ url, prompt, ts: Date.now(), persona: _activePersona });
  if (imgHistory.length > 100) imgHistory.pop();
  try {
    localStorage.setItem('alice_img_history_urls', JSON.stringify(imgHistory));
  } catch (e) { console.error('Failed to save history:', e); }
  renderHistory();
}

function removeImgHistoryItem(index) {
  imgHistory.splice(index, 1);
  try { localStorage.setItem('alice_img_history_urls', JSON.stringify(imgHistory)); } catch {}
  renderHistory();
}

function renderHistory() {
  const container = document.getElementById('is');
  if (!container) return;
  container.innerHTML = imgHistory.map((item, i) => {
    const ts  = item.ts ? new Date(item.ts).toLocaleString() : '';
    const tip = [item.persona, ts, item.prompt].filter(Boolean).join('\n').replace(/"/g, '&quot;');
    return `<img src="${item.url}" onclick="showHistImg(${i})" title="${tip}" class="${i===0?'active':''}" onerror="removeImgHistoryItem(${i})">`;
  }).join('');
}

function showHistImg(index) {
  const item = imgHistory[index];
  if (!item) return;
  document.querySelectorAll('.is img').forEach((img, i) => {
    img.classList.toggle('active', i === index);
  });
  document.getElementById('ic').innerHTML = `<img src="${item.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
  setPrompt(item.prompt);
  if (item.persona && item.persona !== _activePersona) {
    switchPersona(item.persona, { reChat: false });
    const sel = document.getElementById('persona-select');
    if (sel) sel.value = item.persona;
  }
}

function clearImageHistory() {
  if (!confirm('Clear all saved images?')) return;
  imgHistory = [];
  localStorage.removeItem('alice_img_history_urls');
  renderHistory();
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd').value = '';
}

async function loadInfo() {
  try {
    const r = await fetch('/info');
    const d = await r.json();
    charName = d.name || 'Alice';
    document.title = charName;
    const h1 = document.querySelector('h1');
    if (h1) h1.textContent = charName.toUpperCase();
    const firstMsg = document.querySelector('#msgs .msg.alice .sndr');
    if (firstMsg) firstMsg.textContent = charName;
    const firstBody = document.querySelector('#msgs .msg.alice');
    if (firstBody && firstBody.childNodes.length === 2) {
      // Only replace the hardcoded greeting on first load (before any conversation)
      firstBody.childNodes[1].textContent = entranceLine();
    }
    if (d.demo) {
      if (d.demo.user_name)  DEMO_USER_NAME  = d.demo.user_name;
      if (d.demo.user_voice) DEMO_USER_VOICE = d.demo.user_voice;
      if (d.demo.user_speed) DEMO_USER_SPEED = d.demo.user_speed;
      if (d.demo.user_pitch) DEMO_USER_PITCH = d.demo.user_pitch;
    }
    window._sttSilenceMs = (d.stt_silence || 3) * 1000;
    if (!llmReady && d.llm_ready) {
      llmReady = true;
      setLLMReady(true);
    } else if (!d.llm_ready) {
      setLLMReady(false);
      setTimeout(loadInfo, 2000); // poll until ready
    }
    _updateContextMeter(d.history_msgs || 0, d.history_max || 20);
  } catch (e) { console.warn('Could not load info:', e); setTimeout(loadInfo, 2000); }
}

function setLLMReady(ready) {
  const inp = document.getElementById('inp');
  const btn = document.getElementById('ibtn');
  const mic = document.getElementById('mic-btn');
  if (ready) {
    inp.disabled = false;
    inp.placeholder = 'Say something... or /image';
    if (btn) btn.disabled = false;
  } else {
    inp.disabled = true;
    inp.placeholder = 'Waiting for LLM server to start...';
    if (btn) btn.disabled = true;
  }
  // mic is always enabled — STT works independently of the LLM
  if (mic) mic.disabled = false;
}

function _updateContextMeter(msgs, max) {
  const el = document.getElementById('ctx-meter');
  if (!el) return;
  const pct = max > 0 ? Math.min(100, Math.round(msgs / max * 100)) : 0;
  el.textContent = `${msgs}/${max}`;
  el.title = `${pct}% of context used`;
  el.style.color = pct >= 90 ? '#c08080' : pct >= 70 ? '#c0a060' : '#888';
}

loadInfo();

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
  if (muted) _stopTts();
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

function disableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = true;
}
function enableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = false;
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') interrupt('user ESC');
  const inText = ['INPUT', 'TEXTAREA'].includes(e.target.tagName);
  if (e.key === 'Delete') { if (!inText || !e.target.value) deleteActiveImage(); return; }
  if (inText) return;
  if (e.key === 'm' || e.key === 'M') toggleMute();
  if (e.key === 'r' || e.key === 'R') resay();
});

// Auto-stop demo if the user takes over — focus input or type a printable char into it
// Demo mode is not stopped by typing — only by actually sending a message or pressing the Demo button.

async function deleteActiveImage() {
  const img = document.querySelector('#ic img');
  if (!img) return;
  const filename = img.src.split('/').pop().split('?')[0];
  if (!filename.endsWith('.png')) return;
  const res = await fetch(`/image/${encodeURIComponent(filename)}`, { method: 'DELETE' });
  if (!res.ok) { console.warn('Delete failed', await res.text()); return; }
  const idx = imgHistory.findIndex(item => item.url.includes(filename));
  if (idx !== -1) imgHistory.splice(idx, 1);
  try { localStorage.setItem('alice_img_history_urls', JSON.stringify(imgHistory)); } catch {}
  renderHistory();
  if (imgHistory.length > 0) {
    showHistImg(0);
  } else {
    document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
    document.getElementById('pd').value = '';
  }
}

async function interrupt(reason) {
  if (reason === 'user' && _demoMode) { _demoSkip = true; }  // Skip current turn, don't stop demo
  _stopTts();
  if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
  if (chatAbort) {
    console.log('Aborting chat:', reason);
    chatAbort.abort();
    chatAbort = null;
  }
  if (imgAbort) {
    console.log('Aborting media generation:', reason);
    imgAbort.abort();
    imgAbort = null;
  }
  await fetch('/interrupt', { method: 'POST' }).catch(() => {});
}

function doImage() {
  const extra = document.getElementById('inp').value.trim();
  document.getElementById('inp').value = '';
  triggerMedia(extra);
}

let progressTimer = null, _lastPct = 0;
function startProgress() {
  stopProgress();
  _lastPct = 0;
  progressTimer = setInterval(async () => {
    try {
      const r = await fetch('/progress');
      const d = await r.json();
      const pct = Math.round((d.progress || 0) * 100);
      const fill   = document.getElementById('img-pb');
      const status = document.getElementById('img-status');
      if (fill)   fill.style.width = pct + '%';
      if (status) {
        const st = d.state || {};
        const step = st.sampling_step || 0;
        const total = st.sampling_steps || 0;
        const stepStr = total > 0 ? ` (${step}/${total})` : '';
        if (pct > 0)           status.textContent = `Generating... ${pct}%${stepStr}`;
        else if (_lastPct > 0) status.textContent = 'Finishing...';
      }
      _lastPct = pct;
      if (d.current_image) {
        const ic = document.getElementById('ic');
        if (ic && !ic.querySelector('img.final')) {
          let prev = ic.querySelector('img.preview');
          if (prev) {
            prev.src = `data:image/png;base64,${d.current_image}`;
          } else {
            const img = document.createElement('img');
            img.className = 'preview';
            img.src = `data:image/png;base64,${d.current_image}`;
            ic.insertBefore(img, ic.firstChild);
          }
        }
      }
    } catch {}
  }, 800);
}
function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

async function triggerMedia(extra = '', auto = false) {
  if (!auto) await interrupt('new media request');  // auto: already interrupted at chat start
  imgAbort = new AbortController();
  const { signal } = imgAbort;

  disableAll();
  document.getElementById('ih').textContent = 'Generated Scene';

  if (extra && !auto) addMsg('user', 'You', extra);

  document.getElementById('ic').innerHTML =
    '<div id="img-progress-wrap">' +
      '<div class="ph gen" id="img-status">Analyzing scene...</div>' +
      '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>' +
    '</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';

  startProgress();
  try {
    const res = await fetch('/image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ extra }),
      signal
    });
    const d = await res.json();
    if (d.error) {
      document.getElementById('ic').innerHTML = `<div class="ph">${d.error}</div>`;
    } else if (d.url) {
      // Stop polling before preload — frees browser connections for the image fetch
      stopProgress();
      const ic = document.getElementById('ic');
      const existing = ic.innerHTML;
      await new Promise(resolve => {
        const img = new Image();
        img.onload = img.onerror = resolve;
        img.src = d.url;
      });
      ic.innerHTML = `<img src="${d.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
      setPrompt(d.sd_prompt);
      saveImg(d.url, d.sd_prompt);
      _syncSeedBtn(d.pinned, d.seed);
      const rerollBtn = document.getElementById('reroll-btn');
      if (rerollBtn) rerollBtn.disabled = false;
    } else {
      document.getElementById('ic').innerHTML = '<div class="ph">No output generated.</div>';
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      document.getElementById('ic').innerHTML = '<div class="ph">Interrupted.</div>';
    } else {
      console.error('Image error:', e);
      document.getElementById('ic').innerHTML = `<div class="ph">Image error: ${e.message || 'Unknown error'}. Check console.</div>`;
    }
  }
  stopProgress();
  imgAbort = null;
  enableAll();
  document.getElementById('inp').focus();
}

async function loadModels() {
  try {
    const res = await fetch('/models');
    const d = await res.json();
    const sel = document.getElementById('model-select');
    sel.innerHTML = `<option value="" disabled selected>Model</option>` +
      d.models.map(m => `<option value="${m.path}">${m.name}</option>`).join('');
  } catch (e) { console.warn('Could not load models:', e); }
}

async function switchModel(sel) {
  const path = sel.value;
  const name = sel.options[sel.selectedIndex].text;
  sel.disabled = true;
  disableAll();
  const tid = addMsg('alice', charName, `<span class="gen">Loading ${name}...</span>`);
  const res = await fetch('/model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  });
  const d = await res.json();
  updMsg(tid, d.error ? `<em style="color:#c08080">${d.error}</em>` : `Switched to ${name}.`);
  sel.disabled = false;
  enableAll();
}

loadModels();

const _PERSONA_FONTS = {
  'default':          { family: "'Montserrat', sans-serif",      style: 'normal', size: '.88rem', weight: '300', spacing: 'normal' },
  'android':          { family: "'Share Tech Mono', monospace", style: 'normal', size: '.85rem', weight: '400', spacing: '.04em' },
  'victorian-lady':   { family: "'Pinyon Script', cursive",     style: 'normal', size: '1.3rem', weight: '400', spacing: 'normal' },
  'egyptian-goddess': { family: "'Cinzel Decorative', serif",   style: 'normal', size: '.82rem', weight: '400', spacing: '.06em' },
  'forest-witch':     { family: "'Almendra', serif",            style: 'italic', size: '1rem',   weight: '400', spacing: 'normal' },
};
const _DEFAULT_FONT = _PERSONA_FONTS['default'];

// Populated by loadPersonas() — maps persona name to font key
const _personaFontKeys = {};
_applyPersonaFont('default');  // apply default font immediately before loadPersonas() resolves

function _applyPersonaFont(name) {
  const key = _personaFontKeys[name] || name.toLowerCase().replace(/\s+/g, '-');
  const f = _PERSONA_FONTS[key] || _DEFAULT_FONT;
  const b = document.body;
  b.style.setProperty('--alice-font',    f.family);
  b.style.setProperty('--alice-fstyle',  f.style);
  b.style.setProperty('--alice-fsize',   f.size);
  b.style.setProperty('--alice-fweight', f.weight);
  b.style.setProperty('--alice-spacing', f.spacing);
  b.dataset.persona = key;
}

async function loadPersonas() {
  const res = await fetch('/personas');
  const d = await res.json();
  const sel = document.getElementById('persona-select');
  sel.innerHTML = d.personas.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  d.personas.forEach(p => { _personaFontKeys[p.name] = p.font_key; });
  if (sel.value) { _activePersona = sel.value; _applyPersonaFont(sel.value); }
}

async function switchPersona(name, { reChat = true } = {}) {
  await fetch(`/persona/${encodeURIComponent(name)}`, { method: 'POST' });
  _activePersona = name;
  _applyPersonaFont(name);
  await loadInfo();
  // Insert a divider so history is visually separated from the new persona
  const div = document.createElement('div');
  div.className = 'persona-switch-divider';
  div.textContent = `— ${charName} —`;
  document.getElementById('msgs').appendChild(div);
  document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight;
  document.getElementById('pd-wrap').style.display = 'none';
  const rerollBtn = document.getElementById('reroll-btn');
  if (rerollBtn) rerollBtn.disabled = true;
  await loadVoices();
  loadNegative();
  if (reChat && lastUserMsg) _chatWith(lastUserMsg);
}

loadPersonas();

async function loadDemoPersonas() {
  try {
    const r = await fetch('/demo/user-personas');
    const d = await r.json();
    const sel = document.getElementById('demo-persona-select');
    if (!sel) return;
    sel.innerHTML = `<option value="" disabled selected>Type</option>` +
      d.personas.map(p => `<option value="${p}">${p}</option>`).join('');
  } catch (e) { console.warn('Could not load demo personas:', e); }
}

async function switchDemoPersona(name) {
  await fetch('/demo/user-persona', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
}

loadDemoPersonas();

async function loadVoices() {
  try {
    const r = await fetch('/voices');
    const d = await r.json();
    const sel = document.getElementById('voice-select');
    sel.innerHTML = d.voices.map(v => `<option value="${v}" ${v === d.current ? 'selected' : ''}>${v}</option>`).join('');
  } catch (e) { console.warn('Could not load voices:', e); }
}

async function switchVoice(voice) {
  await fetch('/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }) });
  if (lastReplyText) speak(lastReplyText);
}

loadVoices();

function setPrompt(text) {
  if (!text) return;
  document.getElementById('pd').value = text;
}

function togglePromptPanel() {
  const wrap = document.getElementById('pd-wrap');
  const btn  = document.getElementById('pd-toggle');
  const open = wrap.style.display !== 'none';
  wrap.style.display = open ? 'none' : 'flex';
  btn.textContent = open ? '+' : '−';
}

async function regenFromPrompt() {
  const prompt = document.getElementById('pd').value.trim();
  if (!prompt) return;
  await interrupt('regenerating');
  imgAbort = new AbortController();
  disableAll();
  document.getElementById('ic').innerHTML =
    '<div class="ph gen" id="img-status">Regenerating...</div>' +
    '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>';
  const steps = parseInt(document.getElementById('steps').value);
  const cfg_scale = parseFloat(document.getElementById('cfg').value);
  startProgress();
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, steps, cfg_scale }),
      signal: imgAbort.signal
    });
    const d = await res.json();
    stopProgress();
    if (d.url) {
      document.getElementById('ic').innerHTML = `<img src="${d.url}">`;
      saveImg(d.url, prompt);
    } else {
      document.getElementById('ic').innerHTML = `<div class="ph">${d.error || 'No image generated.'}</div>`;
    }
  } catch (e) {
    if (e.name !== 'AbortError')
      document.getElementById('ic').innerHTML = '<div class="ph">Error.</div>';
  }
  stopProgress();
  imgAbort = null;
  enableAll();
}

renderHistory();

async function loadNegative() {
  try {
    const r = await fetch('/negative');
    const d = await r.json();
    const el = document.getElementById('neg-prompt');
    if (el && d.negative) el.textContent = d.negative;
  } catch (e) { console.warn('Could not load negative prompt:', e); }
}
loadNegative();

async function _chatWith(msg, { forceImage = false } = {}) {
  await interrupt('new message sent');
  const tid = addMsg('alice', charName, '<span class="gen dots">thinking</span>');
  document.getElementById('pd').value = '';
  document.getElementById('thinking-bar').style.display = 'block';
  chatAbort = new AbortController();
  disableAll();
  let reply = '', autoImage = true;
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
      signal: chatAbort.signal
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = JSON.parse(line.slice(6));
        if (data.error) { document.getElementById('thinking-bar').style.display='none'; updMsg(tid, '<em style="color:#c08080">' + data.error + '</em>'); }
        if (data.delta) { document.getElementById('thinking-bar').style.display='none'; reply += data.delta; updMsg(tid, reply); }
        if (data.done)  { reply = data.reply; updMsg(tid, reply); autoImage = data.auto_image; }
      }
    }
  } catch (e) {
    document.getElementById('thinking-bar').style.display = 'none';
    if (e.name === 'AbortError') { 
      updMsg(tid, reply || '<em style="color:#888">Interrupted.</em>'); 
    } else { 
      console.error('Chat error:', e);
      updMsg(tid, `<em style="color:#c08080">Chat error: ${e.message || 'Unknown error'}. Check console/terminal.</em>`); 
    }
    chatAbort = null; enableAll(); return;
  }
  document.getElementById('thinking-bar').style.display = 'none';
  chatAbort = null;
  enableAll();
  if (reply) { if (autoImage || forceImage) triggerMedia('', true); speak(reply); }
  loadInfo();
}

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  if (msg === '/export') { exportHistory(); return; }
  if (msg.startsWith('/image')) { await interrupt('new media request'); triggerMedia(msg.slice(6).trim()); return; }
  if (msg === '/auto-image') {
    const r = await fetch('/auto-image', { method: 'POST' });
    const d = await r.json();
    const inp2 = document.getElementById('inp');
    inp2.placeholder = `Auto-image ${d.auto_image ? 'ON' : 'OFF'}`;
    setTimeout(() => inp2.placeholder = 'Say something... or /image', 2500);
    return;
  }
  lastUserMsg = msg;
  addMsg('user', 'You', msg);
  inp.focus();
  if (_demoMode) {
    // Pause demo and abort its current turn so our message goes through cleanly
    _demoPaused = true;
    _demoSkip = true;
    _stopTts();
    if (chatAbort) { chatAbort.abort(); chatAbort = null; }
    if (imgAbort)  { imgAbort.abort();  imgAbort  = null; }
  }
  await _chatWith(msg);
  if (_demoMode) _demoPaused = false;  // let demo resume after Alice replies
  inp.focus();
}

function toggleDemo() {
  if (_demoMode) stopDemo();
  else startDemo();
}

function startDemo() {
  _demoMode = true;
  _demoTurn = 0;
  _updateDemoBtn();
  demoLoop();
}

function stopDemo() {
  _demoMode = false;
  _demoPaused = false;
  if (_demoTimer) { clearTimeout(_demoTimer); _demoTimer = null; }
  _updateDemoBtn();
}

function _updateDemoBtn() {
  const btn = document.getElementById('demo-btn');
  if (!btn) return;
  if (_demoMode) {
    btn.textContent = `Demo: ON (${_demoTurn})`;
    btn.classList.add('demo-active');
  } else {
    btn.textContent = 'Demo';
    btn.classList.remove('demo-active');
  }
}

let DEMO_USER_NAME  = 'Christian';
let DEMO_USER_VOICE = 'am_adam';
let DEMO_USER_SPEED = 0.88;
let DEMO_USER_PITCH = 0.88;
let _demoTurn = 0;

async function demoLoop() {
  if (!_demoMode) return;
  // If user is sending a message, hold here until they're done
  while (_demoPaused) {
    if (!_demoMode) return;
    await new Promise(r => setTimeout(r, 100));
  }
  _demoSkip = false;
  try {
    // Fetch the next user-side prompt, passing turn for mood-arc
    const r = await fetch(`/demo/prompt?turn=${_demoTurn}`);
    const d = await r.json();
    if (!_demoMode) return;
    if (_demoPaused) { demoLoop(); return; }
    if (d.error) {
      console.warn('[demo] prompt error:', d.error);
      // Retry after a short delay rather than stopping demo entirely
      await new Promise(r => setTimeout(r, 3000));
      demoLoop(); return;
    }
    const msg = d.prompt;
    if (!msg) { demoLoop(); return; }

    // Typing indicator — brief pause then reveal message
    const typingId = addMsg('user', DEMO_USER_NAME, '<span class="gen dots">typing</span>');
    if (!_demoSkip) {
      const typingDelay = 600 + Math.random() * 900;
      await new Promise(r => setTimeout(r, typingDelay));
    }
    if (!_demoMode) return;
    if (_demoPaused) { updMsg(typingId, '…'); demoLoop(); return; }
    updMsg(typingId, msg);
    lastUserMsg = msg;

    // Slight random speed jitter so each turn sounds a little different
    const spd = DEMO_USER_SPEED + (Math.random() * 0.06 - 0.03);
    await speak(msg, DEMO_USER_VOICE, spd, DEMO_USER_PITCH, '');
    const userTtsGen = _ttsGen;
    await _waitForTts(userTtsGen);

    if (!_demoMode) return;
    // Don't start a new LLM call while user's message is being processed
    if (_demoPaused) { demoLoop(); return; }

    // Send through the full chat pipeline (LLM reply + Alice TTS + image)
    await _chatWith(msg);

    if (!_demoMode) return;
    if (_demoPaused) { demoLoop(); return; }

    // _chatWith fires speak(reply) synchronously calling _stopTts() → _ttsGen++
    const aliceTtsGen = _ttsGen;

    // Wait for Alice TTS and image to both finish
    await Promise.all([_waitForTts(aliceTtsGen), _waitForImage()]);

    if (!_demoMode) return;

    _demoTurn++;
    _updateDemoBtn();

    // Variable pause 1.5–4s before next turn (skipped if user pressed Skip)
    if (!_demoSkip && !_demoPaused) {
      const pause = 1500 + Math.random() * 2500;
      await new Promise(resolve => { _demoTimer = setTimeout(resolve, pause); });
      _demoTimer = null;
    }
    demoLoop();
  } catch (e) {
    // AbortError is expected when a turn is interrupted — just restart the loop
    if (e.name === 'AbortError' || e.name === 'TypeError') {
      console.log('[demo] turn interrupted, restarting loop');
      if (_demoMode) setTimeout(demoLoop, 500);
    } else {
      console.warn('[demo] loop error:', e);
      stopDemo();
    }
  }
}

async function _waitForTts(gen, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  // Phase 1: wait up to 8s for speak() to start scheduling audio
  const p1End = Date.now() + 8000;
  while (Date.now() < p1End) {
    if (_ttsGen !== gen || !_demoMode || _demoSkip) return;
    if (_audioCtx && _nextStart > (_audioCtx.currentTime + 0.2)) break;
    await new Promise(r => setTimeout(r, 100));
  }
  // If no audio was ever scheduled (e.g. skipped before first chunk), nothing to wait for
  if (!_audioCtx || _nextStart <= 0.1) return;
  // Phase 2: wait for scheduled audio to finish playing
  while (Date.now() < deadline) {
    if (_ttsGen !== gen || !_demoMode || _demoSkip) return;
    if (_audioCtx.currentTime >= _nextStart - 0.1) return;
    await new Promise(r => setTimeout(r, 300));
  }
}

async function _waitForImage(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  // Wait for triggerMedia to start (imgAbort becomes non-null)
  while (Date.now() < deadline) {
    if (!_demoMode || _demoSkip) return;
    if (imgAbort) break;
    await new Promise(r => setTimeout(r, 100));
  }
  // Wait for image gen to finish (imgAbort cleared)
  while (Date.now() < deadline) {
    if (!_demoMode || _demoSkip) return;
    if (!imgAbort) return;
    await new Promise(r => setTimeout(r, 500));
  }
}

function renderMd(text) {
  return text
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
}

function addMsg(cls, sndr, html) {
  if (cls === 'alice' && _activePersona) _applyPersonaFont(_activePersona);
  const id = 'm' + (mid++), d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.id = id;
  d.innerHTML = `<div class="sndr">${sndr}</div>${html}`;
  const c = document.getElementById('msgs');
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
  return id;
}

function updMsg(id, t) {
  const e = document.getElementById(id);
  if (!e) return;
  const content = e.classList.contains('alice') ? renderMd(t) : t;
  e.innerHTML = e.querySelector('.sndr').outerHTML + content;
  const c = document.getElementById('msgs');
  c.scrollTop = c.scrollHeight;
}

function openFullscreen(src) {
  document.getElementById('fullscreen-img').src = src;
  document.getElementById('fullscreen-overlay').classList.add('open');
}
function closeFullscreen() {
  document.getElementById('fullscreen-overlay').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFullscreen(); });

function _syncSeedBtn(pinned, seed) {
  const btn = document.getElementById('seed-btn');
  if (!btn) return;
  btn.classList.toggle('pinned', !!pinned);
  btn.title = pinned ? `Seed ${seed} pinned — face consistent` : 'Pin seed for face consistency';
}

async function toggleSeedPin() {
  const btn = document.getElementById('seed-btn');
  const pinned = btn.classList.contains('pinned');
  const res = await fetch(pinned ? '/seed/unpin' : '/seed/pin', { method: 'POST' });
  const d = await res.json();
  _syncSeedBtn(d.pinned, d.seed);
}

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
loadMicDevices();

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

async function reroll() {
  await interrupt('reroll');
  imgAbort = new AbortController();
  disableAll();
  document.getElementById('ic').innerHTML =
    '<div class="ph gen" id="img-status">Re-rolling...</div>' +
    '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>';
  startProgress();
  try {
    const res = await fetch('/reroll', { method: 'POST', signal: imgAbort.signal });
    const d = await res.json();
    if (d.url) {
      await new Promise(resolve => {
        const img = new Image();
        img.onload = img.onerror = resolve;
        img.src = d.url;
      });
      document.getElementById('ic').innerHTML = `<img src="${d.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
      setPrompt(d.sd_prompt);
      saveImg(d.url, d.sd_prompt);
    } else {
      document.getElementById('ic').innerHTML = `<div class="ph">${d.error || 'No output.'}</div>`;
    }
  } catch (e) {
    if (e.name !== 'AbortError')
      document.getElementById('ic').innerHTML = '<div class="ph">Error.</div>';
  }
  stopProgress();
  imgAbort = null;
  enableAll();
}

async function exportHistory() {
  const res = await fetch('/history');
  const d = await res.json();
  const text = d.history.map(m => `${m.role.toUpperCase()}: ${m.content}`).join('\n\n') +
    (d.memory ? `\n\n--- MEMORY ---\n${d.memory}` : '');
  const a = document.createElement('a');
  a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(text);
  a.download = `alice_chat_${new Date().toISOString().slice(0,16).replace('T','_')}.txt`;
  a.click();
}

async function clearHistory() {
  await fetch('/history', { method: 'DELETE' });
  document.getElementById('msgs').innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>${entranceLine()}</div>`;
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
  lastReplyText = '';
  _stopTts();
  _lastChunks = [];
  document.getElementById('resay-btn').disabled = true;
}
