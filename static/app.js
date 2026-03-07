let mid = 0, imgAbort = null;

function disableAll() {
  ['btn', 'ibtn', 'vbtn'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = true; });
}
function enableAll() {
  ['btn', 'ibtn', 'vbtn'].forEach(id => { const e = document.getElementById(id); if (e) e.disabled = false; });
}

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
    } else if (endpoint === '/video' && !d.fallback && d.video) {
      document.getElementById('ic').innerHTML = `<video autoplay loop muted src="data:video/mp4;base64,${d.video}"></video>`;
      setPrompt(d.sd_prompt);
    } else if (d.image || d.video) {
      const b64 = d.image || d.video;
      document.getElementById('ic').innerHTML = `<img src="data:image/png;base64,${b64}">`;
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
  document.getElementById('pd-wrap').style.display = 'flex';
}

async function regenFromPrompt() {
  const prompt = document.getElementById('pd').value.trim();
  if (!prompt) return;
  await interrupt('regenerating');
  imgAbort = new AbortController();
  disableAll();
  document.getElementById('ic').innerHTML = '<div class="ph gen">Regenerating...</div>';
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
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
  const btn = document.getElementById('btn');
  btn.disabled = true;

  await interrupt('new message sent');

  if (msg.startsWith('/video')) { triggerMedia('/video', msg.slice(6).trim()); return; }
  if (msg.startsWith('/image')) { triggerMedia('/image', msg.slice(6).trim()); return; }

  addMsg('user', 'You', msg);
  const tid = addMsg('alice', 'Alice', '<span class="gen">thinking...</span>');
  document.getElementById('pd').value = '';

  let success = false;
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg })
    });
    const d = await res.json();
    if (d.error) { updMsg(tid, '<em style="color:#c08080">' + d.error + '</em>'); }
    else { updMsg(tid, d.reply); success = true; }
  } catch (e) {
    updMsg(tid, '<em style="color:#c08080">Could not reach backend — is alice.py running?</em>');
  }
  btn.disabled = false;
  inp.focus();

  if (success) triggerMedia('/image', '', true);
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
