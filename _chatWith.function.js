function _chatWith(msg, { forceImage = false } = {}) {
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
