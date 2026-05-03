let _loaded = false;
let _saveTimer = null;

// ── Helpers ────────────────────────────────────────────────────────────────────
function _sv(id, val)          { const e = document.getElementById(id); if (e) e.value = val; }
function _sval(id, val, fmt)   { const e = document.getElementById(id); if (e) e.textContent = fmt ? fmt(val) : val; }
function _check(id, val)       { const e = document.getElementById(id); if (e) e.checked = !!val; }
function _slide(id, valId, val, fmt) { _sv(id, val); _sval(valId, val, fmt); }
function _num(id)  { return +document.getElementById(id).value; }
function _bool(id) { return document.getElementById(id).checked; }
function _str(id)  { return document.getElementById(id).value; }

// ── Toast ──────────────────────────────────────────────────────────────────────
function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast ' + (ok ? 'toast-ok' : 'toast-err') + ' show';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1800);
}

// ── Auto-save ──────────────────────────────────────────────────────────────────
function _scheduleSave() {
  if (!_loaded) return;
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(_saveAll, 500);
}

async function _saveAll() {
  try {
    const r = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        quick_image:         _bool('s-quick_image'),
        vram_swap_for_image: _bool('s-vram_swap'),
        llm_params: {
          max_tokens:        _num('s-max_tokens'),
          temperature:       _num('s-temperature'),
          top_p:             _num('s-top_p'),
          repeat_penalty:    _num('s-repeat_penalty'),
          presence_penalty:  _num('s-presence_penalty'),
          frequency_penalty: _num('s-frequency_penalty'),
        },
        image: {
          steps:           _num('s-steps'),
          cfg_scale:       _num('s-cfg_scale'),
          width:           _num('s-width'),
          height:          _num('s-height'),
          sampler_name:    _str('s-sampler_name'),
          hires_fix:       _bool('s-hires_fix'),
          hires_scale:     _num('s-hires_scale'),
          hires_steps:     _num('s-hires_steps'),
          hires_denoising: _num('s-hires_denoising'),
          auto_pin_seed:   _bool('s-auto_pin_seed'),
          adetailer_face:  _bool('s-adetailer_face'),
        },
        tts: {
          voice: _str('s-voice'),
          speed: _num('s-speed'),
          pitch: _num('s-pitch'),
        },
        memory: {
          max_history: _num('s-max_history'),
          keep_recent: _num('s-keep_recent'),
          max_chars:   _num('s-max_chars'),
        },
        llama_server: {
          n_gpu_layers: _num('s-n_gpu_layers'),
          ctx_size:     _num('s-ctx_size'),
          threads:      _num('s-threads'),
          batch_size:   _num('s-batch_size'),
        },
      }),
    });
    if (r.ok) toast('Saved');
    else toast('Save failed', false);
  } catch (e) { toast('Error: ' + e.message, false); }
}

function _wireAutoSave() {
  const ids = [
    's-max_tokens', 's-temperature', 's-top_p', 's-repeat_penalty',
    's-presence_penalty', 's-frequency_penalty',
    's-steps', 's-cfg_scale', 's-width', 's-height', 's-sampler_name',
    's-hires_fix', 's-hires_scale', 's-hires_steps', 's-hires_denoising',
    's-quick_image', 's-vram_swap', 's-auto_pin_seed', 's-adetailer_face',
    's-voice', 's-speed', 's-pitch',
    's-max_history', 's-keep_recent', 's-max_chars',
    's-n_gpu_layers', 's-ctx_size', 's-threads', 's-batch_size',
  ];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input',  _scheduleSave);
    el.addEventListener('change', _scheduleSave);
  });
}

