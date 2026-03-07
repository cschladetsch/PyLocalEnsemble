let mid = 0, imgAbort = null, muted = false, ttsAudio = null, lastAudioSrc = null;

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
  ['ibtn', 'vbtn'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = true; });
  const s = document.getElementById('stop-btn'); if (s) s.disabled = false;
}
function enableAll() {
  ['ibtn', 'vbtn'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = false; });
  const s = document.getElementById('stop-btn'); if (s) s.disabled = true;
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') interrupt('user ESC');
});

async function interrupt(reason) {
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
  triggerMedia('/image', extra);
}

function doVideo() {
  const extra = document.getElementById('inp').value.trim();
  document.getElementById('inp').value = '';
  triggerMedia('/video', extra);
}

async function triggerMedia(endpoint, extra = '', auto = false) {
  await interrupt('new media request');
  imgAbort = new AbortController();
  const { signal } = imgAbort;

  disableAll();
  const label = endpoint === '/video' ? 'Generating video...' : 'Generating scene...';
  const header = endpoint === '/video' ? 'Generated Video' : 'Generated Scene';
  document.getElementById('ih').textContent = header;

  if (extra && !auto) addMsg('user', 'You', extra);

  document.getElementById('ic').innerHTML = `<div class="ph gen">${label}</div>`;
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ extra }),
      signal
    });
    const d = await res.json();
    if (d.error) {
      document.getElementById('ic').innerHTML = `<div class="ph">${d.error}</div>`;
    } else if (d.gif) {
      document.getElementById('ic').innerHTML = `<img src="data:image/gif;base64,${d.gif}">`;
      setPrompt(d.sd_prompt);
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
  const tid = addMsg('alice', 'Alice', `<span class="gen">Loading ${name}...</span>`);
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
  document.getElementById('msgs').innerHTML = '<div class="msg alice"><div class="sndr">Alice</div>Hello. I&#39;ve been waiting for you...</div>';
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
}

loadPersonas();

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
  document.getElementById('ic').innerHTML = '<div class="ph gen">Regenerating...</div>';
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

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  await interrupt('new message sent');

  if (msg.startsWith('/video')) { triggerMedia('/video', msg.slice(6).trim()); return; }
  if (msg.startsWith('/image')) { triggerMedia('/image', msg.slice(6).trim()); return; }

  addMsg('user', 'You', msg);
  const tid = addMsg('alice', 'Alice', '<span class="gen">thinking...</span>');
  document.getElementById('pd').value = '';

  let success = false, reply = '';
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg })
    });
    const d = await res.json();
    if (d.error) { updMsg(tid, '<em style="color:#c08080">' + d.error + '</em>'); }
    else { reply = d.reply; updMsg(tid, reply); success = true; }
  } catch (e) {
    updMsg(tid, '<em style="color:#c08080">Could not reach backend — is alice.py running?</em>');
  }
  inp.focus();

  if (success) {
    await speak(reply);
    triggerMedia('/image', '', true);
  }
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
}

async function clearHistory() {
  await fetch('/history', { method: 'DELETE' });
  document.getElementById('msgs').innerHTML = '<div class="msg alice"><div class="sndr">Alice</div>Hello. I&#39;ve been waiting for you...</div>';
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
}
