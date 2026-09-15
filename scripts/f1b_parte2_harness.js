// F1B part 2 harness: runs the FINAL served card script (V46, with the C2 number/date formatter) and the
// report-engine wake script in node vm contexts with a minimal fake window. No network.
// stdin: {"format": "<C2 script>", "v46": "<V46 script>", "wake": "<wake script>", "wake_disabled": "<wake script, no URL>",
//         "coord": "<served coordinateSearch function>", "mappanel": "<served rxMapPanelOnce statement>"}
// stdout: JSON observations; the Python gate (scripts/f1b_tela_gate.py) judges them.
'use strict';
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const CAR = 'MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F';
const realSetTimeout = setTimeout;
const tick = (ms = 5) => new Promise(r => realSetTimeout(r, ms));
const strip = s => String(s || '').replace(/<[^>]*>/g, '');

function baseWindow(extra) {
  const listeners = {};
  const document = {
    readyState: 'complete', __listeners: listeners, querySelector() { return null; }, querySelectorAll() { return []; }, getElementById() { return null; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); }, createElement() { return {}; },
  };
  const win = Object.assign({
    document, console, Intl, Number, JSON, String, Array, Map, Set, WeakMap, Object, RegExp, Promise, Math, Error, Date,
    setTimeout: (fn, ms) => realSetTimeout(fn, Math.min(Number(ms) || 0, 5)), clearTimeout,
    matchMedia: () => ({ matches: false, addEventListener() {} }), location: { origin: 'http://127.0.0.1' },
    navigator: {}, getComputedStyle: () => ({}),
  }, extra || {});
  win.window = win;
  vm.createContext(win);
  return win;
}

function run(win, code, label) {
  try { vm.runInContext(code.replace(/^<script[^>]*>/, ''), win, { filename: label }); return null; }
  catch (e) { return `${label}: ${e && e.message}`; }
}

