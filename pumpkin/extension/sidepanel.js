// The Pumpkin panel. It pairs with Pumpkin on the user's computer, shows the chat, and carries out the model's
// browser commands. While this panel is open, the commands keep coming; closing it stops them.
'use strict';

const $ = (id) => document.getElementById(id);
const SETTLE_MS = 8000;

const S = {
  base: '', key: '', session: '', last: 0, state: 'idle', mode: 'edits', preset: '',
  scope: new Set(),          // tabs the model may work in
  opened: new Set(),         // tabs it opened itself (the only ones it may close)
  current: null,             // the tab "no tab given" means
  pollOk: false, polling: false, rendered: new Set(), asking: ''
};

// ---------------------------------------------------------------- storage and the server
const store = {
  get: (keys) => new Promise((r) => chrome.storage.local.get(keys, r)),
  set: (o) => new Promise((r) => chrome.storage.local.set(o, r)),
  clear: (keys) => new Promise((r) => chrome.storage.local.remove(keys, r))
};

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

async function api(path, method, body, opts) {
  opts = opts || {};
  const headers = { 'X-Pumpkin': '1' };
  if (S.key && !opts.anon) headers.Authorization = 'Bearer ' + S.key;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(S.base + '/pumpkin/api/' + path, {
      method: method || 'GET', headers, credentials: 'omit', cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body), signal: opts.signal
    });
  } catch (e) {
    throw new ApiError('Cannot reach Pumpkin at ' + S.base + '. Is it running?', 0);
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw new ApiError(data.error || ('Pumpkin answered ' + res.status), res.status);
  return data;
}

const hostPattern = (url) => {
  const u = new URL(url);
  return u.protocol + '//' + u.hostname + '/*';
};

function originOf(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    const port = u.port && u.port !== '80' && u.port !== '443' ? ':' + u.port : '';
    return u.protocol + '//' + u.hostname.toLowerCase() + port;
  } catch (e) { return ''; }
}

// ---------------------------------------------------------------- screens
function show(which) {
  $('pair').hidden = which !== 'pair';
  $('chat').hidden = which !== 'chat';
}

function notice(text) {
  $('notice').hidden = !text;
  $('notice').textContent = text || '';
}

function dot(on) {
  $('dot').className = 'dot ' + (on ? 'on' : 'off');
  S.pollOk = on;
}

async function connect() {
  const err = $('pair-error');
  err.hidden = true;
  let base = $('base').value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(base)) base = 'http://' + base;
  const code = $('code').value.trim();
  if (!code) { err.textContent = 'Type the pairing code from Pumpkin.'; err.hidden = false; return; }
  try {
    // must be the first thing awaited: Chrome only asks for a new site inside the click
    const granted = await chrome.permissions.request({ origins: [hostPattern(base)] });
    if (!granted) throw new ApiError('Chrome did not allow the extension to reach ' + base + '.', 0);
    S.base = base;
    S.key = '';
    const out = await api('browser/pair', 'POST', { code, name: 'Chrome' }, { anon: true });
    S.key = out.key;
    await store.set({ base: S.base, key: S.key, session: '' });
    $('code').value = '';
    await enter();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

async function enter() {
  show('chat');
  const saved = await store.get(['session', 'mode', 'preset']);
  S.session = saved.session || '';
  S.mode = saved.mode || 'edits';
  S.preset = saved.preset || '';
  $('mode').value = S.mode;
  try {
    const info = await api('browser/info');
    fillPresets(info);
    notice(info.running ? '' : 'No model is running in Pumpkin yet. Pick one there, then send your message.');
    dot(true);
  } catch (e) {
    if (e.status === 401 || e.status === 403) return lost('Pumpkin does not know this browser any more. Pair it again.');
    dot(false);
    notice(e.message);
  }
  if (S.session) {
    try { await refresh(true); } catch (e) { S.session = ''; await store.set({ session: '' }); }
  }
  pollLoop();
  watch();
}

async function lost(message) {
  await store.clear(['key', 'session']);
  S.key = ''; S.session = '';
  show('pair');
  const err = $('pair-error');
  err.textContent = message || '';
  err.hidden = !message;
}

function fillPresets(info) {
  const sel = $('preset');
  sel.textContent = '';
  const none = document.createElement('option');
  none.value = '';
  none.textContent = info.running ? 'Running model (' + info.running + ')' : 'Whatever Pumpkin picks';
  sel.appendChild(none);
  for (const p of info.presets || []) {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.name || p.id;
    sel.appendChild(o);
  }
  sel.value = S.preset;
}

// ---------------------------------------------------------------- the chat
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function atBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 60; }

