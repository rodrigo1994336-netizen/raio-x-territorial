// F1B harness: runs the FINAL served scripts (full reading, panel-sources runtime, CAR integrity, the
// conformity/audit box) in a node vm with a minimal fake DOM and a counting fetch. No network.
// stdin: {"f1b": "<script text>", "f2": "<script text>", "integrity": "<script text>", "audit": "<script text>"}
// stdout: JSON with the observations of every case; the Python gate judges them.
// Run with TZ=America/Sao_Paulo (the gate sets it): bulletin and consultation times are shown in local time.
'use strict';
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const CAR = 'MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F';
const OTHER = 'MG-3120904-0123456789ABCDEF0123456789ABCDEF';
const realSetTimeout = setTimeout;
const tick = (ms = 30) => new Promise(r => realSetTimeout(r, ms));
// Resolves when cond() is true or after `limit` ms (a broken mutant then shows its wrong counts).
async function waitFor(cond, limit = 3000) { const t0 = Date.now(); while (Date.now() - t0 < limit) { let ok = false; try { ok = !!cond(); } catch (e) { ok = false; } if (ok) return true; await tick(5); } return false; }
const settled = F => () => { const e = F.peek(CAR); return !!e && e.phase !== 'loading'; };
const filled = F => () => { const e = F.peek(CAR); return !!e && e.phase === 'ready' && !!e.fill && e.fill.state === 'idle'; };
const strip = s => String(s || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

function el(tag) {
  return {
    tagName: tag, attrs: {}, dataset: {}, children: [], className: '', innerHTML: '', textContent: '', parentNode: null, scrolled: 0,
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    after(n) { n.parentNode = this.parentNode; if (this.parentNode) this.parentNode.children.push(n); },
    before(n) { n.parentNode = this.parentNode; if (this.parentNode) this.parentNode.children.push(n); },
    appendChild(n) { n.parentNode = this; this.children.push(n); return n; },
    scrollIntoView() { this.scrolled += 1; }, closest() { return null; },
  };
}

function makeEnv(route) {
  const listeners = {};
  const calls = [];
  const state = { cardOpen: true };
  const card = el('div');
  card.dataset.car = CAR;
  const button = el('button');
  const pbody = el('div');
  card.querySelector = sel => {
    if (sel === '[data-rx-full-slot]') return card.children.find(c => 'data-rx-full-slot' in c.attrs) || null;
    if (sel === '#rx45Full') return button;
    if (sel === '.rx45-integrity-slot') return card.children.find(c => /rx45-integrity-slot/.test(c.className)) || null;
    return null;
  };
  const document = {
    readyState: 'complete',
    querySelector(sel) {
      if (sel === '#pbody') return pbody;
      if (sel.includes('.rx45-panel-card[data-car="' + CAR + '"]')) return state.cardOpen ? card : null;
      return null;
    },
    querySelectorAll() { return []; },
    createElement: el,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    dispatchEvent(ev) { (listeners[ev.type] || []).forEach(f => f(ev)); },
  };
  const fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const r = await route(u, calls);
    const status = r.status || 200;
    return { ok: status >= 200 && status < 300, status, json: async () => r.body };
  };
  const win = {
    document, fetch, CSS: { escape: s => String(s) }, AbortController,
    setTimeout: (fn, ms) => realSetTimeout(fn, 0), clearTimeout,
    console, Date, Number, JSON, String, Array, Map, Set, Object, RegExp, Promise, Math, Error, CustomEvent: function (t, o) { this.type = t; this.detail = o && o.detail; },
    current: { car_code: CAR },
  };
  win.window = win;
  vm.createContext(win);
  return { win, document, calls, state, card, button, pbody, listeners };
}

const count = (calls, part) => calls.filter(u => u.includes(part)).length;

