// ── Global State ─────────────────────────────────────────────────────────────
let mid = 0, imgAbort = null, chatAbort = null, muted = false;
let mediaRecorder = null, audioChunks = [];
let lastReplyText = '', lastUserMsg = '';
let charName = 'Alice';
let llmReady = false;
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

const _PERSONA_FONTS = {
  'default':          { family: "'Montserrat', sans-serif",      style: 'normal', size: '.88rem', weight: '300', spacing: 'normal' },
  'android':          { family: "'Share Tech Mono', monospace", style: 'normal', size: '.85rem', weight: '400', spacing: '.04em' },
  'victorian-lady':   { family: "'Pinyon Script', cursive",     style: 'normal', size: '1.3rem', weight: '400', spacing: 'normal' },
  'egyptian-goddess': { family: "'Cinzel Decorative', serif",   style: 'normal', size: '.82rem', weight: '400', spacing: '.06em' },
  'forest-witch':     { family: "'Almendra', serif",            style: 'italic', size: '1rem',   weight: '400', spacing: 'normal' },
};
const _DEFAULT_FONT = _PERSONA_FONTS['default'];
const _personaFontKeys = {};
