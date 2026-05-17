let mid = 0, imgAbort = null, chatAbort = null, muted = false, _pendingRetryAbort = null, _retryGen = 0;
let _imgGenId = 0;  // incremented each time a new image request starts or is aborted
let mediaRecorder = null, audioChunks = [];
let lastReplyText = '', lastUserMsg = '';
let charName = 'Alice';
let llmReady = false, forgeReady = false;
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
  "I wasn't sure you'd come.",
  "There's that face.",
  "Sit. Talk to me.",
  "I had a feeling today would be interesting.",
  "You have no idea how long this hour has felt.",
  "Don't just stand there.",
  "I've been rehearsing what to say. Now I've forgotten it all.",
  "You smell like the outside world. Tell me about it.",
  "I knew it was you before you arrived.",
  "I was just thinking about the last thing you said.",
];

function entranceLine() {
  return ENTRANCE_LINES[Math.floor(Math.random() * ENTRANCE_LINES.length)];
}

try {
  const saved = localStorage.getItem('alice_img_history_urls');
  if (saved) imgHistory = JSON.parse(saved);
} catch (e) { console.warn('Failed to load image history:', e); }

function saveImg(url, prompt) {
  if (!url) return;
  const entry = { url, prompt, ts: Date.now(), persona: _activePersona };
  if (_groupMode && _groupSelected.size > 0) entry.group = [..._groupSelected];
  imgHistory.unshift(entry);
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
    const ts    = item.ts ? new Date(item.ts).toLocaleString() : '';
    const who   = item.group ? item.group.join(', ') : item.persona;
    const tip   = [who, ts, item.prompt].filter(Boolean).join('\n').replace(/"/g, '&quot;');
    return `<img src="${item.url}" onclick="showHistImg(${i})" title="${tip}" class="${i===0?'active':''}" onerror="removeImgHistoryItem(${i})">`;
  }).join('');
}