// ------------------------------------------------------------------ card (1B.7)
function cardCases() {
  const out = { errors: [] };
  const win = baseWindow();
  [['format', input.format], ['v46', input.v46]].forEach(([k, code]) => { const e = run(win, code, k); if (e) out.errors.push(e); });
  const api = win.rx46CardF1b;
  if (!api) { out.fatal = 'window.rx46CardF1b missing'; return out; }
  const feature = {
    car_code: CAR, municipality: 'Curvelo', uf: 'MG', area_ha: 14.795, status: 'AT', condition: 'Aguardando análise',
    type: 'IRU', fiscal_modules: 0.3698, created_at: '2018-11-28T13:10:00.000Z', updated_at: '2025-03-07T16:10:06.509Z',
  };
  const view = p => {
    const rows = api.rows(p), html = api.html(p);
    return {
      labels: rows.filter(r => r[1] !== null).map(r => r[0]), waiting: rows.filter(r => r[1] === null).map(r => r[0]),
      values: rows.filter(r => r[1] !== null).map(r => strip(r[1])), html,
      wait_nodes: (html.match(/class="rx46-wait"/g) || []).length,
      empty_labels: (html.match(/<div class="rx46-field"><small>[^<]*<\/small><b>\s*<\/b><\/div>/g) || []).length,
      labelled_waits: (html.match(/class="rx46-wait"[^>]*>\s*<small>[^&<\s][^<]*<\/small>/g) || []).length,
    };
  };
  const g = { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
  // fresh cell: every field the feature carries, at once, no placeholder
  win.rxW1aCellFetchedAt = () => Date.now() - 60 * 1000;
  out.fresh = view(api.cellAge({ ...feature, geometry: g }, g));
  // a cell read more than 5 min ago: status/condition/update date wait for the live answer, in place
  win.rxW1aCellFetchedAt = () => Date.now() - 11 * 60 * 1000;
  const stale = api.cellAge({ ...feature, geometry: g }, g);
  out.stale_values = { status: stale.status, condition: stale.condition, updated_at: stale.updated_at, created_at: stale.created_at };
  out.stale = view(stale);
  out.stale_failed = view({ ...stale, __rx46EnrichFailed: true });
  out.stale_enriched = view({ ...stale, status: 'AT', condition: 'Aguardando análise', updated_at: '2025-03-07T16:10:06.509Z', __rx46Enriched: true });
  // SICAR without fiscal modules: once answered, the field is simply absent
  out.answered_missing = view({ ...feature, fiscal_modules: null, __rx46Enriched: true });
  out.no_code = view({ ...feature, car_code: '', status: '', __rx46Enriched: false });
  // a live record (search/point answer) without update date, before /map-panel: no placeholder (it would shrink)
  win.rxW1aCellFetchedAt = () => null;
  out.live_missing = view(api.cellAge({ ...feature, updated_at: null, fiscal_modules: null }, null));
  // search answer (no cell timestamp): untouched by the cell-age rule
  win.rxW1aCellFetchedAt = () => null;
  out.search = view(api.cellAge({ ...feature }, null));
  return out;
}

// ------------------------------------------------------------------ wake (1B.8)
function wakeEnv(code, opts) {
  const o = opts || {};
  const clock = o.clock || { now: 1_800_000_000_000 };
  class FakeDate extends Date { static now() { return clock.now; } }
  const store = o.store || new Map();
  const fetches = [];
  const sessionStorage = o.storageThrows
    ? { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } }
    : { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  const selectCalls = [];
  let win = null;
  // the painted card of the selection stays on screen until closed
  const original = function (p) { selectCalls.push(p && p.car_code); if (p && p.car_code) { win.current = { car_code: p.car_code }; win.__open = p.car_code; } return 'painted'; };
  original.__rxShareW1a = true; original.__rxIdentitySanitizedV49 = true;
  win = baseWindow({
    Date: FakeDate, sessionStorage,
    fetch: (url, init) => { fetches.push({ url: String(url), method: init && init.method, keepalive: !!(init && init.keepalive) }); return o.fetchRejects ? Promise.reject(new Error('offline')) : Promise.resolve({ ok: true, status: 202 }); },
    rxV46SelectProperty: original,
  });
  win.document.querySelector = sel => (win.__open && String(sel).includes(`.rx46-card[data-car="${win.__open}"]`) ? {} : null);
  const close = () => { win.__open = null; win.current = {}; };
  const err = run(win, code, 'wake');
  return { win, fetches, selectCalls, clock, store, err, close };
}
const intentTarget = hit => ({ closest: sel => (hit && String(sel).includes('data-rx46-action="full"') ? {} : null) });

async function wakeCases() {
  const out = {};
  if (!input.wake) return { fatal: 'wake script missing' };
  // five cards opened in a row in one tab, the last one stays open
  let e = wakeEnv(input.wake);
  out.load_error = e.err;
  const sel = () => e.win.rxV46SelectProperty;
  out.wrapped = !!(sel() && sel().__rxWakeF1b) && sel().__rxShareW1a === true && sel().__rxIdentitySanitizedV49 === true;
  const r1 = sel()({ car_code: CAR });
  out.sync_fetches = e.fetches.length; // nothing leaves before the card is painted and looked at
  out.returns = r1;
  for (let i = 0; i < 4; i++) sel()({ car_code: CAR });
  await tick(20);
  out.burst = { fetches: e.fetches.length, select_calls: e.selectCalls.length, first: e.fetches[0] || null };
  e.clock.now += 9 * 60 * 1000; sel()({ car_code: CAR }); await tick(20);
  out.after_9min = e.fetches.length;
  e.clock.now += 60 * 1000 + 1; sel()({ car_code: CAR }); await tick(20);
  out.after_10min = e.fetches.length;
  // a glance: the card is closed before the dwell -> no wake
  const glance = wakeEnv(input.wake);
  glance.win.rxV46SelectProperty({ car_code: CAR }); glance.close(); await tick(20);
  out.glance = glance.fetches.length;
  // intention without dwell: pointer/focus/finger on "Ver análise completa" or "PDF"
  const hover = wakeEnv(input.wake);
  const fire = (env, type, hit) => (env.win.document.__listeners[type] || []).forEach(f => f({ type, target: intentTarget(hit) }));
  fire(hover, 'pointerover', false); fire(hover, 'focusin', false);
  out.intent_elsewhere = hover.fetches.length;
  fire(hover, 'pointerover', true); fire(hover, 'touchstart', true); fire(hover, 'focusin', true);
  out.intent = hover.fetches.length;
  // a reload in the same tab keeps sessionStorage: still limited
  const reload = wakeEnv(input.wake, { clock: e.clock, store: e.store });
  reload.win.rxV46SelectProperty({ car_code: CAR }); await tick(20);
  out.reload_same_tab = reload.fetches.length;
  // another tab (its own sessionStorage) wakes once
  const other = wakeEnv(input.wake, { clock: e.clock });
  other.win.rxV46SelectProperty({ car_code: CAR }); other.win.rxV46SelectProperty({ car_code: CAR }); await tick(20);
  out.other_tab = other.fetches.length;
  // storage blocked: the page memory still limits
  const blocked = wakeEnv(input.wake, { storageThrows: true });
  for (let i = 0; i < 5; i++) blocked.win.rxV46SelectProperty({ car_code: CAR });
  await tick(20);
  out.storage_blocked = blocked.fetches.length;
  // closing/empty selection does not wake
  const empty = wakeEnv(input.wake);
  empty.win.rxV46SelectProperty({ car_code: '' }); empty.win.rxV46SelectProperty(null); await tick(20);
  out.no_car = empty.fetches.length;
  // offline: the rejection is swallowed, the card still opens
  const offline = wakeEnv(input.wake, { fetchRejects: true });
  let threw = false; try { offline.win.rxV46SelectProperty({ car_code: CAR }); } catch (x) { threw = true; }
  await tick(20);
  out.offline = { threw, fetches: offline.fetches.length, select_calls: offline.selectCalls.length };
  // no service URL / not production: nothing is sent, ever
  if (input.wake_disabled) {
    const off = wakeEnv(input.wake_disabled);
    for (let i = 0; i < 3; i++) off.win.rxV46SelectProperty({ car_code: CAR });
    off.clock.now += 3600 * 1000; off.win.rxV46SelectProperty({ car_code: CAR });
    fire(off, 'pointerover', true);
    await tick(20);
    out.disabled = { fetches: off.fetches.length, select_calls: off.selectCalls.length, enabled: off.win.rxReportWakeF1b && off.win.rxReportWakeF1b.enabled };
  }
  return out;
}

// ------------------------------------------------------------------ coordinate search message (1B.6)
async function coordCases() {
  if (!input.coord) return { fatal: 'coordinate search function missing' };
  const out = {};
  for (const [name, status, body] of [['sicar_502', 502, { detail: 'pending' }], ['none_404', 404, { detail: 'none' }], ['found_200', 200, { property: { car_code: CAR }, geometry: {} }]]) {
    const toasts = [], shown = [];
    const win = baseWindow({
      fetch: async () => ({ ok: status < 300, status, json: async () => body }),
      toast: t => toasts.push(String(t)), showProperty: (p) => shown.push(p && p.car_code),
    });
    const err = run(win, input.coord + '\n;window.__cs=coordinateSearch;', 'coord');
    if (err) { out[name] = { error: err }; continue; }
    // coordinateSearch reads the map through m(): absent here (no map), as on a page still loading
    win.m = () => null;
    let handled = null;
    try { handled = await win.__cs('-20.1, -47.4'); } catch (x) { out[name] = { error: String(x) }; continue; }
    out[name] = { handled, toasts, shown };
  }
  return out;
}

// ------------------------------------------------------------------ shared /map-panel request (1B.3)
async function mapPanelCases() {
  if (!input.mappanel) return { fatal: 'rxMapPanelOnce missing' };
  const calls = [];
  let answer = 'fail';
  const win = baseWindow({
    fetch: async (url) => { calls.push(String(url)); return answer === 'fail' ? { ok: false, status: 503, json: async () => ({ ok: false, detail: 'x' }) }
      : answer === 'partial' ? { ok: true, status: 200, json: async () => ({ ok: true, car: CAR, sigef_reference_state: 'unavailable' }) }
      : { ok: true, status: 200, json: async () => ({ ok: true, car: CAR }) }; },
  });
  const err = run(win, input.mappanel, 'mappanel');
  if (err) return { fatal: err };
  const r1 = await win.rxMapPanelOnce(CAR);
  const r2 = await win.rxMapPanelOnce(CAR);          // a failure is never memorised: this goes to the network
  answer = 'ok';
  const r3 = await win.rxMapPanelOnce(CAR);
  const r4 = await win.rxMapPanelOnce(CAR);          // an answer is memorised: no network
  const before = calls.length;
  answer = 'partial';                                // the INCRA reference did not answer: a re-click asks again
  const OTHER = CAR.slice(0, -1) + '0';
  await win.rxMapPanelOnce(OTHER); await win.rxMapPanelOnce(OTHER);
  return { partial_calls: calls.length - before, calls: before, r1_ok: r1.ok, r2_ok: r2.ok, r3_ok: r3.ok, r4_ok: r4.ok, r4_d: r4.d && r4.d.ok };
}

(async () => {
  const res = {};
  try { res.card = cardCases(); } catch (e) { res.card = { fatal: String(e && e.stack || e).slice(0, 400) }; }
  try { res.wake = await wakeCases(); } catch (e) { res.wake = { fatal: String(e && e.stack || e).slice(0, 400) }; }
  try { res.coord = await coordCases(); } catch (e) { res.coord = { fatal: String(e && e.stack || e).slice(0, 400) }; }
  try { res.mappanel = await mapPanelCases(); } catch (e) { res.mappanel = { fatal: String(e && e.stack || e).slice(0, 400) }; }
  process.stdout.write(JSON.stringify(res));
  process.exit(0);
})();
