let mid = 0, imgAbort = null, chatAbort = null, muted = false, ttsAudio = null, lastAudioSrc = null;
let mediaRecorder = null, audioChunks = [];
let lastReplyText = '';
let charName = 'Alice';
let llmReady = false;
let imgHistory = [];

try {
  const saved = localStorage.getItem('alice_img_history_urls');
  if (saved) imgHistory = JSON.parse(saved);
} catch (e) { console.warn('Failed to load image history:', e); }

function saveImg(url, prompt) {
  if (!url) return;
  imgHistory.unshift({ url, prompt, ts: Date.now() });
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
  container.innerHTML = imgHistory.map((item, i) =>
    `<img src="${item.url}" onclick="showHistImg(${i})" title="${item.prompt || ''}" class="${i===0?'active':''}" onerror="removeImgHistoryItem(${i})">`
  ).join('');
}

function showHistImg(index) {
  const item = imgHistory[index];
  if (!item) return;
  document.querySelectorAll('.is img').forEach((img, i) => {
    img.classList.toggle('active', i === index);
  });
  document.getElementById('ic').innerHTML = `<img src="${item.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
  setPrompt(item.prompt);
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
    if (h1) h1.textContent = charName;
    const firstMsg = document.querySelector('#msgs .msg.alice .sndr');
    if (firstMsg) firstMsg.textContent = charName;
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
  if (!lastAudioSrc) return;
  if (ttsAudio) { ttsAudio.pause(); ttsAudio = null; }
  ttsAudio = new Audio(lastAudioSrc);
  ttsAudio.play();
}

function toggleMute() {
  muted = !muted;
  if (muted && ttsAudio) { ttsAudio.pause(); ttsAudio = null; }
  document.getElementById('mute-btn').textContent = muted ? 'Unmute' : 'Mute';
}

async function speak(text) {
  if (muted) return;
  try {
    const res = await fetch('/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const d = await res.json();
    if (d.audio) {
      if (ttsAudio) { ttsAudio.pause(); ttsAudio = null; }
      lastAudioSrc = 'data:audio/wav;base64,' + d.audio;
      ttsAudio = new Audio(lastAudioSrc);
      ttsAudio.play(); // fire and forget — image gen starts while audio plays
      document.getElementById('resay-btn').disabled = false;
    }
  } catch (e) { console.warn('TTS error:', e); }
}

function disableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = true;
  const s = document.getElementById('stop-btn'); if (s) s.disabled = false;
}
function enableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = false;
  const s = document.getElementById('stop-btn'); if (s) s.disabled = true;
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') interrupt('user ESC');
  if (e.key === 'Delete' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
    deleteActiveImage();
  }
});

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
          ic.innerHTML = `<img src="data:image/png;base64,${d.current_image}" class="preview">`;
        }
      }
    } catch {}
  }, 800);
}
function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

async function triggerMedia(extra = '', auto = false) {
  await interrupt('new media request');
  imgAbort = new AbortController();
  const { signal } = imgAbort;

  disableAll();
  document.getElementById('ih').textContent = 'Generated Scene';

  if (extra && !auto) addMsg('user', 'You', extra);

  document.getElementById('ic').innerHTML =
    '<div class="ph gen" id="img-status">Analyzing scene...</div>' +
    '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>';
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
      document.getElementById('ic').innerHTML = `<img src="${d.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
      setPrompt(d.sd_prompt);
      saveImg(d.url, d.sd_prompt);
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
    sel.innerHTML = d.models.map(m =>
      `<option value="${m.path}" ${m.name === d.current ? 'selected' : ''}>${m.name}</option>`
    ).join('');
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
  'android':          { family: "'Share Tech Mono', monospace", style: 'normal', size: '.85rem', weight: '400', spacing: '.04em' },
  'victorian-lady':   { family: "'Pinyon Script', cursive",     style: 'normal', size: '1.3rem',  weight: '400', spacing: 'normal' },
  'egyptian-goddess': { family: "'Cinzel Decorative', serif",   style: 'normal', size: '.82rem', weight: '400', spacing: '.06em' },
  'forest-witch':     { family: "'Almendra', serif",            style: 'italic', size: '1rem',   weight: '400', spacing: 'normal' },
};
const _DEFAULT_FONT = { family: "'Cormorant Garamond', serif", style: 'italic', size: '1rem', weight: '300', spacing: 'normal' };

function _applyPersonaFont(name) {
  const key = name.toLowerCase().replace(/\s+/g, '-');
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
  sel.innerHTML = d.personas.map(p => `<option value="${p}">${p}</option>`).join('');
  if (sel.value) _applyPersonaFont(sel.value);
}

async function switchPersona(name) {
  await fetch(`/persona/${encodeURIComponent(name)}`, { method: 'POST' });
  _applyPersonaFont(name);
  document.getElementById('msgs').innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>Hello. I&#39;ve been waiting for you...</div>`;
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
}

loadPersonas();

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
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, steps, cfg_scale }),
      signal: imgAbort.signal
    });
    const d = await res.json();
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
  imgAbort = null;
  enableAll();
}

renderHistory();

async function _chatWith(msg) {
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
  if (reply) { lastReplyText = reply; speak(reply); if (autoImage) triggerMedia('', true); }
  loadInfo();
}

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  if (msg.startsWith('/image')) { await interrupt('new media request'); triggerMedia(msg.slice(6).trim()); return; }
  addMsg('user', 'You', msg);
  inp.focus();
  await _chatWith(msg);
  inp.focus();
}

function renderMd(text) {
  return text
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
}

function addMsg(cls, sndr, html) {
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

async function toggleSeedPin() {
  const btn = document.getElementById('seed-btn');
  const pinned = btn.classList.contains('pinned');
  const res = await fetch(pinned ? '/seed/unpin' : '/seed/pin', { method: 'POST' });
  const d = await res.json();
  btn.classList.toggle('pinned', d.pinned);
  btn.title = d.pinned ? `Seed ${d.seed} pinned — face consistent` : 'Pin seed for face consistency';
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
  document.getElementById('msgs').innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>Hello. I&#39;ve been waiting for you...</div>`;
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
}
