// W1a rule harness (run by scripts/w1a_link_imovel_gate.py, never by the portal).
// stdin: {"w1a": <final W1a <script> body>, "sw": <served /sw.js>, "consts": {...}}
// stdout: {"<check>": {"ok": bool, "detail": str}} — each check exercises one owner rule on the
// code the portal really serves, with a fake clock, a tiny DOM and scripted SICAR answers.
'use strict';
const vm = require('vm');
const fs = require('fs');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const K = input.consts;
const ORIGIN = 'https://raio.example';
const VALID = K.valid, OTHER = K.other, NOTFOUND = K.notfound;
const results = {};

function flush() {
  let p = Promise.resolve();
  for (let i = 0; i < 25; i++) p = p.then(() => new Promise(r => setImmediate(r)));
  return p;
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
async function check(name, fn) {
  try { await fn(); results[name] = { ok: true, detail: '' }; }
  catch (e) { results[name] = { ok: false, detail: String(e && e.message || e).slice(0, 400) }; }
}

function makeClock() {
  let now = 1700000000000, seq = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { fn, at: now + Math.max(0, Number(ms) || 0) }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        await flush();
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = Math.max(now, next[1].at);
        try { next[1].fn(); } catch (e) { /* page code errors surface as failed checks */ }
      }
      now = end;
      await flush();
    },
  };
}

