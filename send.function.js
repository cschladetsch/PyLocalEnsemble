function send() {
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