function analysis(car, over) {
  return Object.assign({
    car: { ok: true, properties: { cod_imovel: car, area: 14.795, municipio: 'Curvelo', uf: 'MG' } },
    embargos_ibama: { ok: true, source: 'x', exact: { available: true, occurrence_count: 0, area_unique_ha: 0 } },
    anm: { ok: true, source: 'x', exact: { available: true, occurrence_count: 2, area_unique_ha: 1234.5678 } },
    autos_ibama: { ok: true, feature_count_bbox: 3, occurrence_count: 0 },
    prodes: { reading: { state: 'found', complete: true, inside: { state: 'found', count: 2, area_ha: 3.456, years: [2006, 2021] }, post_cutoff_inside: { state: 'found', count: 1, area_ha: 1.2, years: [2021] } } },
    fire_live: { ok: true, inside_count: 0, near_count: 3, radius_km: 5, latest_file: 'focos_10min_20260914_2320.csv', window_note: 'Últimos 6 arquivos de 10 minutos disponíveis no diretório oficial do INPE.' },
    territorial_constraints: { ok: true, services: {
      terra_indigena: { ok: true, occurrence_count: 0, area_unique_ha: 0, source: 'FUNAI / IBAMA-PAMGIA' },
      unidade_conservacao: { ok: true, occurrence_count: 1, area_unique_ha: 0.5, source: 'CNUC/MMA / IBAMA-PAMGIA' },
      embargo_icmbio: { ok: false, occurrence_count: 0, area_unique_ha: 0, source: 'ICMBio / IBAMA-PAMGIA', detail: 'consulta_pendente:stale_base' },
      floresta_publica: { source: '<img src=x onerror=alert(1)>', occurrence_count: 0 },
    } },
    water_mg: { ok: true, inside_count: 1, near_count: 7 },
    pivots_ana: { ok: true, intersection_count: 0, feature_count_bbox: 1, parsed_feature_count: 1, reference_year: 2022 },
  }, over || {});
}
// Every source answered: no pending question, so no automatic second query.
function complete(car, over) {
  return analysis(car, Object.assign({ territorial_constraints: { ok: true, services: {
    terra_indigena: { ok: true, occurrence_count: 0, area_unique_ha: 0, source: 'FUNAI / IBAMA-PAMGIA' },
    embargo_icmbio: { ok: true, occurrence_count: 0, area_unique_ha: 0, source: 'ICMBio / IBAMA-PAMGIA' },
  } } }, over || {}));
}
const STAMP_OLD = '2026-09-14T23:00:00.000+00:00';