const camel = s => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
function matches(el, sel) {
  let m;
  if ((m = sel.match(/^\.([\w-]+)$/))) return String(el.className || '').split(/\s+/).includes(m[1]);
  if ((m = sel.match(/^#([\w-]+)$/))) return el.id === m[1];
  if ((m = sel.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/))) {
    const v = el.getAttribute(m[1]);
    return m[2] === undefined ? v !== null : v === m[2];
  }
  return /^[a-z]+$/i.test(sel) && el.tagName === sel.toUpperCase();
}
class El {
  constructor(tag, doc) { Object.assign(this, { tagName: tag.toUpperCase(), doc, children: [], parent: null, attrs: {}, dataset: {}, hidden: false, _text: '', value: '', listeners: {}, style: {}, className: '', id: '' }); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = String(v); if (k.startsWith('data-')) this.dataset[camel(k.slice(5))] = String(v); }
  getAttribute(k) { if (k === 'id') return this.id || null; if (k === 'class') return this.className || null; return k in this.attrs ? this.attrs[k] : null; }
  append(...n) { n.forEach(x => this.appendChild(x)); }
  appendChild(x) { x.parent = this; this.children.push(x); return x; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  click() { (this.listeners.click || []).forEach(f => f({ target: this, preventDefault() {} })); if (this.onclick) this.onclick(); }
  descendants() { const out = []; const walk = e => { for (const c of e.children) { out.push(c); walk(c); } }; walk(this); return out; }
  querySelector(sel) { return this.descendants().find(e => matches(e, sel)) || null; }
  querySelectorAll(sel) { return this.descendants().filter(e => matches(e, sel)); }
  contains(x) { for (let e = x; e; e = e.parent) if (e === this) return true; return false; }
  closest(sel) { for (let e = this; e && e !== this.doc.body; e = e.parent) if (matches(e, sel)) return e; return null; }
  focus() { this.doc.activeElement = this; }
  select() {}
  setSelectionRange() {}
  get isConnected() { return this.doc.body.contains(this); }
}

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => JSON.parse(JSON.stringify(body)) };
}
function carBody(code, answer) {
  return { car: { ok: true, source: 'SICAR', properties: { cod_imovel: answer || code, status_imovel: 'AT', area: 14.795, condicao: 'Aguardando análise', uf: 'MG', municipio: 'Curvelo', m_fiscal: 0.37, tipo_imovel: 'IRU' }, geometry: { type: 'Point', coordinates: [-44.18, -18.89] } } };
}
function notFoundBody(answered) {
  const attempts = K.exact.map(s => ({ strategy: s, ok: answered, bytes: answered ? 147 : 0, detail: answered ? null : 'curl: (28) Operation timed out' }))
    .concat([{ strategy: 'municipality_codes_0', ok: false, bytes: 0, detail: 'curl: (28) Operation timed out' }]);
  return { detail: { car: { ok: false, source: 'SICAR', not_found: true, detail: 'CAR não localizado após múltiplas estratégias de consulta SICAR.', attempts } } };
}

// One page: fresh realm running the served W1a script. `answer(code, n)` scripts the CAR route:
// {status, body} | 'hang' (only the abort signal ends it) | 'network'.
function page(href, answer, opts = {}) {
  const clock = makeClock();
  const doc = { readyState: 'complete', activeElement: null, listeners: {} };
  doc.body = new El('body', doc);
  Object.assign(doc, {
    getElementById: id => doc.body.descendants().find(e => e.id === id) || null,
    createElement: t => new El(t, doc),
    querySelector: s => doc.body.querySelector(s),
    querySelectorAll: s => doc.body.querySelectorAll(s),
    addEventListener: (t, f) => { (doc.listeners[t] = doc.listeners[t] || []).push(f); },
  });
  const loc = { _u: new URL(href, ORIGIN) };
  for (const k of ['href', 'origin', 'pathname', 'search', 'hash']) Object.defineProperty(loc, k, { get: () => loc._u[k] });
  const history = { length: 1, state: null, replaced: [], pushed: [],
    replaceState(s, t, u) { this.replaced.push(String(u)); loc._u = new URL(u, loc._u.href); },
    pushState(s, t, u) { this.pushed.push(String(u)); this.length++; loc._u = new URL(u, loc._u.href); } };
  const log = { fetches: [], signals: [], shown: [], selected: [], writes: [] };
  const counts = {};
  const ctx = {
    document: doc, location: loc, history, console,
    URL, URLSearchParams, AbortController,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, Date: { now: clock.now },
    map: { setView() {}, getCenter: () => ({ lat: -18.9, lng: -44.2 }), getZoom: () => 14, on() {} },
    fetch(url, init) {
      const u = String(url);
      log.fetches.push(u);
      const signal = init && init.signal;
      log.signals.push(signal);
      const code = decodeURIComponent(u.split('/v1/live/car/')[1] || '');
      counts[code] = (counts[code] || 0) + 1;
      const a = answer(code, counts[code]);
      if (a === 'network') return Promise.reject(new TypeError('network'));
      return new Promise((resolve, reject) => {
        const abort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        if (signal) { if (signal.aborted) return abort(); signal.addEventListener('abort', abort); }
        if (a !== 'hang') resolve(response(a.status, a.body));
      });
    },
  };
  ctx.window = ctx;
  ctx.rxV46Installed = true;
  ctx.rxV46SelectProperty = function (p) { log.selected.push(p && p.car_code); };
  ctx.rxV46CloseAnchor = function () {};
  ctx.showProperty = function (p, g) { log.shown.push(p && p.car_code); ctx.rxV46SelectProperty(p, g); };
  ctx.rxCopyCarC2 = { write: async t => { if (opts.clipboard) { log.writes.push(t); return true; } return false; } };
  vm.createContext(ctx);
  vm.runInContext(input.w1a, ctx, { filename: 'w1a.js' });
  const notice = () => {
    const el = doc.getElementById('rxShareStateW1a');
    if (!el || el.hidden) return null;
    return { text: el.querySelector('.rx-share-state-text').textContent, closeVisible: !el.querySelector('.rx-share-state-x').hidden, retry: !el.querySelector('.rx-share-state-retry').hidden, el };
  };
  const carParams = () => new URL(loc.href).searchParams.getAll('car');
  return { ctx, clock, doc, loc, history, log, notice, carParams };
}

async function main() {
  const INVALID = [
    '<img src=x onerror="window.__pwned=1">', 'MG-3120904-XYZ', 'javascript:window.__pwned=1',
    VALID + '"><svg onload="window.__pwned=1">', 'XX-3120904-DFB380BECD7A4323AD8AA68FA14D011F',
    'MG-3120904-GFB380BECD7A4323AD8AA68FA14D011F', VALID + '0', VALID.slice(0, 42), ' ' + VALID + '\nX',
  ];

  await check('link_valid_opens_that_car', async () => {
    const pg = page(`/?car=${VALID}`, () => ({ status: 200, body: carBody(VALID) }));
    await pg.clock.advance(50);
    assert(JSON.stringify(pg.log.fetches) === JSON.stringify([`/v1/live/car/${VALID}`]), 'fetch ' + JSON.stringify(pg.log.fetches));
    assert(JSON.stringify(pg.log.shown) === JSON.stringify([VALID]), 'shown ' + JSON.stringify(pg.log.shown));
    assert(pg.notice() === null, 'notice left open ' + JSON.stringify(pg.notice() && pg.notice().text));
    assert(JSON.stringify(pg.carParams()) === JSON.stringify([VALID]), 'url ' + pg.loc.href);
  });

  await check('link_invalid_code_never_used', async () => {
    const hrefs = INVALID.map(v => `/?car=${encodeURIComponent(v)}`).concat([`/?car=${VALID}&car=${OTHER}`]);
    for (const href of hrefs) {
      const pg = page(href, () => ({ status: 200, body: carBody(VALID) }));
      await pg.clock.advance(50);
      assert(pg.log.fetches.length === 0, `invalid code reached fetch: ${href} -> ${pg.log.fetches}`);
      assert(pg.log.shown.length === 0, `invalid code opened a card: ${href}`);
      assert(pg.carParams().length === 0, `invalid code kept in the address bar: ${pg.loc.href}`);
      const n = pg.notice();
      assert(n && n.text === K.msg.invalid && n.closeVisible, `invalid notice: ${href} -> ${JSON.stringify(n && n.text)}`);
      assert(pg.ctx.__pwned === undefined, 'script injection ran');
    }
  });

  await check('link_answer_for_other_car_never_shown', async () => {
    const pg = page(`/?car=${VALID}`, () => ({ status: 200, body: carBody(VALID, OTHER) }));
    await pg.clock.advance(K.retryGapMs + 1000);
    assert(pg.log.shown.length === 0, 'a different CAR opened: ' + pg.log.shown);
    const n = pg.notice();
    assert(n && n.text === K.msg.pending && n.retry && n.closeVisible, 'pending notice ' + JSON.stringify(n && n.text));
    assert(pg.log.fetches.length === 2, 'expected one automatic retry, got ' + pg.log.fetches.length);
  });

  await check('link_404_answered_by_sicar_says_not_found', async () => {
    const pg = page(`/?car=${NOTFOUND}`, () => ({ status: 404, body: notFoundBody(true) }));
    await pg.clock.advance(50);
    const n = pg.notice();
    assert(n && n.text === K.msg.notFound && n.closeVisible, 'not-found notice ' + JSON.stringify(n && n.text));
    assert(pg.carParams().length === 0 && pg.log.shown.length === 0 && pg.log.fetches.length === 1, 'state ' + pg.loc.href);
  });

  await check('link_404_without_sicar_answer_is_pending_not_not_found', async () => {
    const pg = page(`/?car=${NOTFOUND}`, () => ({ status: 404, body: notFoundBody(false) }));
    const seen = [];
    for (let t = 0; t < K.retryGapMs + 2000; t += 250) { await pg.clock.advance(250); const n = pg.notice(); if (n) seen.push(n.text); }
    assert(!seen.includes(K.msg.notFound), 'said "não encontramos" without a SICAR answer');
    const n = pg.notice();
    assert(n && n.text === K.msg.pending && n.retry, 'pending notice ' + JSON.stringify(n && n.text));
  });

  await check('link_5xx_pending_then_retry_button_opens', async () => {
    let up = false;
    const pg = page(`/?car=${VALID}`, () => (up ? { status: 200, body: carBody(VALID) } : { status: 502, body: { detail: 'x' } }));
    await pg.clock.advance(K.retryGapMs + 1000);
    const n = pg.notice();
    assert(n && n.text === K.msg.pending && n.retry && n.closeVisible, 'pending notice ' + JSON.stringify(n && n.text));
    assert(pg.log.fetches.length === 2 && pg.log.shown.length === 0, 'fetches ' + pg.log.fetches.length);
    up = true;
    n.el.querySelector('.rx-share-state-retry').click();
    await pg.clock.advance(50);
    assert(JSON.stringify(pg.log.shown) === JSON.stringify([VALID]), 'retry did not open the card');
  });

  await check('link_deadline_stops_waiting_honestly', async () => {
    const pg = page(`/?car=${VALID}`, () => 'hang');
    await pg.clock.advance(100);
    let n = pg.notice();
    assert(n && n.text === K.msg.busy && n.closeVisible, 'busy notice ' + JSON.stringify(n && n.text));
    await pg.clock.advance(K.slowMs);
    n = pg.notice();
    assert(n && n.text === K.msg.slow && n.closeVisible, 'slow notice ' + JSON.stringify(n && n.text));
    await pg.clock.advance(K.deadlineMs);
    n = pg.notice();
    assert(n && n.text === K.msg.pending && n.retry, 'after the deadline: ' + JSON.stringify(n && n.text));
    assert(pg.log.signals.every(s => s && s.aborted), 'lookup still running after the deadline');
    assert(pg.log.shown.length === 0, 'card opened');
  });

  await check('link_busy_notice_close_cancels', async () => {
    const pg = page(`/?car=${VALID}`, () => 'hang');
    await pg.clock.advance(100);
    const n = pg.notice();
    assert(n && n.text === K.msg.busy, 'busy notice ' + JSON.stringify(n && n.text));
    const x = n.el.querySelector('.rx-share-state-x');
    assert(!x.hidden, 'busy notice has no visible close');
    x.click();
    await pg.clock.advance(50);
    assert(pg.notice() === null, 'notice still open after close');
    assert(pg.log.signals[0] && pg.log.signals[0].aborted, 'closing did not cancel the lookup');
    assert(pg.carParams().length === 0, 'cancelled link kept ?car=');
    await pg.clock.advance(K.deadlineMs + 5000);
    assert(pg.notice() === null && pg.log.shown.length === 0 && pg.log.fetches.length === 1, 'cancelled link came back');
  });

  await check('selection_uses_replace_state_only', async () => {
    const pg = page('/', () => ({ status: 500, body: {} }));
    await pg.clock.advance(50);
    pg.ctx.rxV46SelectProperty({ car_code: OTHER });
    assert(JSON.stringify(pg.carParams()) === JSON.stringify([OTHER]), 'address bar ' + pg.loc.href);
    assert(pg.history.pushed.length === 0 && pg.history.length === 1, 'selection pushed a history entry');
    assert(pg.history.replaced.some(u => u.includes(`car=${OTHER}`)), 'replaceState not used');
    pg.ctx.rxV46CloseAnchor();
    assert(pg.carParams().length === 0 && pg.history.length === 1, 'close kept ?car= ' + pg.loc.href);
  });

  await check('copy_success_says_copiado_with_the_exact_link', async () => {
    const pg = page('/', () => ({ status: 500, body: {} }), { clipboard: true });
    await pg.clock.advance(50);
    const api = pg.ctx.rxShareW1a;
    api.menu(VALID);
    const ok = await api.copy(VALID);
    await pg.clock.advance(10);
    assert(ok === true, 'copy returned ' + ok);
    assert(JSON.stringify(pg.log.writes) === JSON.stringify([`${ORIGIN}/?car=${VALID}`]), 'clipboard got ' + JSON.stringify(pg.log.writes));
    assert(api.html(VALID, 'Curvelo / MG').includes('Link copiado'), 'no "Link copiado" after a real copy');
    const s = pg.doc.getElementById('rxShareSheetW1a');
    assert(!s || s.hidden, 'fallback sheet shown after success');
  });

  await check('copy_failure_never_says_copiado', async () => {
    const pg = page('/', () => ({ status: 500, body: {} }), { clipboard: false });
    await pg.clock.advance(50);
    const api = pg.ctx.rxShareW1a;
    api.menu(VALID);
    await api.copy(VALID);
    await pg.clock.advance(10);
    assert(!api.html(VALID, 'Curvelo / MG').toLowerCase().includes('copiado'), 'said copiado without copying');
    const live = pg.doc.getElementById('rxShareLiveW1a');
    await pg.clock.advance(100);
    assert(!live || !live.textContent.toLowerCase().includes('link copiado'), 'announced "Link copiado" without copying');
    const s = pg.doc.getElementById('rxShareSheetW1a');
    assert(s && !s.hidden && s.querySelector('textarea').value === `${ORIGIN}/?car=${VALID}`, 'fallback sheet missing the link');
  });

  await check('whatsapp_link_contract', async () => {
    const pg = page('/', () => ({ status: 500, body: {} }));
    const api = pg.ctx.rxShareW1a;
    const text = `Veja este imóvel rural no Raio-X Territorial (Curvelo / MG): ${ORIGIN}/?car=${VALID}`;
    assert(api.waHref(VALID, 'Curvelo / MG') === 'https://wa.me/?text=' + encodeURIComponent(text), 'wa href ' + api.waHref(VALID, 'Curvelo / MG'));
    assert(api.waHref('<b>', 'x') === '', 'invalid code produced a WhatsApp link');
    const h = api.html(VALID, 'Curvelo / MG');
    const a = h.slice(h.indexOf('<a '), h.indexOf('</a>'));
    assert(a.includes('target="_blank" rel="noopener noreferrer"') && !a.includes('data-rx46-action'), 'wa anchor ' + a);
    assert(api.html('MG-1<script>', 'x') === '', 'invalid code rendered share controls');
  });

  // ---- service worker: the shell is one key, whatever the query ----
  function swRealm(online) {
    const store = new Map();
    const key = k => (typeof k === 'string' ? new URL(k, ORIGIN).href : k.url);
    const caches = {
      async open(n) {
        if (!store.has(n)) store.set(n, new Map());
        const m = store.get(n);
        return { async put(k, r) { m.delete(key(k)); m.set(key(k), r); }, async match(k) { return m.get(key(k)); },
          async keys() { return [...m.keys()].map(url => ({ url })); }, async delete(k) { return m.delete(key(k)); } };
      },
      async keys() { return [...store.keys()]; },
      async delete(n) { return store.delete(n); },
    };
    const listeners = {};
    const state = { online };
    const ctx = {
      self: { addEventListener: (t, f) => { listeners[t] = f; }, skipWaiting() {}, clients: { claim: async () => {} } },
      caches, location: { origin: ORIGIN }, URL, Promise, Error, setTimeout, clearTimeout, console,
      fetch: async req => {
        const url = typeof req === 'string' ? new URL(req, ORIGIN).href : req.url;
        if (!state.online) throw new TypeError('offline');
        return { ok: true, status: 200, type: 'basic', url, clone() { return this; } };
      },
    };
    vm.createContext(ctx);
    vm.runInContext(input.sw, ctx, { filename: 'sw.js' });
    const nav = async path => {
      const ev = { request: { url: new URL(path, ORIGIN).href, method: 'GET', mode: 'navigate' }, p: null, respondWith(p) { this.p = p; }, waitUntil() {} };
      listeners.fetch(ev);
      if (!ev.p) return { status: 'not-handled' };
      try { const r = await ev.p; return { status: r && r.status }; } catch (e) { return { status: 'failed:' + e.message }; }
    };
    const life = async t => { let p = null; listeners[t]({ waitUntil(x) { p = x; } }); await p; };
    const shellKeys = async () => { const names = await caches.keys(); const n = names.find(x => x.endsWith('-shell')); return n ? (await (await caches.open(n)).keys()).map(r => r.url) : []; };
    return { state, nav, life, shellKeys, caches };
  }

  await check('sw_offline_shell_survives_shared_links', async () => {
    const sw = swRealm(true);
    await sw.life('install');
    await sw.life('activate');
    for (let i = 0; i < 6; i++) { const r = await sw.nav(`/?car=probe${i}`); assert(r.status === 200, 'online nav ' + r.status); }
    const keys = await sw.shellKeys();
    sw.state.online = false;
    const link = await sw.nav(`/?car=${VALID}`);
    assert(link.status === 200, 'offline shared link: ' + link.status + ' (shell keys ' + JSON.stringify(keys) + ')');
    const root = await sw.nav('/');
    assert(root.status === 200, 'offline root: ' + root.status);
    assert(keys.length === 1 && keys[0] === ORIGIN + '/', 'shell cache keys ' + JSON.stringify(keys));
  });

  await check('sw_activate_drops_old_query_keys', async () => {
    const sw = swRealm(true);
    await sw.life('install');
    const names = await sw.caches.keys();
    const shell = await sw.caches.open(names.find(x => x.endsWith('-shell')));
    await shell.put(`/?car=${VALID}`, { ok: true, status: 200, clone() { return this; } });
    await sw.life('activate');
    const keys = await sw.shellKeys();
    assert(JSON.stringify(keys) === JSON.stringify([ORIGIN + '/']), 'shell keys after activate ' + JSON.stringify(keys));
  });

  process.stdout.write(JSON.stringify(results));
}

main().catch(e => { process.stdout.write(JSON.stringify({ harness_crashed: { ok: false, detail: String(e && e.stack || e) } })); });