const VERB = {
  tabs: 'Looked at the tabs', open_tab: 'Opened', goto: 'Went to', back: 'Went back', close_tab: 'Closed a tab',
  read_page: 'Read the page', find: 'Looked for', click: 'Clicked', type: 'Typed', choose: 'Chose', press: 'Pressed',
  scroll: 'Scrolled', wait: 'Waited', delegate: 'Asked a helper', todo: 'Updated the plan'
};

function stepText(ev) {
  const a = ev.args || {};
  let t = VERB[ev.name] || ev.name;
  if (ev.name === 'open_tab' || ev.name === 'goto') t += ' ' + (a.url || '');
  else if (ev.name === 'find') t += ' "' + (a.query || '') + '"';
  else if (ev.name === 'type') t += ' "' + String(a.text || '').slice(0, 60) + '"';
  else if (ev.name === 'choose') t += ' "' + (a.option || '') + '"';
  else if (ev.name === 'press') t += ' ' + (a.key || '');
  else if (ev.name === 'scroll') t += ' ' + (a.direction || '');
  else if (ev.name === 'click' && ev.target) t += ' ' + ev.target;
  return t;
}

function render(events) {
  const feed = $('feed');
  const stick = atBottom(feed);
  for (const ev of events) {
    if (S.rendered.has(ev.n)) continue;
    S.rendered.add(ev.n);
    S.last = Math.max(S.last, ev.n);
    let node = null;
    if (ev.kind === 'user') node = el('div', 'msg user', ev.text);
    else if (ev.kind === 'assistant') { if ((ev.text || '').trim()) node = el('div', 'msg assistant', ev.text); }
    else if (ev.kind === 'tool') {
      node = el('div', 'step' + (ev.ok === false ? ' bad' : ''), stepText(ev) + (ev.ok === false ? ' — failed' : ''));
      if (ev.ok === false && ev.result) node.title = String(ev.result).slice(0, 300);
    } else if (ev.kind === 'info') node = el('div', 'info', ev.text);
    else if (ev.kind === 'error') node = el('div', 'step bad', ev.text);
    else if (ev.kind === 'answer' && ev.allow === 'no') node = el('div', 'info', 'You said no.');
    if (node) feed.appendChild(node);
  }
  if (stick) feed.scrollTop = feed.scrollHeight;
}

function busy(state) {
  const running = state === 'running' || state === 'waiting';
  $('send').hidden = running;
  $('stop').hidden = !running;
  $('text').disabled = false;
}