// ── Load settings ──────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const r = await fetch('/settings');
    const d = await r.json();

    const lp  = d.llm_params    || {};
    const img = d.image         || {};
    const tts = d.tts           || {};
    const mem = d.memory        || {};
    const srv = d.llama_server  || {};

    _slide('s-max_tokens',        'max-tokens-val',   lp.max_tokens        ?? 150,  v => v);
    _slide('s-temperature',       'temp-val',         lp.temperature       ?? 0.9,  v => (+v).toFixed(2));
    _slide('s-top_p',             'top-p-val',        lp.top_p             ?? 0.92, v => (+v).toFixed(2));
    _slide('s-repeat_penalty',    'rep-val',          lp.repeat_penalty    ?? 1.25, v => (+v).toFixed(2));
    _slide('s-presence_penalty',  'pres-val',         lp.presence_penalty  ?? 0.8,  v => (+v).toFixed(1));
    _slide('s-frequency_penalty', 'freq-val',         lp.frequency_penalty ?? 0.5,  v => (+v).toFixed(1));

    _slide('s-steps',           'img-steps-val',    img.steps           ?? 25,   v => v);
    _slide('s-cfg_scale',       'img-cfg-val',      img.cfg_scale       ?? 7,    v => (+v).toFixed(1));
    _slide('s-hires_scale',     'hires-scale-val',  img.hires_scale     ?? 1.5,  v => (+v).toFixed(2));
    _slide('s-hires_steps',     'hires-steps-val',  img.hires_steps     ?? 15,   v => v);
    _slide('s-hires_denoising', 'hires-denoise-val', img.hires_denoising ?? 0.45, v => (+v).toFixed(2));
    _sv('s-width',        img.width        ?? 512);
    _sv('s-height',       img.height       ?? 768);
    _sv('s-sampler_name', img.sampler_name ?? 'DPM++ SDE Karras');
    _check('s-hires_fix',      img.hires_fix      !== false);
    _check('s-auto_pin_seed',  img.auto_pin_seed  !== false);
    _check('s-adetailer_face', !!img.adetailer_face);
    _check('s-quick_image',    d.quick_image !== false);
    _check('s-vram_swap',      d.vram_swap_for_image !== false);

    _slide('s-speed', 'tts-speed-val', tts.speed ?? 0.78, v => (+v).toFixed(2));
    _slide('s-pitch', 'tts-pitch-val', tts.pitch ?? 0.94, v => (+v).toFixed(2));
    window._settingsTtsVoice = tts.voice || 'af_nicole';

    _slide('s-max_history', 'mem-max-val',   mem.max_history ?? 16,   v => v);
    _slide('s-keep_recent', 'mem-keep-val',  mem.keep_recent ?? 8,    v => v);
    _slide('s-max_chars',   'mem-chars-val', mem.max_chars   ?? 1500, v => v);

    _sv('s-n_gpu_layers', srv.n_gpu_layers ?? 33);
    _sv('s-ctx_size',     srv.ctx_size     ?? 4096);
    _sv('s-threads',      srv.threads      ?? 8);
    _sv('s-batch_size',   srv.batch_size   ?? 512);

  } catch (e) { console.error('loadSettings:', e); }
}

async function loadLLMModels() {
  try {
    const r = await fetch('/models');
    const d = await r.json();
    const sel = document.getElementById('s-model-select');
    if (!sel) return;
    sel.innerHTML = d.models.length
      ? d.models.map(m => {
          const size = m.size_gb ? `${m.size_gb}GB  ` : '';
          return `<option value="${m.path}">${size}${m.name}</option>`;
        }).join('')
      : '<option value="" disabled>No models found</option>';
    if (d.current) {
      const match = d.models.find(m =>
        m.path === d.current || m.name === d.current || d.current.includes(m.name)
      );
      if (match) sel.value = match.path;
    }
  } catch (e) { console.warn('loadLLMModels:', e); }
}

async function loadSDModels() {
  const sel = document.getElementById('s-sd-model-select');
  if (!sel) return;
  try {
    const r = await fetch('/sd-models');
    const d = await r.json();
    if (d.error || !d.models.length) {
      sel.innerHTML = '<option value="" disabled>SD unavailable</option>';
      return;
    }
    sel.innerHTML = d.models.map(m =>
      `<option value="${m.title}" ${m.title === d.current ? 'selected' : ''}>${m.name}</option>`
    ).join('');
  } catch (e) {
    const sel2 = document.getElementById('s-sd-model-select');
    if (sel2) sel2.innerHTML = '<option value="" disabled>SD error</option>';
  }
}

async function loadVoices() {
  try {
    const r = await fetch('/voices');
    const d = await r.json();
    const sel = document.getElementById('s-voice');
    if (!sel) return;
    const cur = window._settingsTtsVoice || d.current || '';
    sel.innerHTML = (d.voices || []).map(v =>
      `<option value="${v}" ${v === cur ? 'selected' : ''}>${v}</option>`
    ).join('');
  } catch (e) { console.warn('loadVoices:', e); }
}

// ── Model switches (server-side operations, not just config saves) ─────────────
async function switchLLMModel(sel) {
  const path = sel.value;
  const name = sel.options[sel.selectedIndex].text.trim();
  sel.disabled = true;
  toast(`Loading ${name}…`);
  try {
    const r = await fetch('/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const d = await r.json();
    toast(d.error ? d.error : `Switched to ${name}`, !d.error);
  } catch (e) { toast('Error: ' + e.message, false); }
  sel.disabled = false;
}

async function switchSDModelSettings(sel) {
  const title = sel.value;
  const name = sel.options[sel.selectedIndex].text.trim();
  sel.disabled = true;
  toast(`Loading ${name}…`);
  try {
    const r = await fetch('/sd-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    const d = await r.json();
    toast(d.error ? d.error : `Loaded ${name}`, !d.error);
  } catch (e) { toast('Error: ' + e.message, false); }
  sel.disabled = false;
}

// ── Init ───────────────────────────────────────────────────────────────────────
(async () => {
  await loadSettings();
  await loadVoices();
  _wireAutoSave();
  _loaded = true;
})();
loadLLMModels();
loadSDModels();