async function main() {
  const out = {};
  // ---- pure rows/html
  {
    const env = makeEnv(async () => ({ body: {} }));
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    out.api = !!F && typeof F.rows === 'function' && typeof F.html === 'function' && typeof F.start === 'function' && typeof F.merge === 'function';
    if (out.api) {
      const rows = F.rows(analysis(CAR));
      out.rows = rows;
      out.rows_capped = F.rows(analysis(CAR, { autos_ibama: { ok: true, feature_count_bbox: 2000, occurrence_count: 0 } })).find(r => r.id === 'autos_ibama');
      out.rows_exact_unavailable = F.rows(analysis(CAR, { embargos_ibama: { ok: true, exact: { available: false, occurrence_count: 0 } } })).find(r => r.id === 'embargo_ibama');
      out.rows_ok_missing = F.rows(analysis(CAR, { anm: { exact: { available: true, occurrence_count: 0 } } })).find(r => r.id === 'anm');
      out.rows_water_outside = F.rows(analysis(CAR, { water_mg: { ok: false, detail: 'consulta_pendente:outside_source_coverage_mg' } })).filter(r => r.id === 'water').length;
      out.rows_pivot_partial = F.rows(analysis(CAR, { pivots_ana: { ok: true, intersection_count: 0, feature_count_bbox: 5, parsed_feature_count: 3 } })).find(r => r.id === 'pivot');
      out.rows_tc_failed = F.rows(analysis(CAR, { territorial_constraints: { ok: false, detail: 'boom' } })).filter(r => ['terra_indigena', 'unidade_conservacao'].includes(r.id)).map(r => r.answer);
      out.rows_prodes_pending = F.rows(analysis(CAR, { prodes: { reading: { state: 'pending', inside: { state: 'pending', count: 0 } } } })).find(r => r.id === 'prodes');
      // Altamira (PA-1500602): nine PRODES years, the reading complete
      out.rows_prodes_9y = F.rows(analysis(CAR, { prodes: { reading: { state: 'found', complete: true, inside: { count: 20, area_ha: 121.82, years: [2008, 2009, 2013, 2016, 2020, 2021, 2023, 2024, 2025] }, post_cutoff_inside: { count: 9, area_ha: 40.5, years: [2020, 2021, 2023, 2024, 2025] } } } })).find(r => r.id === 'prodes');
      // a PRODES layer that failed: found, but every number is a floor
      out.rows_prodes_incomplete = F.rows(analysis(CAR, { prodes: { reading: { state: 'found', complete: false, inside: { count: 20, area_ha: 121.82, years: [2021, 2023] }, post_cutoff_inside: { count: 0, area_ha: 0, years: [] } } } })).find(r => r.id === 'prodes');
      out.rows_prodes_not_found_incomplete = F.rows(analysis(CAR, { prodes: { reading: { state: 'not_found', complete: false, inside: { count: 0 } } } })).find(r => r.id === 'prodes');
      out.rows_fire_undated = F.rows(analysis(CAR, { fire_live: { ok: true, inside_count: 0, near_count: 0, radius_km: 5 } })).find(r => r.id === 'fire');
      out.rows_empty = F.rows({}).length;
      const now = Date.now();
      out.html_ready = F.html({ phase: 'ready', rows, at: now });
      out.html_ready_unstamped = F.html({ phase: 'ready', rows, at: null });
      out.html_loading = F.html({ phase: 'loading' });
      out.html_failed = F.html({ phase: 'failed', at: now });
      out.html_fill_scheduled = F.html({ phase: 'ready', rows, at: now, fill: { state: 'scheduled', when: now + 190000 } });
      out.html_fill_running = F.html({ phase: 'ready', rows, at: now, fill: { state: 'running', when: now } });
      out.html_fill_idle = F.html({ phase: 'ready', rows, at: now, fill: { state: 'idle', tried: true } });
      out.html_complete = F.html({ phase: 'ready', rows: F.rows(complete(CAR)), at: now });
      const old = F.rows(analysis(CAR));
      const fresh = F.rows(analysis(CAR, { embargos_ibama: { ok: false }, territorial_constraints: { ok: true, services: {
        embargo_icmbio: { ok: true, occurrence_count: 0, source: 'ICMBio / IBAMA-PAMGIA' }, floresta_publica: { ok: false, source: 'SFB' } } } }));
      const m = F.merge(old, fresh);
      out.merge = { changed: m.changed, answers: Object.fromEntries(m.rows.map(r => [r.id, r.answer])), pending_last: m.rows.findIndex(r => r.answer === 'pendente') >= m.rows.filter(r => r.answer !== 'pendente').length };
    }
  }
  // ---- single flight: five starts, three button clicks, re-renders -> ONE quick (everything answered)
  {
    const env = makeEnv(async (u) => u.includes('/v1/live/quick/') ? { body: { ok: true, mode: 'quick-cache', analysis: complete(CAR), deep_state: { state: 'ready', completed_at: STAMP_OLD, analysis: complete(CAR) } } } : { status: 404, body: {} });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    for (let i = 0; i < 5; i++) F.start(CAR);
    for (let i = 0; i < 3; i++) env.win.rxProgressiveAnalyze();
    await waitFor(settled(F));
    for (let i = 0; i < 3; i++) env.document.dispatchEvent({ type: 'rx45:panel-rendered', detail: { car: CAR } });
    F.start(CAR); env.win.rxProgressiveAnalyze();
    await tick(20);
    const slot = env.card.querySelector('[data-rx-full-slot]');
    const e = F.peek(CAR) || {};
    out.single = { quick: count(env.calls, '/v1/live/quick/'), phase: e.phase, at: e.at, stamp_ms: Date.parse(STAMP_OLD), slot_html: slot ? slot.innerHTML : null, slot_in_card: !!slot && slot.parentNode === env.card,
      pbody: env.pbody.innerHTML, button: env.button.textContent, scrolled: slot ? slot.scrolled : 0 };
  }
  // ---- deep polling: running twice, then ready (no completed_at: the run was seen running)
  {
    let polls = 0;
    const env = makeEnv(async (u) => {
      if (u.includes('/v1/live/quick/')) return { body: { ok: true, mode: 'quick-car', analysis: { car: { ok: true } }, deep_state: { state: 'running', stage: 'queued' } } };
      if (u.includes('/v1/live/progressive/status/')) { polls += 1; return { body: polls < 3 ? { state: 'running' } : { state: 'ready', analysis: complete(CAR) } }; }
      return { status: 404, body: {} };
    });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    const t0 = Date.now();
    F.start(CAR); F.start(CAR);
    await waitFor(settled(F));
    const e = F.peek(CAR) || {};
    out.polling = { quick: count(env.calls, '/v1/live/quick/'), status: count(env.calls, '/v1/live/progressive/status/'), phase: e.phase, at_recent: typeof e.at === 'number' && e.at >= t0 };
  }
  // ---- crossing the engine cache expiry: the first answer after it is the PREVIOUS run's "ready" (deep_state
  // of quick-car), which must never be shown; the new run is asked for and shown
  {
    let phase = 'cache', polls = 0;
    const OLD = complete(CAR, { anm: { ok: true, exact: { available: true, occurrence_count: 2, area_unique_ha: 10 } } });
    const NEW = complete(CAR, { anm: { ok: true, exact: { available: true, occurrence_count: 7, area_unique_ha: 10 } } });
    const env = makeEnv(async (u) => {
      if (u.includes('/v1/live/quick/')) return phase === 'cache'
        ? { body: { ok: true, mode: 'quick-cache', analysis: OLD, deep_state: { state: 'ready', completed_at: STAMP_OLD, analysis: OLD } } }
        : { body: { ok: true, mode: 'quick-car', analysis: { car: OLD.car }, deep_state: { state: 'ready', completed_at: STAMP_OLD, analysis: OLD } } };
      if (u.includes('/v1/live/progressive/status/')) { polls += 1; return { body: polls < 2 ? { state: 'running' } : { state: 'ready', completed_at: '2026-09-14T23:28:16.000+00:00', analysis: NEW } }; }
      return { status: 404, body: {} };
    });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(settled(F));
    const first = F.peek(CAR) || {};
    const firstAnm = ((first.rows || []).find(r => r.id === 'anm') || {}).detail;
    phase = 'expired';
    F.start(CAR, { force: true });
    await waitFor(settled(F));
    const e = F.peek(CAR) || {};
    out.stale = { first_anm: firstAnm, anm: ((e.rows || []).find(r => r.id === 'anm') || {}).detail, status: count(env.calls, '/v1/live/progressive/status/'),
      at: e.at, new_stamp_ms: Date.parse('2026-09-14T23:28:16.000+00:00'), slot: (env.card.querySelector('[data-rx-full-slot]') || {}).innerHTML || '' };
  }
  // ---- partial reading: ONE automatic second query when the engine cache expires, only pending rows change;
  // nothing more by itself; "Consultar de novo" asks again; a closed panel asks nothing
  {
    let quicks = 0;
    const env = makeEnv(async (u) => {
      if (u.includes('/v1/live/quick/')) {
        quicks += 1;
        const a = quicks === 1 ? analysis(CAR) : analysis(CAR, { embargos_ibama: { ok: false }, territorial_constraints: { ok: true, services: {
          terra_indigena: { ok: true, occurrence_count: 0, source: 'FUNAI / IBAMA-PAMGIA' }, unidade_conservacao: { ok: true, occurrence_count: 1, area_unique_ha: 0.5, source: 'CNUC/MMA / IBAMA-PAMGIA' },
          embargo_icmbio: { ok: true, occurrence_count: 0, source: 'ICMBio / IBAMA-PAMGIA' }, floresta_publica: { ok: false, source: 'SFB' } } } });
        return { body: { ok: true, mode: 'quick-cache', analysis: a, deep_state: { state: 'ready', completed_at: STAMP_OLD, analysis: a } } };
      }
      return { status: 404, body: {} };
    });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(filled(F));
    await tick(40);
    const e = F.peek(CAR) || {};
    const answers = Object.fromEntries((e.rows || []).map(r => [r.id, r.answer]));
    const slot = (env.card.querySelector('[data-rx-full-slot]') || {}).innerHTML || '';
    const afterAuto = count(env.calls, '/v1/live/quick/');
    F.fill(CAR, e, true);
    await waitFor(() => count(env.calls, '/v1/live/quick/') > afterAuto && (F.peek(CAR).fill || {}).state === 'idle');
    out.partial = { quick_after_auto: afterAuto, answers, slot, embargo_ibama: answers.embargo_ibama, refilled: typeof e.refilled === 'number',
      quick_after_manual: count(env.calls, '/v1/live/quick/') };
    // closed panel: the automatic second query never leaves
    let q2 = 0;
    const env2 = makeEnv(async (u) => { if (u.includes('/v1/live/quick/')) { q2 += 1; env2.state.cardOpen = false; return { body: { ok: true, mode: 'quick-cache', analysis: analysis(CAR), deep_state: { state: 'ready', analysis: analysis(CAR) } } }; } return { status: 404, body: {} }; });
    vm.runInContext(input.f1b, env2.win);
    const F2 = env2.win.rxFullReadingF1b;
    F2.start(CAR);
    await waitFor(settled(F2));
    await tick(60);
    out.partial.closed_quick = q2;
  }
  // ---- failure: worker 503 -> one automatic retry, then "Consulta pendente"; no re-ask within the pause; retry asks again
  {
    const env = makeEnv(async () => ({ status: 503, body: { detail: 'x' } }));
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(settled(F));
    const first = { quick: count(env.calls, '/v1/live/quick/'), phase: (F.peek(CAR) || {}).phase, slot: (env.card.querySelector('[data-rx-full-slot]') || {}).innerHTML || '', button: env.button.textContent };
    F.start(CAR); env.win.rxProgressiveAnalyze(); env.document.dispatchEvent({ type: 'rx45:panel-rendered', detail: { car: CAR } });
    await tick(20);
    first.after_rerender = count(env.calls, '/v1/live/quick/');
    F.start(CAR, { force: true });
    await waitFor(settled(F));
    first.after_force = count(env.calls, '/v1/live/quick/');
    out.failure = first;
  }
  // ---- the engine state endpoint keeps failing: polling stops after 3 errors in a row (per attempt)
  {
    const env = makeEnv(async (u) => u.includes('/v1/live/quick/') ? { body: { ok: true, mode: 'quick-car', deep_state: { state: 'running' } } } : { status: 503, body: {} });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(settled(F));
    out.status_errors = { quick: count(env.calls, '/v1/live/quick/'), status: count(env.calls, '/v1/live/progressive/status/'), phase: (F.peek(CAR) || {}).phase };
  }
  // ---- an answer about another property is never shown
  {
    const env = makeEnv(async (u) => u.includes('/v1/live/quick/') ? { body: { ok: true, mode: 'quick-cache', analysis: analysis(OTHER), deep_state: { state: 'ready', analysis: analysis(OTHER) } } } : { status: 404, body: {} });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(settled(F));
    const e = F.peek(CAR) || {};
    out.other_car = { phase: e.phase, rows: e.rows, slot: (env.card.querySelector('[data-rx-full-slot]') || {}).innerHTML || '' };
  }
  // ---- panel closed while the worker is still working: polling stops, entry forgotten
  {
    let polls = 0;
    const env = makeEnv(async (u) => {
      if (u.includes('/v1/live/quick/')) return { body: { ok: true, mode: 'quick-car', deep_state: { state: 'running' } } };
      polls += 1; if (polls === 2) env.state.cardOpen = false; return { body: { state: 'running' } };
    });
    vm.runInContext(input.f1b, env.win);
    const F = env.win.rxFullReadingF1b;
    F.start(CAR);
    await waitFor(() => env.state.cardOpen === false && F.peek(CAR) === null);
    await tick(20);
    out.closed = { status: count(env.calls, '/v1/live/progressive/status/'), quick: count(env.calls, '/v1/live/quick/'), entry: F.peek(CAR) };
  }
  // ---- CAR integrity: one query per property through the shared per-CAR runtime
  for (const [name, answered] of [['integrity_ok', true], ['integrity_fail', false]]) {
    const env = makeEnv(async (u) => u.includes('/v1/live/car-integrity/') ? { body: answered ? { ok: true, overlap: { state: 'checked', distinct_car_count: 0 }, municipality_boundary: { state: 'checked' }, uf_boundary: { state: 'checked' }, table_rows: [], snapshot: '2026-09-01' } : { ok: false } } : { status: 404, body: {} });
    env.win.showProperty = function () { return null; };
    vm.runInContext(input.f2, env.win);
    vm.runInContext(input.integrity, env.win);
    for (let i = 0; i < 3; i++) env.win.showProperty({ properties: { cod_imovel: CAR } });
    for (let i = 0; i < 4; i++) { env.document.dispatchEvent({ type: 'rx45:panel-rendered', detail: { car: CAR } }); await tick(20); }
    env.win.rxV47LoadIntegrity();
    await waitFor(() => { const e = env.win.rxPanelSourcesF2 && env.win.rxPanelSourcesF2.peek('car_integrity', CAR); return !!e && e.phase !== 'checking'; });
    for (let i = 0; i < 3; i++) { env.document.dispatchEvent({ type: 'rx45:panel-rendered', detail: { car: CAR } }); await tick(10); }
    await tick(50);
    const peek = env.win.rxPanelSourcesF2 && env.win.rxPanelSourcesF2.peek('car_integrity', CAR);
    out[name] = { fetches: count(env.calls, '/v1/live/car-integrity/'), phase: peek ? peek.phase : null };
  }
  // ---- "ver fontes e datas": the open box after the full analysis answered (Altamira: embargo and PRODES found)
  if (input.audit) {
    const IDS = [['car', 'CAR / SICAR'], ['embargo', 'Embargos'], ['prodes', 'PRODES'], ['indigenous_land', 'Terra Indígena'], ['legal_reserve', 'Reserva Legal'], ['conservation_unit', 'Un. Conservação'], ['registry', 'Matrícula'], ['public_forest', 'Floresta Pública'], ['snci', 'SNCI'], ['mte_slave_labor', 'MTE — Trabalho Escravo'], ['sinaflor', 'SINAFLOR — Supressão']];
    const ALT = complete(CAR, { embargos_ibama: { ok: true, exact: { available: true, occurrence_count: 2, area_unique_ha: 3.2 } } });
    const env = makeEnv(async (u) => u.includes('/v1/live/quick/') ? { body: { ok: true, mode: 'quick-cache', analysis: ALT, deep_state: { state: 'ready', completed_at: STAMP_OLD, analysis: ALT } } } : { status: 404, body: {} });
    const rowEls = {
      mte_slave_labor: { dataset: { state: 'blocked_missing_owner_identity' }, querySelector: s => s === '.rx48-check-status' ? { textContent: 'NÃO VERIFICADA' } : s === '.rx48-check-reason' ? { textContent: 'O CAR não traz CPF/CNPJ do titular.' } : null },
    };
    for (const [id] of IDS) if (!rowEls[id] && id !== 'car') rowEls[id] = { dataset: {}, querySelector: () => null };
    let box = null;
    env.card.__rxSourceAudit = { available: true, registry: IDS.map(([id, label]) => ({ id, label, state: id === 'car' ? 'ANSWERED_HIT' : 'NOT_QUERIED' })) };
    const baseQS = env.card.querySelector;
    env.card.querySelector = sel => {
      const m = /data-source="([^"]+)"/.exec(sel); if (m) return rowEls[m[1]] || null;
      if (sel === '.rx48-audit-box') return box;
      if (sel === '.rx48-audit-box.open') return box && box.open ? box : null;
      if (sel === '.rx45-audit-count') return { after(n) { box = n; box.open = true; } };
      return baseQS(sel);
    };
    env.win.CSS = { escape: s => String(s) };
    try {
      vm.runInContext(input.audit, env.win);
      vm.runInContext(input.f1b, env.win);
      const A = env.win.rxV48Audit, F = env.win.rxFullReadingF1b;
      out.audit = { api: !!(A && typeof A.build === 'function') };
      if (out.audit.api) {
        out.audit.before = strip(A.build(env.card).innerHTML);
        F.start(CAR);
        await waitFor(settled(F));
        out.audit.after = strip((box || {}).innerHTML);  // repainted by the reading (the box is open)
        out.audit.rebuilt = strip(A.build(env.card).innerHTML);
      }
    } catch (e) { out.audit = { fatal: String(e && e.stack || e).slice(0, 400) }; }
  }
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
}

main().catch(e => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) }), () => process.exit(0)); });