function renderAsk(pending) {
  const box = $('asks');
  const id = pending ? pending.id : '';
  if (id === S.asking) return;
  S.asking = id;
  box.textContent = '';
  if (!pending) return;
  const card = el('div', 'ask');
  const btn = (label, cls, fn) => {
    const b = el('button', cls, label);
    b.addEventListener('click', fn);
    return b;
  };
  const row = el('div', 'row');
  if (pending.kind === 'site') {
    const sens = !!pending.sensitive;
    card.appendChild(el('p', '', 'Pumpkin wants to use ' + pending.origin + (sens ? '. This looks like a bank, mail, password or account site.' : '.')));
    if (sens) card.appendChild(el('p', 'warn', 'It will ask again next time.'));
    const grant = async (allow) => {
      // inside the click: Chrome only shows its question for a new site here
      let ok = true;
      if (allow !== 'no') {
        try { ok = await chrome.permissions.request({ origins: [hostPattern(pending.origin)] }); } catch (e) { ok = false; }
      }
      if (!ok) return answer(pending, 'no', 'Chrome did not give the extension access to this site.');
      answer(pending, allow);
    };
    row.appendChild(btn('Allow this chat', 'primary', () => grant('yes')));
    if (!sens) row.appendChild(btn('Always allow', '', () => grant('always')));
    row.appendChild(btn('No', '', () => grant('no')));
  } else if (pending.kind === 'browser') {
    card.appendChild(el('p', '', 'Pumpkin wants to ' + pending.action + ': ' + (pending.target || '') + (pending.origin ? ' (' + pending.origin + ')' : '')));
    if (pending.warning) card.appendChild(el('p', 'warn', 'The page said: "' + pending.warning + '". It reads like orders to the AI. Only say yes if you meant this.'));
    row.appendChild(btn('Yes', 'primary', () => answer(pending, 'yes')));
    if (!pending.warning) row.appendChild(btn('Yes, don\'t ask again this chat', '', () => answer(pending, 'always')));
    row.appendChild(btn('No', '', () => answer(pending, 'no')));
  } else {
    card.appendChild(el('p', '', 'Pumpkin asks for your approval (' + pending.kind + '). Open Pumpkin on your computer to see it.'));
    row.appendChild(btn('No', '', () => answer(pending, 'no')));
  }
  card.appendChild(row);
  box.appendChild(card);
  card.scrollIntoView({ block: 'nearest' });
}

async function answer(pending, allow, note) {
  S.asking = '';
  $('asks').textContent = '';
  try {
    await api('browser/sessions/' + S.session + '/answer', 'POST', { id: pending.id, allow, note: note || '' });
  } catch (e) { notice(e.message); }
  refresh();
}

async function refresh(first) {
  if (!S.session) return;
  const v = await api('browser/sessions/' + S.session + '?after=' + S.last);
  S.state = v.state;
  render(v.events || []);
  busy(v.state);
  renderAsk(v.pending);
  if (first) $('feed').scrollTop = $('feed').scrollHeight;
  return v;
}

let watching = false;
async function watch() {
  if (watching) return;
  watching = true;
  while (S.key) {
    try {
      if (S.session && (S.state === 'running' || S.state === 'waiting')) await refresh();
    } catch (e) {
      if (e.status === 401 || e.status === 403) { watching = false; return lost('Pumpkin does not know this browser any more. Pair it again.'); }
      if (e.status === 404) { S.session = ''; S.state = 'idle'; busy('idle'); }
    }
    await new Promise((r) => setTimeout(r, 700));
  }
  watching = false;
}

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t && originOf(t.url) ? t : null;
}

async function newChat() {
  if (S.session) { try { await api('browser/sessions/' + S.session + '/stop', 'POST', {}); } catch (e) { /* gone */ } }
  S.session = ''; S.last = 0; S.state = 'idle'; S.rendered = new Set(); S.asking = '';
  $('feed').textContent = '';
  $('asks').textContent = '';
  busy('idle');
  await store.set({ session: '' });
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  notice('');
  if (!S.session) {
    // a new chat starts in the tab the user is looking at: that tab, and the ones the chat opens, are its world
    S.scope = new Set(); S.opened = new Set(); S.current = null;
    const t = await activeTab();
    if (t) { S.scope.add(t.id); S.current = t.id; }
    const v = await api('browser/sessions', 'POST', { mode: S.mode, preset: S.preset || null, text });
    S.session = v.id;
    await store.set({ session: S.session });
    S.state = v.state;
    render(v.events || []);
    busy(v.state || 'running');
  } else {
    const v = await api('browser/sessions/' + S.session + '/send', 'POST', { text, after: S.last });
    S.state = v.state;
    render(v.events || []);
    busy(v.state || 'running');
  }
}

// ---------------------------------------------------------------- doing what the model asks in the browser
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function settle(tabId) {
  const end = Date.now() + SETTLE_MS;
  await sleep(250);
  while (Date.now() < end) {
    let t;
    try { t = await chrome.tabs.get(tabId); } catch (e) { return; }
    if (t.status === 'complete') break;
    await sleep(150);
  }
  await sleep(350);
}

