let mid = 0, imgAbort = null, chatAbort = null, muted = false, ttsAudio = null, lastAudioSrc = null;
let mediaRecorder = null, audioChunks = [];
let charName = 'Alice';
let llmReady = false;

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
    if (!llmReady && d.llm_ready) {
      llmReady = true;
      setLLMReady(true);
    } else if (!d.llm_ready) {
      setLLMReady(false);
      setTimeout(loadInfo, 2000); // poll until ready
    }
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
});

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

let progressTimer = null;
function startProgress() {
  stopProgress();
  progressTimer = setInterval(async () => {
    try {
      const r = await fetch('/progress');
      const d = await r.json();
      const pct = Math.round((d.progress || 0) * 100);
      const fill   = document.getElementById('img-pb');
      const status = document.getElementById('img-status');
      if (fill)   fill.style.width = pct + '%';
      if (status) status.textContent = pct > 0 ? `Generating... ${pct}%` : 'Preparing...';
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
    '<div class="ph gen" id="img-status">Generating scene...</div>' +
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
    } else if (d.image) {
      document.getElementById('ic').innerHTML = `<img src="data:image/png;base64,${d.image}">`;
      setPrompt(d.sd_prompt);
    } else {
      document.getElementById('ic').innerHTML = '<div class="ph">No output generated.</div>';
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      document.getElementById('ic').innerHTML = '<div class="ph">Interrupted.</div>';
    } else {
      document.getElementById('ic').innerHTML = '<div class="ph">Error contacting backend.</div>';
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

async function loadPersonas() {
  const res = await fetch('/personas');
  const d = await res.json();
  const sel = document.getElementById('persona-select');
  sel.innerHTML = d.personas.map(p => `<option value="${p}">${p}</option>`).join('');
}

async function switchPersona(name) {
  await fetch(`/persona/${encodeURIComponent(name)}`, { method: 'POST' });
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
    if (d.image) {
      document.getElementById('ic').innerHTML = `<img src="data:image/png;base64,${d.image}">`;
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
    if (e.name === 'AbortError') { updMsg(tid, reply || '<em style="color:#888">Interrupted.</em>'); }
    else { updMsg(tid, '<em style="color:#c08080">Could not reach backend — is alice.py running?</em>'); }
    chatAbort = null; enableAll(); return;
  }
  document.getElementById('thinking-bar').style.display = 'none';
  chatAbort = null;
  enableAll();
  if (reply) { await speak(reply); if (autoImage) triggerMedia('', true); }
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
  e.innerHTML = e.querySelector('.sndr').outerHTML + t;
  const c = document.getElementById('msgs');
  c.scrollTop = c.scrollHeight;
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
      inp.focus();
      inp.setSelectionRange(d.text.length, d.text.length);
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
    sel.innerHTML = devices
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

    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
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

async function clearHistory() {
  await fetch('/history', { method: 'DELETE' });
  document.getElementById('msgs').innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>Hello. I&#39;ve been waiting for you...</div>`;
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
}
