// ── SD Page — standalone image generation console ──────────────────────────────
let sdAbort = null;

async function checkForge() {
  try {
    const r = await fetch('/info');
    const d = await r.json();
    const el = document.getElementById('sd-status');
    if (d.forge_ready) {
      el.textContent = 'Forge ready';
      el.style.color = 'var(--green)';
    } else {
      el.textContent = 'Forge starting…';
      el.style.color = 'var(--yellow)';
      setTimeout(checkForge, 2000);
    }
  } catch (e) {
    document.getElementById('sd-status').textContent = 'Forge unavailable';
    document.getElementById('sd-status').style.color = 'var(--red)';
  }
}
checkForge();

async function generateSD() {
  const prompt = document.getElementById('sd-prompt').value.trim();
  if (!prompt) return;
  if (sdAbort) { sdAbort.abort(); }
  sdAbort = new AbortController();

  const steps  = parseInt(document.getElementById('sd-steps').value) || 25;
  const width  = parseInt(document.getElementById('sd-width').value) || 512;
  const height = parseInt(document.getElementById('sd-height').value) || 512;
  const seed   = parseInt(document.getElementById('sd-seed').value) || -1;

  const btn = document.getElementById('sd-gen-btn');
  btn.disabled = true;
  btn.textContent = 'Generating…';

  document.getElementById('sd-progress').style.display = 'block';
  document.getElementById('sd-status-text').textContent = 'Sending to Forge…';
  document.getElementById('ic').innerHTML = '<div class="ph">Generating…</div>';

  let progressTimer = null;
  function startSDProgress() {
    progressTimer = setInterval(async () => {
      try {
        const r = await fetch('/progress');
        const d = await r.json();
        const pct = Math.round((d.progress || 0) * 100);
        const fill = document.getElementById('sd-progress-fill');
        const status = document.getElementById('sd-status-text');
        if (fill) fill.style.width = pct + '%';
        if (status) {
          const st = d.state || {};
          const step = st.sampling_step || 0;
          const total = st.sampling_steps || 0;
          const textinfo = (d.textinfo || '').trim();
          if (pct > 0) {
            const stepStr = total > 0 ? ` (${step}/${total})` : '';
            status.textContent = `Generating… ${pct}%${stepStr}`;
          } else if (textinfo) {
            status.textContent = textinfo;
          } else {
            status.textContent = 'Finishing…';
          }
        }
      } catch {}
    }, 500);
  }
  startSDProgress();

  try {
    const res = await fetch('/sd-generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, steps, width, height, seed }),
      signal: sdAbort.signal,
    });
    const d = await res.json();

    if (d.url) {
      document.getElementById('ic').innerHTML =
        `<img src="${d.url}" class="final" style="max-width:100%;max-height:100%;object-fit:contain">`;
      document.getElementById('sd-prompt').value = '';
      document.getElementById('sd-seed').value = d.seed;
      const msgs = document.getElementById('msgs');
      if (msgs) {
        const d2 = document.createElement('div');
        d2.className = 'msg alice';
        d2.innerHTML = `<div class="sndr">Alice</div>Generated: ${prompt.slice(0, 60)}…`;
        msgs.appendChild(d2);
        msgs.scrollTop = msgs.scrollHeight;
      }
    } else {
      document.getElementById('ic').innerHTML =
        `<div class="ph" style="color:#c08080">${d.error || 'Generation failed'}</div>`;
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      document.getElementById('ic').innerHTML =
        `<div class="ph" style="color:#c08080">Error: ${e.message}</div>`;
    }
  }

  btn.disabled = false;
  btn.textContent = 'Generate';
  document.getElementById('sd-progress').style.display = 'none';
}