function pickTab(args) {
  const id = args.tab != null ? Number(args.tab) : S.current;
  if (id == null || !S.scope.has(id)) {
    throw new Error(args.tab != null ? 'Tab ' + args.tab + ' is not one you can use. Use the tabs tool to see yours.'
      : 'There is no tab to work in yet. Use open_tab with an address.');
  }
  return id;
}

// Pumpkin's own page (and the same port on this computer under another name): the model never works there,
// whatever the server says, because those pages can change Pumpkin itself
function isPumpkin(origin) {
  const mine = originOf(S.base);
  if (!origin || !mine) return false;
  if (origin === mine) return true;
  try {
    const a = new URL(origin), b = new URL(mine);
    const local = (h) => h === 'localhost' || h.endsWith('.localhost') || h === '[::1]' || /^127\./.test(h) || h === '0.0.0.0';
    return local(a.hostname) && local(b.hostname) && (a.port || '80') === (b.port || '80');
  } catch (e) { return false; }
}

function checkOrigin(origin, allowed) {
  if (isPumpkin(origin)) throw new Error('That is Pumpkin itself. The model cannot use it from the browser.');
  return (allowed || []).includes(origin);
}

async function inPage(tabId, op, args) {
  let out;
  try {
    const r = await chrome.scripting.executeScript({ target: { tabId }, func: pumpkinPage, args: [op, args || {}] });
    out = r && r[0] && r[0].result;
  } catch (e) {
    throw new Error('The extension cannot use this page (' + (e.message || e) + '). Chrome may not have given it access to this site.');
  }
  if (!out) throw new Error('The page did not answer. It may still be loading, or it is a page extensions cannot read.');
  if (out.error) throw new Error(out.error);
  return out;
}

async function tabInfo(id) {
  const t = await chrome.tabs.get(id);
  return { tab: id, url: t.url || '', title: t.title || '' };
}

async function execute(tool, args) {
  args = args || {};
  const allowed = args.allowed || [];

  if (tool === 'tabs') {
    const lines = [];
    for (const id of S.scope) {
      try {
        const t = await chrome.tabs.get(id);
        lines.push('[' + id + '] ' + (t.title || '(no title)') + ' — ' + t.url + (id === S.current ? '  (current)' : ''));
      } catch (e) { S.scope.delete(id); }
    }
    return { text: lines.join('\n') || 'No tabs yet. Use open_tab with an address.' };
  }

  if (tool === 'wait') {
    await sleep(Math.max(1, Math.min(10, Number(args.seconds) || 2)) * 1000);
    return { text: 'Waited.' };
  }

  if (tool === 'open_tab' || tool === 'goto') {
    const url = String(args.url || '');
    const origin = originOf(url);
    if (!origin) throw new Error('Give a full web address starting with https:// or http://.');
    if (!checkOrigin(origin, allowed)) return { needs_permission: origin };
    if (tool === 'open_tab') {
      const t = await chrome.tabs.create({ url, active: true });
      S.scope.add(t.id); S.opened.add(t.id); S.current = t.id;
      await settle(t.id);
      return Object.assign({ text: 'Opened tab ' + t.id + '.' }, await tabInfo(t.id));
    }
    const id = pickTab(args);
    await chrome.tabs.update(id, { url });
    S.current = id;
    await settle(id);
    return Object.assign({ text: 'Went to ' + url }, await tabInfo(id));
  }

  if (tool === 'back') {
    const id = pickTab(args);
    await chrome.tabs.goBack(id);
    S.current = id;
    await settle(id);
    return Object.assign({ text: 'Went back.' }, await tabInfo(id));
  }

  if (tool === 'close_tab') {
    const id = pickTab(args);
    if (!S.opened.has(id)) throw new Error('You can only close tabs you opened yourself.');
    await chrome.tabs.remove(id);
    S.scope.delete(id); S.opened.delete(id);
    if (S.current === id) S.current = [...S.scope][0] == null ? null : [...S.scope][0];
    return { text: 'Closed tab ' + id + '.' };
  }

  // everything else works on what is in a tab
  const id = pickTab(args);
  const info = await tabInfo(id);
  const origin = originOf(info.url);
  if (!origin) throw new Error('That tab is not a web page the extension can read (' + (info.url || 'empty') + ').');
  if (!checkOrigin(origin, allowed)) return { needs_permission: origin };
  S.current = id;
  const op = tool === 'read_page' ? 'read' : tool === 'describe' ? 'describe' : tool;
  const pageArgs = tool === 'describe' ? { ref: args.ref } : args;
  const out = await inPage(id, op, pageArgs);
  if (['click', 'type', 'choose', 'press'].includes(tool) && tool !== 'describe') await settle(id);
  const after = await tabInfo(id).catch(() => info);
  if (tool !== 'describe' && originOf(after.url) !== origin) out.moved = after.url;
  return Object.assign(out, { tab: id }, tool === 'describe' ? { url: info.url, title: info.title } : after);
}

