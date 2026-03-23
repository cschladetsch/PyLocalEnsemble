// ── UI Utilities & Info ────────────────────────────────────────────────────────
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
  if (c) {
    c.appendChild(d);
    c.scrollTop = c.scrollHeight;
  }
  return id;
}

function updMsg(id, t) {
  const e = document.getElementById(id);
  if (!e) return;
  const content = e.classList.contains('alice') ? renderMd(t) : t;
  e.innerHTML = e.querySelector('.sndr').outerHTML + content;
  const c = document.getElementById('msgs');
  if (c) c.scrollTop = c.scrollHeight;
}

function openFullscreen(src) {
  document.getElementById('fullscreen-img').src = src;
  document.getElementById('fullscreen-overlay').classList.add('open');
}
function closeFullscreen() {
  document.getElementById('fullscreen-overlay').classList.remove('open');
}

function disableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = true;
}
function enableAll() {
  const e = document.getElementById('ibtn'); if (e) e.disabled = false;
}

function setPrompt(text) {
  if (!text) return;
  const pd = document.getElementById('pd');
  if (pd) pd.value = text;
}

function togglePromptPanel() {
  const wrap = document.getElementById('pd-wrap');
  const btn  = document.getElementById('pd-toggle');
  if (!wrap || !btn) return;
  const open = wrap.style.display !== 'none';
  wrap.style.display = open ? 'none' : 'flex';
  btn.textContent = open ? '+' : '−';
}

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

function _updateContextMeter(msgs, max) {
  const el = document.getElementById('ctx-meter');
  if (!el) return;
  const pct = max > 0 ? Math.min(100, Math.round(msgs / max * 100)) : 0;
  el.textContent = `${msgs}/${max}`;
  el.title = `${pct}% of context used`;
  el.style.color = pct >= 90 ? '#c08080' : pct >= 70 ? '#c0a060' : '#888';
}

async function loadInfo() {
  try {
    const r = await fetch('/info');
    const d = await r.json();
    charName = d.name || 'Alice';
    document.title = charName;
    const h1 = document.querySelector('h1');
    if (h1) h1.textContent = charName.toUpperCase();
    if (d.active_persona && d.active_persona !== _activePersona) {
      const psel = document.getElementById('persona-select');
      if (psel && psel.querySelector(`option[value="${d.active_persona}"]`)) {
        psel.value    = d.active_persona;
        _activePersona = d.active_persona;
        _applyPersonaFont(d.active_persona);
      }
    }
    const firstMsg = document.querySelector('#msgs .msg.alice .sndr');
    if (firstMsg) firstMsg.textContent = charName;
    const firstBody = document.querySelector('#msgs .msg.alice');
    if (firstBody && firstBody.childNodes.length === 2) {
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
      setTimeout(loadInfo, 2000);
    }
    _updateContextMeter(d.history_msgs || 0, d.history_max || 20);
  } catch (e) { console.warn('Could not load info:', e); setTimeout(loadInfo, 2000); }
}

function setLLMReady(ready) {
  const inp = document.getElementById('inp');
  const btn = document.getElementById('ibtn');
  const mic = document.getElementById('mic-btn');
  if (ready) {
    if (inp) {
      inp.disabled = false;
      inp.placeholder = 'Say something... or /image';
    }
    if (btn) btn.disabled = false;
  } else {
    if (inp) {
      inp.disabled = true;
      inp.placeholder = 'Waiting for LLM server to start...';
    }
    if (btn) btn.disabled = true;
  }
  if (mic) mic.disabled = false;
}
