// ── Image Generation ────────────────────────────────────────────────────────
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
    if (!_groupMode) await _startGroupMode();
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
  const ic = document.getElementById('ic');
  if (ic) ic.innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  const pd = document.getElementById('pd');
  if (pd) pd.value = '';
}

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

let progressTimer = null, _lastPct = 0, _passCount = 0, _inFinishing = false;
function startProgress() {
  stopProgress();
  _lastPct = 0;
  _passCount = 0;
  _inFinishing = false;
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
        if (pct > 0 && _lastPct === 0 && _passCount > 0) _passCount++;
        if (pct > 0 && _passCount === 0) _passCount = 1;
        const passStr = _passCount > 1 ? ` · pass ${_passCount}` : '';
        if (pct > 0) {
          _inFinishing = false;
          const stepStr = total > 0 ? ` (${step}/${total})` : '';
          status.textContent = `Generating${passStr}… ${pct}%${stepStr}${eta}`;
        } else if (_lastPct > 0 || _inFinishing) {
          _inFinishing = true;
          const detail = textinfo || (st.job ? `${st.job}` : 'decoding…');
          status.textContent = `Finishing${passStr} · ${detail}`;
        } else if (textinfo) {
          status.textContent = textinfo;
        }
      }
      // Show live preview as soon as Forge has a current_image — user sees the
      // image forming instead of waiting for the full API response.
      if (d.current_image) {
        const ic = document.getElementById('ic');
        if (ic && !ic.querySelector('img.final')) {
          const existing = ic.querySelector('img.preview');
          if (!existing) {
            ic.innerHTML = `<img class="preview" src="data:image/png;base64,${d.current_image}" style="width:100%;opacity:0.85">`;
          } else {
            existing.src = `data:image/png;base64,${d.current_image}`;
          }
        }
      }
      _lastPct = pct;
    } catch {}
  }, 800);
}
function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

async function triggerMedia(extra = '', auto = false) {
  if (!auto) await interrupt('new media request');
  const myAbort = new AbortController();
  imgAbort = myAbort;
  disableAll();
  if (extra && !auto) addMsg('user', 'You', extra);
  const isForeground = () => imgAbort === myAbort;
  if (isForeground()) {
    document.getElementById('ih').textContent = 'Generated Scene';
    document.getElementById('ic').innerHTML =
      '<div id="img-progress-wrap">' +
        '<div class="ph gen" id="img-status">Analyzing scene...</div>' +
        '<div class="img-progress-track"><div class="img-progress-fill" id="img-pb"></div></div>' +
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
      saveImg(d.url, d.sd_prompt);
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
    const inp = document.getElementById('inp');
    if (inp) inp.focus();
  }
}

function doImage() {
  const inp = document.getElementById('inp');
  if (!inp) return;
  const extra = inp.value.trim();
  inp.value = '';
  triggerMedia(extra);
}

async function regenFromPrompt() {
  const promptEl = document.getElementById('pd');
  if (!promptEl) return;
  const prompt = promptEl.value.trim();
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

async function loadNegative() {
  try {
    const r = await fetch('/negative');
    const d = await r.json();
    const el = document.getElementById('neg-prompt');
    if (el && d.negative) el.textContent = d.negative;
  } catch (e) { console.warn('Could not load negative prompt:', e); }
}

function _syncSeedBtn(pinned, seed) {
  const btn = document.getElementById('seed-btn');
  if (!btn) return;
  btn.classList.toggle('pinned', !!pinned);
  btn.title = pinned ? `Seed ${seed} pinned — face consistent` : 'Pin seed for face consistency';
}

async function toggleSeedPin() {
  const btn = document.getElementById('seed-btn');
  if (!btn) return;
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
