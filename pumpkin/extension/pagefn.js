// Runs inside a web page (injected with chrome.scripting.executeScript, in the extension's own isolated world).
// It must be self-contained: Chrome sends its source to the page. Ops: read, find, click, type, choose, press,
// scroll, describe. The numbers [12] belong to the last read or find of that page.
function pumpkinPage(op, args) {
 try {
 return (function () {
  args = args || {};
  const win = window;
  const st = win.__pumpkin || (win.__pumpkin = { refs: new Map(), next: 1 });

  const INTERACTIVE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary', '[role=button]', '[role=link]',
    '[role=checkbox]', '[role=radio]', '[role=tab]', '[role=menuitem]', '[role=option]', '[role=switch]',
    '[role=combobox]', '[role=textbox]', '[role=searchbox]', '[role=treeitem]', '[contenteditable=""]',
    '[contenteditable=true]', '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  function clean(s, n) {
    s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
    return n && s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
  }

  function allElements(root, out) {
    out = out || [];
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) allElements(el.shadowRoot, out);
    }
    for (const el of root.querySelectorAll(INTERACTIVE)) out.push(el);
    return out;
  }

  function isPassword(el) {
    if (el.tagName !== 'INPUT') return false;
    const t = (el.getAttribute('type') || '').toLowerCase();
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    return t === 'password' || ac.includes('password');
  }

  // card numbers and security codes: like passwords, only the user types these
  function isCard(el) {
    if (el.tagName !== 'INPUT') return false;
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    if (/\bcc-(number|csc|exp|exp-month|exp-year)\b/.test(ac)) return true;
    const hint = [el.name, el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder')].join(' ').toLowerCase();
    return /\b(cvv|cvc|csc|cvv2|card.?number|cardnumber|cc.?num|security.?code)\b/.test(hint);
  }

  function roleOf(el) {
    const r = el.getAttribute('role');
    if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return 'dropdown';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'checkbox' || ty === 'radio') return ty;
      if (['button', 'submit', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'search') return 'searchbox';
      if (ty === 'file') return 'file upload';
      if (ty === 'range') return 'slider';
      return ty === 'password' ? 'password field' : 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'clickable';
  }

  function nameOf(el) {
    let n = el.getAttribute('aria-label');
    if (!n) {
      const by = el.getAttribute('aria-labelledby');
      if (by) n = by.split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || '').join(' ');
    }
    if (!n && el.labels && el.labels.length) n = Array.from(el.labels).map((l) => l.textContent).join(' ');
    const t = el.tagName.toLowerCase();
    if (!n && t === 'input') {
      const ty = (el.getAttribute('type') || '').toLowerCase();
      n = ['button', 'submit', 'reset'].includes(ty) ? el.value : el.getAttribute('placeholder');
    }
    if (!n && t === 'select') n = el.getAttribute('name');
    if (!n) n = el.textContent;
    if (!n) {
      const img = el.querySelector && el.querySelector('img[alt]');
      n = img ? img.getAttribute('alt') : '';
    }
    if (!n) n = el.getAttribute('title') || el.getAttribute('placeholder') || el.getAttribute('name') || el.id;
    return clean(n, 90);
  }

  function describe(el, ref) {
    const role = roleOf(el);
    const t = el.tagName.toLowerCase();
    let s = (ref != null ? '[' + ref + '] ' : '') + role + ' "' + nameOf(el) + '"';
    if (isPassword(el)) s += ' (password: only the user types here)';
    else if (isCard(el)) s += ' (card details: only the user types here)';
    else if (t === 'input' || t === 'textarea') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'checkbox' || ty === 'radio') s += el.checked ? ' [checked]' : ' [not checked]';
      else if (ty !== 'file' && el.value) s += ' value="' + clean(el.value, 80) + '"';
    } else if (t === 'select') {
      const o = el.options[el.selectedIndex];
      s += ' selected="' + clean(o ? o.text : '', 60) + '" options: ' +
        Array.from(el.options).slice(0, 25).map((x) => clean(x.text, 40)).join(' | ');
    } else if (el.isContentEditable && el.textContent) s += ' text="' + clean(el.textContent, 80) + '"';
    const exp = el.getAttribute('aria-expanded');
    if (exp) s += exp === 'true' ? ' [open]' : ' [closed]';
    if (el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true') s += ' [on]';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') s += ' [disabled]';
    if (t === 'a' && el.href) {
      try {
        const u = new URL(el.href);
        s += ' -> ' + (u.origin === location.origin ? u.pathname + u.search : u.origin + u.pathname);
      } catch (e) { /* not a link we can show */ }
    }
    return s;
  }

  function scan() {
    st.refs = new Map();
    st.next = 1;
    const seen = new Set();
    const lines = [];
    const vh = win.innerHeight;
    for (const el of allElements(document)) {
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      if (lines.length >= 220) break;
      const id = st.next++;
      st.refs.set(id, new WeakRef(el));
      const r = el.getBoundingClientRect();
      const where = r.bottom < 0 ? ' (above)' : r.top > vh ? ' (below)' : '';
      lines.push({ id, text: describe(el, id) + where, el });
    }
    return lines;
  }

  function pageText(offset) {
    const full = (document.body ? document.body.innerText : '') || '';
    const text = full.replace(/\n{3,}/g, '\n\n').trim();
    const start = Math.max(0, Number(offset) || 0);
    const LIMIT = 9000;
    let out = text.slice(start, start + LIMIT);
    if (start + LIMIT < text.length) out += '\n… ' + (text.length - start - LIMIT) + ' more characters (read_page with offset=' + (start + LIMIT) + ')';
    return out;
  }

  function target() {
    const ref = Number(args.ref);
    const w = st.refs.get(ref);
    const el = w && w.deref();
    if (!el || !el.isConnected) {
      throw new Error('Number ' + args.ref + ' is not on the page (any more). Read the page again and use the new numbers.');
    }
    return el;
  }

  const here = () => ({ url: location.href, title: document.title });

  if (op === 'read') {
    const mode = args.mode || 'both';
    let text = '';
    if (mode !== 'outline') text += pageText(args.offset);
    if (mode !== 'text') {
      const lines = scan();
      text += (text ? '\n\n' : '') + 'Things you can use on this page (use the number):\n' +
        (lines.length ? lines.map((l) => l.text).join('\n') : '(nothing to click or type into)');
    }
    const frames = document.querySelectorAll('iframe').length;
    if (frames) text += '\n\n(' + frames + ' frame' + (frames === 1 ? '' : 's') + ' on the page are not read.)';
    return Object.assign({ text, page: true }, here());
  }

  if (op === 'find') {
    const q = clean(args.query, 80).toLowerCase();
    const lines = scan().filter((l) => l.text.toLowerCase().includes(q));
    const inText = (document.body ? document.body.innerText : '').toLowerCase().includes(q);
    return Object.assign({
      text: (lines.length ? lines.map((l) => l.text).join('\n') : 'Nothing to click or type into matches "' + q + '".') +
        (inText ? '\n(The words are in the text of the page.)' : ''),
      page: true
    }, here());
  }

  if (op === 'describe') {
    const el = target();
    let text = describe(el);
    const btnValue = el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase()) ? el.value : '';
    const seen = isPassword(el) || isCard(el) ? '' : clean(el.innerText || btnValue || '', 90);
    const named = nameOf(el);
    if (seen && seen.toLowerCase() !== named.toLowerCase()) text += ' — on screen it says "' + seen + '"';
    if (!visible(el)) text += ' — it is not visible on the page';
    return Object.assign({ text }, here());
  }

  if (op === 'click') {
    const el = target();
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') throw new Error('That is disabled.');
    if (el.tagName === 'SELECT') throw new Error('That is a drop-down list. Use choose with the option you want.');
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = el.getBoundingClientRect();
    const opts = { bubbles: true, cancelable: true, view: win, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    if (typeof el.focus === 'function') el.focus({ preventScroll: true });
    for (const t of ['pointerdown', 'mousedown']) el.dispatchEvent(new MouseEvent(t, opts));
    for (const t of ['pointerup', 'mouseup']) el.dispatchEvent(new MouseEvent(t, opts));
    if (typeof el.click === 'function') el.click(); else el.dispatchEvent(new MouseEvent('click', opts));
    return Object.assign({ text: 'Clicked ' + describe(el) }, here());
  }

  if (op === 'type') {
    const el = target();
    if (isPassword(el)) throw new Error('That is a password field. Only the user types there: ask them to.');
    if (isCard(el)) throw new Error('That field is for card details. Only the user types there: ask them to.');
    if (el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'file') throw new Error('That is a file picker; it cannot be used.');
    const text = String(args.text == null ? '' : args.text);
    const clear = args.clear !== false;
    el.scrollIntoView({ block: 'center' });
    el.focus({ preventScroll: true });
    if (el.isContentEditable) {
      if (clear) { document.execCommand('selectAll', false, null); document.execCommand('delete', false, null); }
      document.execCommand('insertText', false, text);
    } else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      const proto = el.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      const set = Object.getOwnPropertyDescriptor(proto, 'value').set;
      set.call(el, clear ? text : el.value + text);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      throw new Error('That is not a text field.');
    }
    let said = 'Typed into ' + describe(el);
    if (args.submit) {
      const form = el.form;
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
      if (form && typeof form.requestSubmit === 'function') form.requestSubmit();
      el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
      said += ' and pressed Enter';
    }
    return Object.assign({ text: said }, here());
  }

  if (op === 'choose') {
    const el = target();
    if (el.tagName !== 'SELECT') throw new Error('That is not a drop-down list. Click it and then click the option.');
    const want = clean(args.option, 80).toLowerCase();
    const opts = Array.from(el.options);
    const hit = opts.find((o) => clean(o.text).toLowerCase() === want || o.value.toLowerCase() === want) ||
      opts.find((o) => clean(o.text).toLowerCase().includes(want));
    if (!hit) throw new Error('No option matches "' + args.option + '". Options: ' + opts.slice(0, 25).map((o) => clean(o.text, 40)).join(' | '));
    el.value = hit.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return Object.assign({ text: 'Chose "' + clean(hit.text, 60) + '"' }, here());
  }

  if (op === 'press') {
    const key = String(args.key || '');
    if (!key) throw new Error('Say which key.');
    const el = document.activeElement && document.activeElement !== document.body ? document.activeElement : document.body;
    const ev = (t) => new KeyboardEvent(t, { key, code: key.length === 1 ? 'Key' + key.toUpperCase() : key, bubbles: true, cancelable: true });
    const down = el.dispatchEvent(ev('keydown'));
    if (down && key === 'Enter') {
      if (el.form && el.tagName !== 'TEXTAREA' && typeof el.form.requestSubmit === 'function') el.form.requestSubmit();
      else if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') el.click();
    }
    if (down && key === 'Tab') {
      const list = allElements(document).filter(visible);
      const i = list.indexOf(el);
      const next = list[(i + 1) % Math.max(list.length, 1)];
      if (next) next.focus();
    }
    el.dispatchEvent(ev('keyup'));
    return Object.assign({ text: 'Pressed ' + key }, here());
  }

  if (op === 'scroll') {
    const d = args.direction;
    const h = win.innerHeight * 0.85;
    const before = win.scrollY;
    if (d === 'down') win.scrollBy({ top: h });
    else if (d === 'up') win.scrollBy({ top: -h });
    else if (d === 'top') win.scrollTo({ top: 0 });
    else if (d === 'bottom') win.scrollTo({ top: document.documentElement.scrollHeight });
    else throw new Error('Direction is down, up, top or bottom.');
    const total = document.documentElement.scrollHeight;
    const pct = Math.round(((win.scrollY + win.innerHeight) / Math.max(total, 1)) * 100);
    return Object.assign({ text: (win.scrollY === before ? 'Could not scroll further. ' : 'Scrolled. ') + 'You are ' + pct + '% down the page.' }, here());
  }

  throw new Error('Unknown page operation ' + op);
 })();
 } catch (e) {
  return { error: String((e && e.message) || e) };
 }
}