async function runCommand(c) {
  const tool = c.tool === 'describe' ? 'describe' : c.tool;
  const args = Object.assign({}, c.args);
  if (tool === 'describe') {
    // asking before an action: tell the user what the thing is, without doing it
    try {
      const r = await execute('describe', args);
      return { ok: true, result: r };
    } catch (e) { return { ok: false, result: { error: e.message } }; }
  }
  try {
    return { ok: true, result: await execute(tool, args) };
  } catch (e) {
    return { ok: false, result: { error: e.message || String(e) } };
  }
}

async function pollLoop() {
  if (S.polling) return;
  S.polling = true;
  while (S.key) {
    try {
      const tabs = [];
      for (const id of S.scope) {
        try { const t = await chrome.tabs.get(id); tabs.push({ id, title: t.title, url: t.url }); } catch (e) { S.scope.delete(id); }
      }
      const out = await api('browser/poll', 'POST', { tabs, wait: 25 });
      dot(true);
      for (const c of out.commands || []) {
        const r = await runCommand(c);
        try { await api('browser/result', 'POST', { id: c.id, ok: r.ok, result: r.result }); } catch (e) { /* nobody is waiting any more */ }
      }
    } catch (e) {
      if (e.status === 401 || e.status === 403) { S.polling = false; return lost('Pumpkin does not know this browser any more. Pair it again.'); }
      dot(false);
      await sleep(3000);
    }
  }
  S.polling = false;
}

// tabs the chat's tabs open belong to the chat too
chrome.tabs.onCreated.addListener((t) => {
  if (t.openerTabId != null && S.scope.has(t.openerTabId)) { S.scope.add(t.id); S.opened.add(t.id); }
});
chrome.tabs.onRemoved.addListener((id) => {
  S.scope.delete(id); S.opened.delete(id);
  if (S.current === id) S.current = null;
});

// ---------------------------------------------------------------- wiring
$('connect').addEventListener('click', connect);
$('code').addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });
$('menu').addEventListener('click', () => { $('settings').hidden = !$('settings').hidden; });
$('new').addEventListener('click', newChat);
$('mode').addEventListener('change', async () => {
  S.mode = $('mode').value;
  await store.set({ mode: S.mode });
  if (S.session) { try { await api('browser/sessions/' + S.session + '/mode', 'POST', { mode: S.mode }); } catch (e) { notice(e.message); } }
});
$('preset').addEventListener('change', async () => {
  S.preset = $('preset').value;
  await store.set({ preset: S.preset });
  if (S.session) { try { await api('browser/sessions/' + S.session + '/model', 'POST', { preset: S.preset || null }); } catch (e) { notice(e.message); } }
});
$('unpair').addEventListener('click', async () => {
  await lost('Disconnected. You can remove this browser on Pumpkin\'s Browser page too.');
});
$('stop').addEventListener('click', async () => {
  try { await api('browser/sessions/' + S.session + '/stop', 'POST', {}); } catch (e) { notice(e.message); }
  refresh();
});
$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = $('text').value;
  if (!t.trim()) return;
  $('text').value = '';
  try { await send(t); } catch (err) { $('text').value = t; notice(err.message); }
});
$('text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('form').requestSubmit(); }
});

(async function init() {
  const saved = await store.get(['base', 'key']);
  if (saved.base) { S.base = saved.base; $('base').value = saved.base; }
  if (saved.base && saved.key) { S.key = saved.key; await enter(); } else show('pair');
})();
