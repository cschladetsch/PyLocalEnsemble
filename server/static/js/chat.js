// ── Chat & History & Personas ────────────────────────────────────────────────
async function _chatWith(msg, { forceImage = false } = {}) {
  _interruptChat();
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
        if (data.status) { updMsg(tid, `<em style="color:#888">${data.status}</em>`); }
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
    _interruptChat();
    inp.focus();
    await _sendGroupMsg(msg);
    inp.focus();
    return;
  }
  addMsg('user', 'You', msg);
  inp.focus();
  if (_demoMode) {
    _demoPaused = true;
    _demoSkip = true;
    _stopTts();
    if (chatAbort) { chatAbort.abort(); chatAbort = null; }
    if (imgAbort)  { imgAbort.abort();  imgAbort  = null; }
  }
  await _chatWith(msg);
  if (_demoMode) _demoPaused = false;
  inp.focus();
}

async function loadModels() {
  try {
    const res = await fetch('/models');
    const d = await res.json();
    const sel = document.getElementById('model-select');
    if (sel) sel.innerHTML = `<option value="" disabled selected>Model</option>` +
      d.models.map(m => `<option value="${m.path}">${m.name}</option>`).join('');
  } catch (e) { console.warn('Could not load models:', e); }
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
  if (sel) sel.disabled = true;
  try {
    const res = await fetch('/persona-pack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (res.ok) {
      await loadPersonas();
      const resetSel = document.getElementById('reset-persona-select');
      if (resetSel) loadResetPersonas();
    }
  } catch (e) { console.error('Failed to switch pack:', e); }
  if (sel) sel.disabled = false;
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

async function loadPersonas() {
  const [persRes, infoRes] = await Promise.all([fetch('/personas'), fetch('/info')]);
  const d    = await persRes.json();
  const info = await infoRes.json();
  const sel = document.getElementById('persona-select');
  if (sel) {
    sel.innerHTML = d.personas.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
    d.personas.forEach(p => { _personaFontKeys[p.name] = p.font_key; });
    if (info.active_persona && sel.querySelector(`option[value="${info.active_persona}"]`)) {
      sel.value = info.active_persona;
    }
    if (sel.value) { _activePersona = sel.value; _applyPersonaFont(sel.value); }
  }
  const resetSel = document.getElementById('reset-persona-select');
  if (resetSel) {
    resetSel.innerHTML =
      '<option value="" disabled selected>Reset…</option>' +
      '<option value="__all__">All personas</option>' +
      d.personas.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  }
}

async function switchPersona(name, { reChat = true } = {}) {
  console.log('[persona] switching to:', name);
  try {
    const r = await fetch(`/persona/${encodeURIComponent(name)}`, { method: 'POST' });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      console.error('[persona] switch failed', r.status, body.error || r.statusText, '— name:', name);
      return;
    }
    _activePersona = name;
    _applyPersonaFont(name);
    await loadInfo();
    const div = document.createElement('div');
    div.className = 'persona-switch-divider';
    div.textContent = `— ${charName} —`;
    const msgs = document.getElementById('msgs');
    if (msgs) {
      msgs.appendChild(div);
      msgs.scrollTop = msgs.scrollHeight;
    }
    const pdWrap = document.getElementById('pd-wrap');
    if (pdWrap) pdWrap.style.display = 'none';
    const rerollBtn = document.getElementById('reroll-btn');
    if (rerollBtn) rerollBtn.disabled = true;
    await loadVoices();
    loadNegative();
    if (reChat && lastUserMsg) _chatWith(lastUserMsg);
  } catch (e) {
    console.error('[persona] network error switching to', name, e);
  }
}

async function clearHistory() {
  await fetch('/history', { method: 'DELETE' });
  const msgs = document.getElementById('msgs');
  if (msgs) msgs.innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>${entranceLine()}</div>`;
  const ic = document.getElementById('ic');
  if (ic) ic.innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  const pdWrap = document.getElementById('pd-wrap');
  if (pdWrap) pdWrap.style.display = 'none';
  const pd = document.getElementById('pd');
  if (pd) pd.value = '';
  lastReplyText = '';
  _stopTts();
  _lastChunks = [];
  const resayBtn = document.getElementById('resay-btn');
  if (resayBtn) resayBtn.disabled = true;
}

async function resetPersona(selectEl) {
  const resetSel = selectEl || document.getElementById('reset-persona-select');
  const name = resetSel ? resetSel.value : _activePersona;
  if (!name) return;
  const label = name === '__all__' ? 'ALL personas' : name;
  if (!confirm(`Reset ${label}?\n\nThis clears chat history and relationship memory.`)) {
    if (resetSel) resetSel.value = '';
    return;
  }
  if (name === '__all__') {
    const opts = Array.from(resetSel.options).filter(o => o.value && o.value !== '__all__');
    await Promise.all(opts.map(o => fetch(`/persona/${encodeURIComponent(o.value)}/reset`, { method: 'DELETE' })));
  } else {
    await fetch(`/persona/${encodeURIComponent(name)}/reset`, { method: 'DELETE' });
  }
  if (resetSel) resetSel.value = '';
  const msgs = document.getElementById('msgs');
  if (msgs) msgs.innerHTML = `<div class="msg alice"><div class="sndr">${charName}</div>${entranceLine()}</div>`;
  const ic = document.getElementById('ic');
  if (ic) ic.innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  const pdWrap = document.getElementById('pd-wrap');
  if (pdWrap) pdWrap.style.display = 'none';
  const pd = document.getElementById('pd');
  if (pd) pd.value = '';
  lastReplyText = '';
  _stopTts();
  _lastChunks = [];
  const resayBtn = document.getElementById('resay-btn');
  if (resayBtn) resayBtn.disabled = true;
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
