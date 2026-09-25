// ── SD Page — standalone image generation console ──────────────────────────────
let sdAbort = null;

async function checkForge() {
  try {
    const r = await fetch('/sd-info');
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

// ── Video generation ─────────────────────────────────────────────────────
let sdVideoAbort = null;

async function generateSDVideo() {
  const prompt = document.getElementById('sd-video-prompt').value.trim();
  if (!prompt) return;
  if (sdVideoAbort) { sdVideoAbort.abort(); }
  sdVideoAbort = new AbortController();

  const frames  = parseInt(document.getElementById('sd-video-frames').value) || 24;
  const fps     = parseInt(document.getElementById('sd-video-fps').value) || 8;
  const steps   = parseInt(document.getElementById('sd-video-steps').value) || 20;
  const denoise = parseFloat(document.getElementById('sd-video-denoise').value) || 0.35;
  const width   = parseInt(document.getElementById('sd-width').value) || 512;
  const height  = parseInt(document.getElementById('sd-height').value) || 512;
  const seed    = parseInt(document.getElementById('sd-seed').value) || -1;

  const btn = document.getElementById('sd-video-gen-btn');
  btn.disabled = true;
  btn.textContent = 'Generating…';

  document.getElementById('sd-video-progress').style.display = 'block';
  document.getElementById('sd-video-status-text').textContent = 'Rendering frame 0…';
  document.getElementById('ic').innerHTML = '<div class="ph">Generating video…</div>';

  let progressTimer = setInterval(async () => {
    try {
      const r = await fetch('/sd-video-progress');
      const d = await r.json();
      const total = d.total || frames;
      const pct = total > 0 ? Math.round((d.frame / total) * 100) : 0;
      const fill = document.getElementById('sd-video-progress-fill');
      const status = document.getElementById('sd-video-status-text');
      if (fill) fill.style.width = pct + '%';
      if (status) status.textContent = `Rendering frame ${d.frame}/${total}…`;
    } catch {}
  }, 700);

  try {
    const res = await fetch('/sd-generate-video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, frames, fps, steps, denoise, width, height, seed }),
      signal: sdVideoAbort.signal,
    });
    const d = await res.json();

    if (d.url) {
      document.getElementById('ic').innerHTML =
        `<video src="${d.url}" class="final" autoplay loop muted playsinline style="max-width:100%;max-height:100%;object-fit:contain"></video>`;
      document.getElementById('sd-video-prompt').value = '';
      const msgs = document.getElementById('msgs');
      if (msgs) {
        const d2 = document.createElement('div');
        d2.className = 'msg alice';
        d2.innerHTML = `<div class="sndr">Alice</div>Generated video: ${prompt.slice(0, 60)}…`;
        msgs.appendChild(d2);
        msgs.scrollTop = msgs.scrollHeight;
      }
    } else {
      document.getElementById('ic').innerHTML =
        `<div class="ph" style="color:#c08080">${d.error || 'Video generation failed'}</div>`;
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      document.getElementById('ic').innerHTML =
        `<div class="ph" style="color:#c08080">Error: ${e.message}</div>`;
    }
  }

  clearInterval(progressTimer);
  btn.disabled = false;
  btn.textContent = 'Generate video';
  document.getElementById('sd-video-progress').style.display = 'none';
}