async function showHistImg(index) {
  const item = imgHistory[index];
  if (!item) return;
  document.querySelectorAll('.is img').forEach((img, i) => {
    img.classList.toggle('active', i === index);
  });
  document.getElementById('ic').innerHTML = `<img src="${item.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
  setPrompt(item.prompt);
  if (item.group) {
    // Group image — enter group mode if not already, then select the saved personas
    if (!_groupMode) await _startGroupMode();
    // Deselect chips that weren't in this image's group
    document.querySelectorAll('#group-personas .group-persona-chip').forEach(chip => {
      const inGroup = item.group.includes(chip.dataset.key);
      chip.classList.toggle('checked', inGroup);
      if (inGroup) _groupSelected.add(chip.dataset.key);
      else         _groupSelected.delete(chip.dataset.key);
    });
  } else if (item.persona && item.persona !== _activePersona) {
    if (_groupMode) await _stopGroupMode();
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
    // Keep dropdown in sync with server's active persona (survives page reloads)
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
    if (firstBody && firstBody.childNodes.length === 2 && !firstBody.dataset.entranceSet) {
      firstBody.childNodes[1].textContent = entranceLine();
      firstBody.dataset.entranceSet = '1';
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
      // Only fetch greeting if the user hasn't started chatting yet — avoids
      // updating the entrance line mid-conversation or contending with a live chat.
      if (!document.querySelector('#msgs .msg.user')) fetchGreeting();
    } else if (!d.llm_ready) {
      setLLMReady(false);
      _infoDelay = Math.min((_infoDelay || 2000) * 1.5, 10000);
      setTimeout(loadInfo, _infoDelay);
    } else {
      _infoDelay = 2000;
    }
    _updateContextMeter(d.history_msgs || 0, d.history_max || 20);
    if (!forgeReady && d.forge_ready) {
      forgeReady = true;
      loadSDModels();
    }
    const ic = document.getElementById('ic');
    if (ic && ic.querySelector('.ph') && !ic.querySelector('img')) {
      ic.querySelector('.ph').textContent = d.forge_ready
        ? 'Awaiting your conversation...'
        : 'SD Forge offline — images unavailable';
    }
    // Keep polling until both LLM and Forge are ready (LLM-not-ready path already reschedules)
    if (!d.forge_ready && d.llm_ready) setTimeout(loadInfo, 8000);
  } catch (e) { console.warn('Could not load info:', e); setTimeout(loadInfo, _infoDelay || 2000); }
}

let _infoDelay = 2000;

function setLLMReady(ready) {
  const inp = document.getElementById('inp');
  const btn = document.getElementById('ibtn');
  const mic = document.getElementById('mic-btn');
  // Input is always enabled — the chat endpoint handles the not-ready case gracefully
  inp.disabled = false;
  if (btn) btn.disabled = false;
  inp.placeholder = ready ? 'Say something... or /image' : 'LLM starting — you can type, reply may be delayed…';
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

// Cancel only the chat stream — leaves image generation running.
function _interruptChat() {
  _resetGroupAudioState();
  _stopTts();
  _stopGroupSpeech();
  if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
  if (chatAbort) { chatAbort.abort(); chatAbort = null; }
}

// Full stop: cancel chat AND image generation (Stop button, explicit new image, reroll).
async function interrupt(reason) {
  if (reason === 'user' && _demoMode) { _demoSkip = true; }
  _interruptChat();
  if (_pendingRetryAbort) { _pendingRetryAbort.abort(); _pendingRetryAbort = null; }
  _retryGen++;
  const hadImgInFlight = !!imgAbort;
  if (imgAbort) {
    console.log('Aborting media generation:', reason);
    imgAbort.abort();
    imgAbort = null;
    _imgGenId++;  // invalidate any pending isForeground() from the aborted request
  }
  stopProgress();
  enableAll();
  // Only tell the server to cancel Forge if something was actually running.
  // Calling /interrupt unnecessarily sets _gen_cancel and kills the next gen.
  if (hadImgInFlight) {
    await fetch('/interrupt', { method: 'POST' }).catch(() => {});
  }
}

function doImage() {
  const extra = document.getElementById('inp').value.trim();
  document.getElementById('inp').value = '';
  triggerMedia(extra);
}

let progressTimer = null, _lastPct = 0, _passCount = 0, _inFinishing = false, _passWasHigh = false, _passDropped = false;
function startProgress() {
  stopProgress();
  _lastPct = 0;
  _passCount = 0;
  _inFinishing = false;
  _passWasHigh = false;
  _passDropped = false;
  progressTimer = setInterval(async () => {
    try {
      const r = await fetch('/progress');
      const d = await r.json();
      const pct = Math.round((d.progress || 0) * 100);
      const fill   = document.getElementById('img-pb');
      const status = document.getElementById('img-status');
      if (fill) fill.style.width = pct + '%';
      if (status) {
        const st       = d.state || {};
        const step     = st.sampling_step  || 0;
        const total    = st.sampling_steps || 0;
        const eta      = d.eta_relative > 0.5 ? ` · ${Math.ceil(d.eta_relative)}s` : '';
        const textinfo = (d.textinfo || '').trim();
        // Pass detection: mark when sampling was well underway, detect the drop, then the restart
        if (pct >= 40) _passWasHigh = true;
        if (pct === 0 && _passWasHigh && !_passDropped) _passDropped = true;
        if (pct > 0 && _passCount === 0)  { _passCount = 1; _passWasHigh = false; }
        if (pct > 0 && _passDropped)       { _passCount++; _passDropped = false; _passWasHigh = false; }
        const passStr = _passCount > 1 ? ` · pass ${_passCount}` : '';
        if (pct > 0) {
          _inFinishing = false;
          const stepStr = total > 0 ? ` (${step}/${total})` : '';
          status.textContent = `Generating${passStr}… ${pct}%${stepStr}${eta}`;
        } else if (_lastPct > 0 || _inFinishing) {
          // Sampling done — now VAE decode or inter-pass gap
          _inFinishing = true;
          const detail = textinfo || (st.job ? `${st.job}` : 'decoding…');
          status.textContent = `Finishing${passStr} · ${detail}`;
        } else if (textinfo) {
          // Forge is busy before sampling starts (e.g. loading model checkpoint)
          status.textContent = textinfo;
        }
      }
      _lastPct = pct;
      if (d.current_image) {
        const ic = document.getElementById('ic');
        if (ic && !ic.querySelector('img.final')) {
          const slot = document.getElementById('img-preview-slot');
          if (slot) {
            const existing = slot.querySelector('img.preview');
            if (!existing) {
              slot.innerHTML = `<img class="preview" src="data:image/png;base64,${d.current_image}" style="max-width:100%;max-height:100%;opacity:0.85">`;
            } else {
              existing.src = `data:image/png;base64,${d.current_image}`;
            }
          } else {
            const existing = ic.querySelector('img.preview');
            if (!existing) {
              ic.innerHTML = `<img class="preview" src="data:image/png;base64,${d.current_image}" style="width:100%;opacity:0.85">`;
            } else {
              existing.src = `data:image/png;base64,${d.current_image}`;
            }
          }
        }
      }
    } catch {}
  }, 400);
}
function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

async function triggerMedia(extra = '', auto = false) {
  if (!auto) await interrupt('new media request');  // auto: already interrupted at chat start
  const myAbort = new AbortController();
  imgAbort = myAbort;
  const myGenId = ++_imgGenId;
  // isForeground() returns true as long as no newer image request has started or been aborted
  const isForeground = () => _imgGenId === myGenId;

  disableAll();

  if (extra && !auto) addMsg('user', 'You', extra);

  if (isForeground()) {
    document.getElementById('ih').textContent = 'Generated Scene';
    document.getElementById('ic').innerHTML =
      '<div id="img-progress-wrap" style="position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center">' +
        '<div id="img-preview-slot" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden"></div>' +
        '<div id="img-progress-overlay" style="position:absolute;bottom:0;left:0;right:0;padding:.3rem .5rem;background:rgba(0,0,0,0.55);z-index:2">' +
          '<div class="ph gen" id="img-status" style="margin:0 0 .25rem">Analyzing scene...</div>' +
          '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>' +
        '</div>' +
      '</div>';
    document.getElementById('pd-wrap').style.display = 'none';
    document.getElementById('pd').value = '';
    startProgress();
  }

  try {
    const res = await fetch('/image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ extra }),
      signal: myAbort.signal,
    });
    const d = await res.json();
    if (d.url) {
      await new Promise(resolve => {
        const img = new Image();
        img.onload = img.onerror = resolve;
        img.src = d.url;
      });
      saveImg(d.url, d.sd_prompt);  // always save to history
      if (isForeground()) {
        stopProgress();
        document.getElementById('ic').innerHTML = `<img src="${d.url}" class="final" onclick="openFullscreen(this.src)" title="Click to fullscreen">`;
        setPrompt(d.sd_prompt);
        _syncSeedBtn(d.pinned, d.seed);
        const rerollBtn = document.getElementById('reroll-btn');
        if (rerollBtn) rerollBtn.disabled = false;
      }
    } else if (isForeground()) {
      document.getElementById('ic').innerHTML = d.error
        ? `<div class="ph">${d.error}</div>`
        : '<div class="ph">No output generated.</div>';
    }
  } catch (e) {
    if (isForeground()) {
      console.error('Image error:', e);
      document.getElementById('ic').innerHTML = `<div class="ph">Image error: ${e.message || 'Unknown error'}. Check console.</div>`;
    }
  }
  if (isForeground()) {
    stopProgress();
    imgAbort = null;
    enableAll();
    document.getElementById('inp').focus();
  }
}

async function loadModels() {
  try {
    const res = await fetch('/models');
    const d = await res.json();
    const sel = document.getElementById('model-select');
    sel.innerHTML = `<option value="" disabled selected>Model</option>` +
      d.models.map(m => {
        const size = m.size_gb ? `${m.size_gb}GB  ` : '';
        return `<option value="${m.path}">${size}${m.name}</option>`;
      }).join('');
    if (d.current) {
      const match = d.models.find(m => m.path === d.current || m.name === d.current || d.current.includes(m.name));
      if (match) sel.value = match.path;
    }
  } catch (e) { console.warn('Could not load models:', e); }
}

async function loadSDModels() {
  const sel = document.getElementById('sd-model-select');
  if (!sel) return;
  try {
    const res = await fetch('/sd-models');
    const d = await res.json();
    if (d.error || !d.models.length) {
      sel.innerHTML = `<option value="" disabled selected>SD Model (unavailable)</option>`;
      return;
    }
    sel.innerHTML = `<option value="" disabled>SD Checkpoint</option>` +
      d.models.map(m =>
        `<option value="${m.title}" ${m.title === d.current ? 'selected' : ''}>${m.name}</option>`
      ).join('');
  } catch (e) {
    sel.innerHTML = `<option value="" disabled selected>SD Model (error)</option>`;
    console.warn('Could not load SD models:', e);
  }
}

async function switchSDModel(sel) {
  const title = sel.value;
  const name = sel.options[sel.selectedIndex].text;
  sel.disabled = true;
  const tid = addMsg('alice', charName, `<span class="gen">Loading SD checkpoint: ${name}…</span>`);
  try {
    const res = await fetch('/sd-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    const d = await res.json();
    updMsg(tid, d.error
      ? `<em style="color:#c08080">SD model error: ${d.error}</em>`
      : `SD checkpoint: ${name}.`);
  } catch (e) {
    updMsg(tid, `<em style="color:#c08080">SD model switch failed: ${e.message}</em>`);
  }
  sel.disabled = false;
}

async function loadPacks() {
  try {
    const res = await fetch('/persona-packs');
    const d = await res.json();
    const sel = document.getElementById('pack-select');
    if (!sel) return;
    sel.innerHTML = `<option value="" disabled selected>Packs</option>` +
      d.packs.map(p => `<option value="${p}">${p}</option>`).join('');
  } catch (e) { console.warn('Could not load persona packs:', e); }
}

async function switchPack(name) {
  const sel = document.getElementById('pack-select');
  sel.disabled = true;
  try {
    const res = await fetch('/persona-pack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (res.ok) {
      await loadPersonas();
      // Also update the reset persona select
      const resetSel = document.getElementById('reset-persona-select');
      if (resetSel) loadResetPersonas();
    }
  } catch (e) { console.error('Failed to switch pack:', e); }
  sel.disabled = false;
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
loadSDModels();
loadPacks();

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
  const [persRes, infoRes] = await Promise.all([fetch('/personas'), fetch('/info')]);
  const d    = await persRes.json();
  const info = await infoRes.json();
  const sel = document.getElementById('persona-select');
  sel.innerHTML = d.personas.map(p => `<option value="${p.name}">${p.label || p.name}</option>`).join('');
  d.personas.forEach(p => { _personaFontKeys[p.name] = p.font_key; });
  // Sync dropdown to whatever persona the server is currently on
  if (info.active_persona && sel.querySelector(`option[value="${info.active_persona}"]`)) {
    sel.value = info.active_persona;
  }
  if (sel.value) { _activePersona = sel.value; _applyPersonaFont(sel.value); }
  const resetSel = document.getElementById('reset-persona-select');
  if (resetSel) {
    resetSel.innerHTML =
      '<option value="" disabled selected>Reset…</option>' +
      '<option value="__all__">All personas</option>' +
      d.personas.map(p => `<option value="${p.name}">${p.label || p.name}</option>`).join('');
  }
}

async function switchPersona(name, { reChat = true } = {}) {
  console.log('[persona] switching to:', name);
  let r;
  try {
    r = await fetch(`/persona/${encodeURIComponent(name)}`, { method: 'POST' });
  } catch (e) {
    console.error('[persona] network error switching to', name, e);
    return;
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    console.error('[persona] switch failed', r.status, body.error || r.statusText, '— name:', name);
    return;
  }
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
  const _gsid = addMsg('alice', charName, entranceLine());
  fetchGreeting(_gsid);
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

async function loadQuickState() {
  try {
    const r = await fetch('/settings');
    const d = await r.json();
    const btn = document.getElementById('quick-btn');
    if (btn) btn.classList.toggle('active', !!d.quick_image);
  } catch {}
}

async function toggleQuick() {
  const btn = document.getElementById('quick-btn');
  const next = btn ? !btn.classList.contains('active') : true;
  if (btn) btn.classList.toggle('active', next);
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ quick_image: next }),
  });
}

loadQuickState();

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
  _retryGen++;
  if (_pendingRetryAbort) { _pendingRetryAbort.abort(); _pendingRetryAbort = null; }
  _interruptChat();  // stop previous chat only — image gen continues
  const tid = addMsg('alice', charName, '<span class="gen dots">thinking</span>');
  document.getElementById('pd').value = '';
  document.getElementById('thinking-bar').style.display = 'block';
  chatAbort = new AbortController();
  disableAll();
  let reply = '', autoImage = false, scheduleRetry = false;
  let _earlyTtsText = '';  // text sent to TTS early; '' = not yet started
  let _ttsBuf = '';        // accumulates deltas for sentence-boundary detection
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
        if (data.status) { updMsg(tid, `<em style="color:#888">${data.status}</em>`); }
        if (data.error) { document.getElementById('thinking-bar').style.display='none'; updMsg(tid, '<em style="color:#c08080">' + data.error + '</em>'); }
        if (data.delta) {
          document.getElementById('thinking-bar').style.display='none';
          reply += data.delta;
          updMsg(tid, reply);
          // Start TTS early on first sentence boundary
          if (!_earlyTtsText && !muted) {
            _ttsBuf += data.delta;
            const _sEnd = _ttsBuf.search(/[.!?]["']?\s/);
            if (_sEnd !== -1 || _ttsBuf.length > 300) {
              // Slice at the punctuation mark (not beyond) so no partial next-word leaks in
              _earlyTtsText = (_sEnd !== -1 ? _ttsBuf.slice(0, _sEnd + 1) : _ttsBuf).trim();
              speak(_earlyTtsText);
            }
          }
        }
        if (data.done)  { reply = data.reply; updMsg(tid, reply); autoImage = data.auto_image; if (data.retry) scheduleRetry = true; }
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
  if (scheduleRetry) {
    _pendingRetryAbort = new AbortController();
    const sig = _pendingRetryAbort.signal;
    const myGen = _retryGen;
    (async () => {
      for (let i = 0; i < 90 && !sig.aborted && _retryGen === myGen; i++) {
        await new Promise(r => setTimeout(r, 2000));
        if (sig.aborted || _retryGen !== myGen) return;
        try { const d = await (await fetch('/info')).json(); if (d.llm_ready) { _chatWith(msg, { forceImage }); return; } } catch {}
      }
    })();
    return;
  }
  if (reply) {
    if (autoImage || forceImage) triggerMedia('', true);
    if (!_earlyTtsText) {
      speak(reply);
    } else {
      // Chain remainder from post-processed reply (not raw _earlyTtsText length,
      // which diverges when parentheticals or boilerplate are stripped).
      const _rEnd = reply.search(/[.!?]["']?\s/);
      const remainder = _rEnd !== -1
        ? reply.slice(_rEnd + 1).trim()
        : (reply.length > _earlyTtsText.length ? reply.slice(_earlyTtsText.length).trim() : '');
      if (remainder) speakChain(remainder);
      lastReplyText = reply;
    }
  }
  loadInfo();
}

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  if (msg === '/export') { exportHistory(); return; }
  if (msg.startsWith('/image')) { await interrupt('new media request'); triggerMedia(msg.slice(6).trim()); return; }
  if (msg === '/auto-image') {
    const inp2 = document.getElementById('inp');
    if (_groupMode) {
      inp2.placeholder = 'Group chat images are manual only. Use the Image button or /image.';
      setTimeout(() => inp2.placeholder = 'Group chat... images are manual via button or /image', 2500);
      return;
    }
    const r = await fetch('/auto-image', { method: 'POST' });
    const d = await r.json();
    inp2.placeholder = `Auto-image ${d.auto_image ? 'ON' : 'OFF'}`;
    setTimeout(() => inp2.placeholder = 'Say something... or /image', 2500);
    return;
  }
  lastUserMsg = msg;
  if (_groupMode) {
    _interruptChat();  // stop previous chat only — image gen continues
    inp.focus();
    await _sendGroupMsg(msg);
    inp.focus();
    return;
  }
  addMsg('user', 'You', msg);
  inp.focus();
  if (_demoMode) {
    // Pause demo and abort its current turn so our message goes through cleanly
    _demoPaused = true;
    _demoSkip = true;
    _stopTts();
    if (chatAbort) { chatAbort.abort(); chatAbort = null; }
    if (imgAbort)  { imgAbort.abort();  imgAbort  = null; _imgGenId++; }
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
  fetch('/demo/start', { method: 'POST' }).catch(() => {});
  demoLoop();
}

function stopDemo() {
  _demoMode = false;
  _demoPaused = false;
  if (_demoTimer) { clearTimeout(_demoTimer); _demoTimer = null; }
  _updateDemoBtn();
  fetch('/demo/stop', { method: 'POST' }).catch(() => {});
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

let DEMO_USER_NAME  = 'User';
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

async function fetchGreeting(targetId) {
  try {
    const r = await fetch('/persona/greeting');
    const d = await r.json();
    if (!d.greeting) return;
    if (targetId) {
      updMsg(targetId, d.greeting);
    } else {
      const el = document.querySelector('#msgs .msg.alice');
      if (el) {
        const sndr = el.querySelector('.sndr');
        if (sndr) el.innerHTML = sndr.outerHTML + d.greeting;
        el.dataset.entranceSet = '1';  // prevent loadInfo() from overwriting with a random line
      }
    }
  } catch (e) {}
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

// ── Group Chat ─────────────────────────────────────────────────────────────────
let _groupMode      = false;
let _groupPersonas  = {};      // key → {name, tts, font_key, color}
let _groupEvents    = null;    // EventSource for /group/events
let _groupSelected  = new Set();

// Color pool — assigned dynamically to whatever personas are in the group
const _GROUP_COLORS = [
  '#c084a0', '#4fc3f7', '#daa520', '#7cb984', '#e8b86a',
  '#b39ddb', '#f48fb1', '#80cbc4', '#ffcc80', '#ce93d8',
  '#90caf9', '#a5d6a7',
];

async function toggleGroupMode() {
  if (_groupMode) await _stopGroupMode();
  else            await _startGroupMode();
}

async function _startGroupMode() {
  _groupMode = true;
  document.getElementById('group-btn').classList.add('demo-active');
  document.getElementById('group-panel').style.display = 'flex';
  document.getElementById('inp').placeholder = 'Group chat... images are manual via button or /image';

  // Build persona chip list
  const res = await fetch('/personas');
  const d   = await res.json();
  _groupSelected = new Set(d.personas.map(p => p.name));

  const container = document.getElementById('group-personas');
  container.innerHTML = d.personas.map(p =>
    `<span class="group-persona-chip checked" data-key="${p.name}" onclick="_toggleGroupChip(this)">
       ${p.label || p.name}
     </span>`
  ).join('');

  await _applyGroupPersonas();
}

async function _stopGroupMode() {
  _groupMode = false;
  _resetGroupAudioState();
  document.getElementById('group-btn').classList.remove('demo-active');
  document.getElementById('group-panel').style.display = 'none';
  document.getElementById('group-to-wrap').style.display = 'none';
  document.getElementById('inp').placeholder = 'Say something... or /image';
  if (_groupEvents) { _groupEvents.close(); _groupEvents = null; }
  await fetch('/group/stop', { method: 'POST' }).catch(() => {});
  _addGroupSystemMsg('Group chat ended.');
}

function _toggleGroupChip(el) {
  const key = el.dataset.key;
  if (_groupSelected.has(key)) {
    if (_groupSelected.size <= 1) return; // keep at least one
    _groupSelected.delete(key);
    el.classList.remove('checked');
  } else {
    _groupSelected.add(key);
    el.classList.add('checked');
  }
  _applyGroupPersonas();
}

async function _applyGroupPersonas() {
  _resetGroupAudioState();
  const r = await fetch('/group/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ personas: [..._groupSelected] }),
  });
  const d = await r.json();

  // Refresh status for TTS configs + populate To: dropdown
  const sr = await fetch('/group/status');
  const sd = await sr.json();
  _groupPersonas = {};
  sd.personas.forEach((p, i) => {
    p.color = _GROUP_COLORS[i % _GROUP_COLORS.length];
    _groupPersonas[p.key] = p;
  });

  const toSel = document.getElementById('group-to');
  toSel.innerHTML = '<option value="all">All</option>' +
    sd.personas.map(p => `<option value="${p.key}">${p.name}</option>`).join('');

  document.getElementById('group-to-wrap').style.display = 'flex';

  // (Re)connect SSE for async chatter
  if (_groupEvents) _groupEvents.close();
  _groupEvents = new EventSource('/group/events');
  _groupEvents.onmessage = _onGroupEvent;

  const names = sd.personas.map(p => p.name).join(', ');
  _addGroupSystemMsg(`Group: ${names}`);
}

function _onGroupEvent(e) {
  let data;
  try { data = JSON.parse(e.data); } catch { return; }
  if (data.ping) return;

  if (data.type === 'system') {
    _addGroupSystemMsg(data.content);
    return;
  }

  if (data.type === 'chatter') {
    const toHint = (data.to && data.to !== 'all') ? data.to : '';
    _addGroupMsg(data.persona, data.sender, data.content, toHint, true);
    const tts = data.tts || {};
    _speakGroup(data.persona, data.content, tts);
  }
}
function _addGroupSystemMsg(text) {
  const d = document.createElement('div');
  d.className = 'msg group-system';
  d.textContent = text;
  const c = document.getElementById('msgs');
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function _addGroupMsg(personaKey, senderName, html, toHint, isChatter) {
  const id = 'm' + (mid++);
  const d = document.createElement('div');
  d.className = 'msg alice group-msg';
  d.id = id;

  // Dynamic color — assigned from pool when group started, no hardcoded names
  const color = _groupPersonas[personaKey]?.color || _GROUP_COLORS[0];
  d.style.borderLeftColor = color;

  // Apply the persona's own font (falls back to default)
  const fontKey = _groupPersonas[personaKey]?.font_key;
  const f = _PERSONA_FONTS[fontKey] || _DEFAULT_FONT;
  d.style.fontFamily     = f.family;
  d.style.fontStyle      = f.style;
  d.style.fontSize       = f.size;
  d.style.fontWeight     = f.weight;
  d.style.letterSpacing  = f.spacing;

  const toSpan = toHint   ? `<span class="group-to-hint">→ ${toHint}</span>` : '';
  const badge  = isChatter ? `<span class="group-chatter-badge">✦</span>` : '';
  d.innerHTML = `<div class="sndr" style="color:${color}">${senderName}${toSpan}${badge}</div>${html}`;
  const c = document.getElementById('msgs');
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
  return id;
}

async function _sendGroupMsg(msg) {
  const to = document.getElementById('group-to').value;
  addMsg('user', 'You', msg);
  document.getElementById('thinking-bar').style.display = 'block';
  disableAll();

  try {
    const res = await fetch('/group/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, to: to }),
    });
    
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let currentTids = {}; // personaKey -> messageId

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const d = JSON.parse(line.slice(6));
        
        if (d.error) {
          _addGroupSystemMsg(`Error (${d.sender}): ${d.error}`);
          continue;
        }

        if (d.typing) {
          document.getElementById('thinking-bar').style.display = 'none';
          const tid = _addGroupMsg(d.persona, d.sender, '<span class="gen dots">thinking</span>', (to !== 'all' && to === d.persona) ? '' : to, false);
          currentTids[d.persona] = { id: tid, content: '' };
        }

        if (d.delta && currentTids[d.persona]) {
          currentTids[d.persona].content += d.delta;
          const e = document.getElementById(currentTids[d.persona].id);
          if (e) {
            const color = _groupPersonas[d.persona]?.color || _GROUP_COLORS[0];
            const toHint = (to !== 'all' && (to === d.persona || to === d.sender)) ? '' : to;
            const toSpan = toHint ? `<span class="group-to-hint">→ ${toHint}</span>` : '';
            e.innerHTML = `<div class="sndr" style="color:${color}">${d.sender}${toSpan}</div>${renderMd(currentTids[d.persona].content)}`;
            document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight;
          }
        }

        if (d.done && currentTids[d.persona]) {
          const tid = currentTids[d.persona].id;
          const reply = d.reply;
          const e = document.getElementById(tid);
          if (e) {
            const color = _groupPersonas[d.persona]?.color || _GROUP_COLORS[0];
            const toHint = (to !== 'all' && (to === d.persona || to === d.sender)) ? '' : to;
            const toSpan = toHint ? `<span class="group-to-hint">→ ${toHint}</span>` : '';
            e.innerHTML = `<div class="sndr" style="color:${color}">${d.sender}${toSpan}<span class="group-chatter-badge">✦</span></div>${renderMd(reply)}`;
          }
          if (d.tts) _speakGroup(d.persona, reply, d.tts);
          delete currentTids[d.persona];
        }
      }
    }
  } catch (e) {
    console.error('[group chat]', e);
    _addGroupSystemMsg('Error during group chat.');
  } finally {
    document.getElementById('thinking-bar').style.display = 'none';
    enableAll();
  }
}

async function _waitForGroupTts(gen, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  // Wait for speak() to schedule at least one audio chunk
  const p1 = Date.now() + 8000;
  while (Date.now() < p1) {
    if (_ttsGen !== gen || !_groupMode) return;
    if (_audioCtx && _nextStart > (_audioCtx.currentTime + 0.2)) break;
    await new Promise(r => setTimeout(r, 100));
  }
  if (!_audioCtx || _nextStart <= 0.1) return;
  // Wait for scheduled audio to finish
  while (Date.now() < deadline) {
    if (_ttsGen !== gen || !_groupMode) return;
    if (_audioCtx.currentTime >= _nextStart - 0.1) return;
    await new Promise(r => setTimeout(r, 200));
  }
}

async function clearHistory() {
  await fetch('/history', { method: 'DELETE' });
  document.getElementById('msgs').innerHTML = '';
  const _gcid = addMsg('alice', charName, entranceLine());
  fetchGreeting(_gcid);
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
  lastReplyText = '';
  _stopTts();
  _lastChunks = [];
  document.getElementById('resay-btn').disabled = true;
}

async function resetPersona(selectEl) {
  const resetSel = selectEl || document.getElementById('reset-persona-select');
  const name = resetSel ? resetSel.value : _activePersona;
  if (!name) return;
  const label = name === '__all__' ? 'ALL personas' : name;
  if (!confirm(`Reset ${label}?\n\nThis clears chat history and relationship memory.`)) {
    resetSel.value = '';
    return;
  }
  if (name === '__all__') {
    const opts = Array.from(resetSel.options).filter(o => o.value && o.value !== '__all__');
    await Promise.all(opts.map(o => fetch(`/persona/${encodeURIComponent(o.value)}/reset`, { method: 'DELETE' })));
  } else {
    await fetch(`/persona/${encodeURIComponent(name)}/reset`, { method: 'DELETE' });
  }
  resetSel.value = '';
  document.getElementById('msgs').innerHTML = '';
  const _grid = addMsg('alice', charName, entranceLine());
  fetchGreeting(_grid);
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd-wrap').style.display = 'none';
  document.getElementById('pd').value = '';
  lastReplyText = '';
  _stopTts();
  _lastChunks = [];
  document.getElementById('resay-btn').disabled = true;
}
