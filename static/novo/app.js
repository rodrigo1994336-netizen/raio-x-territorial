/* Raio-X Territorial - tela nova (/novo). Sem framework; Leaflet 1.9.4 vendorizado.
   Regras do dono que este arquivo segue:
   - nada escrito sobre o mapa (sem tooltip, popup nem rótulo nosso; nome só no cartão, depois do clique);
   - título do cartão = nome validado pelo painel ou o código do CAR; nunca município, nunca área convertida em outra unidade;
   - "Sim" e "Não" só quando a fonte respondeu; qualquer outra coisa é "Consulta pendente";
   - zero só é "Não" com resposta completa (corte no teto não é zero);
   - a medida desenhada pelo usuário é dele, nunca a área do CAR. */
(function (root) {
  'use strict';

  // ------------------------------------------------------------------ funções puras
  var CAR_RE = /^[A-Z]{2}-\d{7}-[0-9A-F]{32}$/;
  var isNum = function (v) { return typeof v === 'number' && isFinite(v); };
  function toNum(v) {
    if (isNum(v)) return v;
    if (typeof v !== 'string' || !v.trim()) return null;
    var n = Number(v.trim().replace(',', '.'));
    return isFinite(n) ? n : null;
  }
  function num(v, d) {
    var n = toNum(v);
    if (n === null) return '';
    var digits = d === undefined ? 2 : d;
    return n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function int(v) { var n = toNum(v); return n === null ? '' : Math.round(n).toLocaleString('pt-BR'); }
  function ha(v) { var s = num(v, 2); return s ? s + ' ha' : ''; }
  function plural(n, one, many) { return int(n) + ' ' + (Math.round(n) === 1 ? one : many); }
  function date(v) {
    if (!v) return '';
    var t = Date.parse(String(v));
    if (!isFinite(t)) return '';
    try {
      return new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(t));
    } catch (e) { return ''; }
  }
  function dateTime(t) {
    if (!isNum(t)) return '';
    try {
      var d = new Date(t);
      var day = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric' }).format(d);
      var hour = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' }).format(d);
      return day + ', às ' + hour;
    } catch (e) { return ''; }
  }
  function ymd(s) { var m = /^(\d{4})(\d{2})(\d{2})$/.exec(String(s || '')); return m ? m[3] + '/' + m[2] + '/' + m[1] : ''; }
  function modulos(v) {
    var n = toNum(v), s = num(v, 2);
    if (!s) return '';
    return s + (n < 2 ? ' módulo fiscal' : ' módulos fiscais');
  }
  var SITUACAO = { AT: 'Ativo', PE: 'Pendente', SU: 'Suspenso', CA: 'Cancelado' };
  var TIPO = { IRU: 'Imóvel rural', AST: 'Assentamento da reforma agrária', PCT: 'Território de povo ou comunidade tradicional' };
  function first(o) {
    for (var i = 1; i < arguments.length; i++) {
      var v = o ? o[arguments[i]] : null;
      if (v !== null && v !== undefined && v !== '' && v !== 'null') return v;
    }
    return null;
  }
  function lowerFirst(s) { s = String(s || '').trim(); return s ? s.charAt(0).toLowerCase() + s.slice(1) : ''; }

  // SICAR attributes (viewport feature, /v1/live/car or /map-panel) -> the card facts. Empty fields are left out.
  function facts(props, panel) {
    var p = props || {}, q = panel && panel.ok === true ? panel : {};
    var code = String(first(q, 'car_code') || first(p, 'cod_imovel', 'car_code') || '').trim().toUpperCase();
    var area = first(q, 'area_ha'); if (area === null) area = first(p, 'area', 'area_ha', 'num_area');
    var mf = first(q, 'fiscal_modules'); if (mf === null) mf = first(p, 'm_fiscal', 'fiscal_modules', 'mod_fiscal');
    var st = String(first(q, 'car_status') || first(p, 'status_imovel', 'status', 'ind_status') || '').trim().toUpperCase();
    var tp = String(first(q, 'property_type') || first(p, 'tipo_imovel', 'type', 'ind_tipo') || '').trim().toUpperCase();
    var cond = first(q, 'condition') || first(p, 'condicao', 'condition', 'des_condic') || '';
    var mun = first(q, 'municipality') || first(p, 'municipio', 'municipality') || '';
    var uf = first(q, 'uf') || first(p, 'uf') || (code ? code.slice(0, 2) : '');
    return {
      code: CAR_RE.test(code) ? code : '',
      place: mun ? String(mun) + (uf ? ' (' + String(uf).toUpperCase() + ')' : '') : '',
      area: ha(area),
      areaHa: toNum(area),
      situacao: SITUACAO[st] || '',
      condicao: String(cond || '').trim(),
      tipo: TIPO[tp] || '',
      modulos: modulos(mf),
      inscricao: date(first(q, 'created_at') || first(p, 'dat_criacao', 'created_at')),
      atualizacao: date(first(q, 'updated_at') || first(p, 'data_atualizacao', 'dat_atuali', 'updated_at'))
    };
  }

  // Title: the validated name only when the panel says so (C2a); otherwise the CAR code in two lines.
  function identity(code, panel) {
    var p = panel && panel.ok === true ? panel : null;
    var name = p && p.validated_name_state === 'validated' && p.panel_name_eligible === true ? String(p.validated_name || '').trim() : '';
    if (name) return { named: true, title: name, lines: [name] };
    var c = String(code || '');
    return { named: false, title: c, lines: c.length > 11 ? [c.slice(0, 11), c.slice(11)] : [c] };
  }

  // Search box: CAR code, coordinate or municipality.
  var BR = { s: -34.2, n: 5.6, w: -74.2, e: -28.6 };
  function parseQuery(text) {
    var raw = String(text || '').trim();
    if (!raw) return { kind: 'empty' };
    var compact = raw.replace(/\s+/g, '').toUpperCase();
    if (CAR_RE.test(compact)) return { kind: 'car', code: compact };
    if (/^[A-Z]{2}-\d{7}-?[0-9A-F]*$/.test(compact)) return { kind: 'car-partial' };
    // "-18.8912, -44.1819" (decimal point, comma or semicolon between) or "-18,8912 -44,1819" (decimal comma, space or semicolon between)
    var m = /^(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)$/.exec(raw) || /^(-?\d{1,3}(?:[.,]\d+)?)\s*(?:;|\s)\s*(-?\d{1,3}(?:[.,]\d+)?)$/.exec(raw);
    if (m) {
      var a = toNum(m[1]), b = toNum(m[2]);
      var inLat = function (v) { return v >= BR.s && v <= BR.n; }, inLon = function (v) { return v >= BR.w && v <= BR.e; };
      if (a !== null && b !== null) {
        if (inLat(a) && inLon(b)) return { kind: 'coord', lat: a, lon: b };
        if (inLon(a) && inLat(b)) return { kind: 'coord', lat: b, lon: a };
        return { kind: 'coord-outside' };
      }
    }
    if (/^[-\d\s.,;]+$/.test(raw)) return { kind: 'coord-invalid' };
    return raw.length >= 2 ? { kind: 'city', q: raw.slice(0, 80) } : { kind: 'short' };
  }

  // Map view in the link: v=lat,lon,zoom
  function parseView(v) {
    var m = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d{1,2}(?:\.\d+)?)$/.exec(String(v || ''));
    if (!m) return null;
    var lat = Number(m[1]), lon = Number(m[2]), z = Number(m[3]);
    if (!(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && z >= 3 && z <= 20)) return null;
    return { lat: lat, lon: lon, z: z };
  }
  function viewParam(lat, lon, z) { return lat.toFixed(5) + ',' + lon.toFixed(5) + ',' + Math.round(z); }

  // Parcel grid (same cells as /v1/live/sicar/viewport-v46).
  var STEPS = [0.01, 0.02, 0.04, 0.08, 0.16, 0.32];
  function stepFor(w, s, e, n, cur) {
    var target = Math.sqrt(Math.max(1e-9, (e - w) * (n - s)) / 6);
    if (cur && Math.abs(Math.log(cur / target)) < Math.log(2) * 0.75) return cur;
    var best = STEPS[0];
    STEPS.forEach(function (x) { if (Math.abs(Math.log(x / target)) < Math.abs(Math.log(best / target))) best = x; });
    return best;
  }
  function cellKeys(step, w, s, e, n) {
    var out = [], x0 = Math.floor(w / step + 1e-9), x1 = Math.floor(e / step - 1e-9), y0 = Math.floor(s / step + 1e-9), y1 = Math.floor(n / step - 1e-9);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 400) return out;
    for (var iy = y0; iy <= y1; iy++) for (var ix = x0; ix <= x1; ix++) out.push(step + ':' + ix + ':' + iy);
    return out;
  }

  // User measurement on the sphere (WGS84 radius). The same formula Leaflet.draw uses for areas.
  function geodesicArea(latlngs) {
    var R = 6378137, rad = Math.PI / 180, area = 0, n = latlngs.length;
    if (n < 3) return 0;
    for (var i = 0; i < n; i++) {
      var p1 = latlngs[i], p2 = latlngs[(i + 1) % n];
      area += (p2.lng - p1.lng) * rad * (2 + Math.sin(p1.lat * rad) + Math.sin(p2.lat * rad));
    }
    return Math.abs(area * R * R / 2);
  }
  function distanceText(m) { return m < 1000 ? int(m) + ' m' : num(m / 1000, 2) + ' km'; }

  // ---------------------------------------------------------- Raio-X em uma olhada (puro)
  var SIM = 'sim', NAO = 'nao', PEND = 'pendente';
  var ANSWER = { sim: 'Sim', nao: 'Não', pendente: 'Consulta pendente' };
  function exactPart(obj, label) {
    if (!obj || typeof obj !== 'object') return { label: label, state: PEND };
    var ex = obj.exact || {}, n = ex.occurrence_count;
    if (obj.ok === true && ex.available === true && isNum(n) && n >= 0) return { label: label, state: n > 0 ? SIM : NAO, n: n, ha: isNum(ex.area_unique_ha) ? ex.area_unique_ha : null };
    return { label: label, state: PEND };
  }
  function autosPart(obj) {
    var label = 'auto de infração do IBAMA';
    if (!obj || typeof obj !== 'object') return { label: label, state: PEND };
    var n = obj.occurrence_count, capped = isNum(obj.feature_count_bbox) && obj.feature_count_bbox >= 2000;
    // 200 não é todos: a count cut at the cap that found nothing is not an answer.
    if (obj.ok === true && isNum(n) && n >= 0 && !(capped && n === 0)) return { label: label, state: n > 0 ? SIM : NAO, n: n, floor: capped };
    return { label: label, state: PEND };
  }
  function servicePart(services, key, label) {
    if (!services || typeof services !== 'object') return { label: label, state: PEND };
    var s = services[key];
    if (!s || typeof s !== 'object') return null;
    var n = s.occurrence_count;
    if (s.ok === true && isNum(n) && n >= 0) return { label: label, state: n > 0 ? SIM : NAO, n: n, ha: isNum(s.area_unique_ha) ? s.area_unique_ha : null };
    return { label: label, state: PEND };
  }
  // A found fact answers "Sim" even if another source is pending; "Não" needs every source answered.
  function groupState(parts) {
    var list = parts.filter(Boolean);
    if (!list.length) return PEND;
    if (list.some(function (p) { return p.state === SIM; })) return SIM;
    if (list.some(function (p) { return p.state === PEND; })) return PEND;
    return NAO;
  }
  function names(list) { return list.length > 1 ? list.slice(0, -1).join(', ') + ' e ' + list[list.length - 1] : (list[0] || ''); }
  function pendingText(list) { return 'ainda sem resposta: ' + names(list); }
  function partLine(p, found, none) {
    if (p.state === SIM) return found(p);
    if (p.state === NAO) return none;
    return 'consulta pendente';
  }

  // when: the engine's production time (number) or an already written date (string); unknown -> ''.
  function glance(analysis, fact, when) {
    var a = analysis && typeof analysis === 'object' ? analysis : {};
    when = isNum(when) ? dateTime(when) : (typeof when === 'string' ? when : '');
    var rows = [];

    // 1 - desmatamento (INPE PRODES, a mesma leitura do relatório)
    (function () {
      var r = a.prodes && a.prodes.reading, row = { id: 'desmatamento', q: 'Tem desmatamento registrado?', seal: PEND, lead: '', details: [], source: 'INPE, PRODES', when: when };
      if (r && typeof r === 'object') {
        var ins = r.inside || {}, post = r.post_cutoff_inside || {}, full = r.complete === true;
        if (r.state === 'found' && isNum(ins.count) && ins.count > 0) {
          row.seal = SIM;
          var bits = [(full ? '' : 'pelo menos ') + plural(ins.count, 'desmatamento', 'desmatamentos') + ' dentro do imóvel'];
          if (isNum(ins.area_ha) && ins.area_ha > 0) {
            var pct = isNum(fact && fact.areaHa) && fact.areaHa > 0 ? Math.round(ins.area_ha / fact.areaHa * 100) : null;
            bits.push(ha(ins.area_ha) + (pct !== null && pct <= 100 ? ' (' + pct + '% da área)' : ''));
          }
          var tail = isNum(post.count) && post.count > 0 ? plural(post.count, 'é posterior', 'são posteriores') + ' a 31/07/2019' : (full && post.count === 0 ? 'nenhum é posterior a 31/07/2019' : '');
          row.lead = 'Sim. ' + bits.join(', ') + (tail ? '; ' + tail : '') + '.';
        } else if (r.state === 'not_found' && full) {
          row.seal = NAO;
          row.lead = 'Não. O mapa de desmatamento do INPE não tem registro dentro deste imóvel.';
        }
        ['inside_text', 'boundary_text', 'accumulated_text', 'other_classes_text'].forEach(function (k) {
          if (row.seal !== PEND && typeof r[k] === 'string' && r[k].trim()) row.details.push(r[k].trim());
        });
      }
      if (row.seal === PEND) row.lead = 'Consulta pendente. O mapa de desmatamento do INPE não respondeu nesta consulta.';
      rows.push(row);
    })();

    // 2 - embargo ou auto de infração no local
    (function () {
      var sv = a.territorial_constraints && a.territorial_constraints.services;
      var parts = [exactPart(a.embargos_ibama, 'embargo do IBAMA'), servicePart(sv, 'embargo_icmbio', 'embargo do ICMBio'), autosPart(a.autos_ibama)].filter(Boolean);
      var seal = groupState(parts), row = { id: 'embargo', q: 'Tem embargo ou auto de infração no local?', seal: seal, lead: '', details: [], source: 'IBAMA e ICMBio', when: when };
      var found = parts.filter(function (p) { return p.state === SIM; }), pend = parts.filter(function (p) { return p.state === PEND; });
      if (seal === SIM) {
        row.lead = 'Sim. ' + found.map(function (p) {
          if (p.label === 'auto de infração do IBAMA') return (p.floor ? 'pelo menos ' : '') + plural(p.n, 'auto de infração do IBAMA com local no imóvel', 'autos de infração do IBAMA com local no imóvel');
          return plural(p.n, p.label, p.label.replace('embargo', 'embargos')) + (p.ha > 0 ? ', ' + ha(p.ha) + ' dentro do imóvel' : '');
        }).join('; ') + '.';
      } else if (seal === NAO) {
        row.lead = 'Não. Nenhum embargo do IBAMA ou do ICMBio sobre a área, nem auto de infração do IBAMA com local dentro dela.';
      } else {
        var none = parts.filter(function (p) { return p.state === NAO; }).map(function (p) { return p.label; });
        row.lead = 'Consulta pendente. ' + (none.length ? 'Sem ' + names(none) + ' no imóvel; ' : '') + pendingText(pend.map(function (p) { return p.label; })) + '.';
      }
      parts.forEach(function (p) {
        row.details.push(p.label.charAt(0).toUpperCase() + p.label.slice(1) + ': ' + partLine(p, function (x) { return plural(x.n, 'registro', 'registros') + (x.ha > 0 ? ', ' + ha(x.ha) : ''); }, (p.label === 'auto de infração do IBAMA' ? 'nenhum com local no imóvel' : 'nenhum no imóvel')) + '.');
      });
      if (seal !== PEND && pend.length) row.details.push('Ainda sem resposta: ' + names(pend.map(function (p) { return p.label; })) + '. O que já foi encontrado continua valendo.');
      rows.push(row);
    })();

    // 3 - área protegida ou mineração
    (function () {
      var tc = a.territorial_constraints, sv = tc && tc.services;
      var parts = [
        servicePart(sv, 'terra_indigena', 'terra indígena'),
        servicePart(sv, 'unidade_conservacao', 'unidade de conservação'),
        servicePart(sv, 'quilombola', 'território quilombola'),
        servicePart(sv, 'assentamento', 'assentamento do INCRA'),
        servicePart(sv, 'floresta_publica', 'floresta pública'),
        exactPart(a.anm, 'processo de mineração na ANM')
      ].filter(Boolean);
      var seal = groupState(parts), row = { id: 'protegida', q: 'Está em área protegida ou tem mineração?', seal: seal, lead: '', details: [], source: 'FUNAI, MMA, INCRA, Serviço Florestal e ANM', when: when };
      if (seal === SIM) {
        row.lead = 'Sim. ' + parts.filter(function (p) { return p.state === SIM; }).map(function (p) {
          if (p.label === 'processo de mineração na ANM') return plural(p.n, 'processo de mineração na ANM', 'processos de mineração na ANM') + (p.ha > 0 ? ', ' + ha(p.ha) + ' sobre o imóvel' : '');
          return (p.label.charAt(0).toUpperCase() + p.label.slice(1)) + (p.ha > 0 ? ': ' + ha(p.ha) + ' sobrepostos ao imóvel' : ': ' + plural(p.n, 'registro', 'registros'));
        }).join('; ') + '.';
      } else if (seal === NAO) {
        row.lead = 'Não. Fora de terra indígena, unidade de conservação, território quilombola, assentamento e floresta pública. Nenhum processo de mineração na ANM.';
      } else {
        var none = parts.filter(function (p) { return p.state === NAO; }).map(function (p) { return p.label; });
        var pend = parts.filter(function (p) { return p.state === PEND; }).map(function (p) { return p.label; });
        row.lead = 'Consulta pendente. ' + (none.length ? 'Nada encontrado ' + (none.length === 1 ? 'na base que respondeu' : 'nas ' + none.length + ' bases que responderam') + '; ' : '') + pendingText(pend) + '.';
      }
      parts.forEach(function (p) {
        row.details.push(p.label.charAt(0).toUpperCase() + p.label.slice(1) + ': ' + partLine(p, function (x) { return x.ha > 0 ? ha(x.ha) + ' sobre o imóvel' : plural(x.n, 'registro', 'registros'); }, (p.label === 'processo de mineração na ANM' ? 'nenhum sobre o imóvel' : 'sem sobreposição')) + '.');
      });
      rows.push(row);
    })();

    // 4 - o que o CAR declara (sem selo: é declaração, não achado)
    (function () {
      var f = fact || {}, row = { id: 'car', q: 'O que o CAR declara?', seal: null, lead: '', details: [], source: 'SICAR', when: f.atualizacao ? 'atualizado em ' + f.atualizacao : '' };
      var status = f.situacao ? f.situacao + (f.condicao ? ', ' + lowerFirst(f.condicao) : '') + '.' : '';
      var size = [f.area ? f.area + ' declarados' : '', f.modulos].filter(Boolean).join(', ');
      row.lead = [status, size ? size.charAt(0).toUpperCase() + size.slice(1) + '.' : '', f.inscricao ? 'Inscrito em ' + f.inscricao + '.' : ''].filter(Boolean).join(' ');
      if (!row.lead) { row.seal = PEND; row.lead = 'Consulta pendente. O SICAR não respondeu nesta consulta.'; }
      row.details.push('O CAR mostra onde fica o imóvel e o que foi declarado; não prova quem é o dono. Quem prova é a matrícula do cartório.');
      if (f.tipo) row.details.push('Tipo: ' + f.tipo + '.');
      rows.push(row);
    })();

    // 5 - água (sem selo: falta de autorização registrada não é falta de água)
    (function () {
      var row = { id: 'agua', q: 'Tem água?', seal: null, lead: '', details: [], source: '', when: when }, said = [], src = [];
      var w = a.water_mg, outside = w && /outside_source_coverage|não aplicável/i.test(String(w.detail || ''));
      if (w && typeof w === 'object' && !outside) {
        var n = w.inside_count;
        if (w.ok === true && isNum(n) && n >= 0) {
          said.push(n > 0 ? 'Há ' + plural(n, 'outorga', 'outorgas') + ' de uso de água com ponto dentro do imóvel.' : 'Outorga de uso de água com ponto dentro do imóvel: nenhuma no cadastro consultado.');
          src.push('IGAM e ANA');
        } else row.details.push('Outorgas de uso de água: consulta pendente.');
      }
      var pv = a.pivots_ana;
      if (pv && typeof pv === 'object') {
        var k = pv.intersection_count, partial = isNum(pv.parsed_feature_count) && isNum(pv.feature_count_bbox) && pv.parsed_feature_count < pv.feature_count_bbox;
        var yr = isNum(pv.reference_year) ? ' no mapeamento da ANA de ' + pv.reference_year : ' no mapeamento da ANA';
        if (pv.ok === true && isNum(k) && k >= 0 && !(partial && k === 0)) {
          said.push(k > 0 ? plural(k, 'pivô central de irrigação', 'pivôs centrais de irrigação') + ' no imóvel' + (pv.intersection_area_unique_ha > 0 ? ', ' + ha(pv.intersection_area_unique_ha) : '') + yr + '.' : 'Pivô central de irrigação:' + ' nenhum' + yr + '.');
          src.push('ANA');
        } else row.details.push('Pivôs centrais: consulta pendente.');
      }
      if (said.length) {
        row.lead = said.join(' ');
        row.details.unshift('Não ter autorização registrada não quer dizer que falta água no imóvel.');
      } else { row.seal = PEND; row.lead = 'Consulta pendente. As bases de água não responderam nesta consulta.'; }
      row.source = src.indexOf('IGAM e ANA') >= 0 ? 'IGAM e ANA' : (src[0] || 'IGAM e ANA');
      rows.push(row);
    })();

    // 6 - terra e clima
    (function () {
      var row = { id: 'terra', q: 'Como é a terra e o clima?', seal: null, lead: '', details: [], source: '', when: when }, said = [], src = [];
      var ide = a.ide_layers && typeof a.ide_layers === 'object' ? a.ide_layers : {};
      // IDE-Sisema is a Minas Gerais source: outside MG its layers are not applicable, never pending.
      var mg = String((fact && fact.code) || '').slice(0, 2) === 'MG';
      var applies = function (l) { return mg && l && typeof l === 'object' && !/não aplicável|outside_source_coverage/i.test(String(l.detail || '')); };
      var top = function (l) {
        var list = Array.isArray(l.samples) ? l.samples.filter(function (s) { return s && s.properties; }) : [];
        list.sort(function (x, y) { return (y.intersection_pct_car || 0) - (x.intersection_pct_car || 0); });
        return list[0] || null;
      };
      var soil = ide.soil;
      if (applies(soil)) {
        var s = soil.ok === true && soil.exact_count > 0 ? top(soil) : null, leg = s ? String(s.properties.legenda || '').trim() : '';
        if (leg) {
          said.push('Solo: ' + lowerFirst(leg).replace(/^./, function (c) { return c.toUpperCase(); }) + (isNum(s.intersection_pct_car) ? ', em ' + int(s.intersection_pct_car) + '% da área' : '') + '.');
          src.push('IDE-Sisema (MG)');
        } else if (soil.ok !== true) row.details.push('Mapa de solos: consulta pendente.');
      }
      var ero = ide.erosion;
      if (applies(ero)) {
        var e = ero.ok === true && ero.exact_count > 0 ? top(ero) : null, ind = e ? String(e.properties.indicador || '').trim() : '';
        if (ind) { said.push('Risco potencial de erosão: ' + lowerFirst(ind) + '.'); if (src.indexOf('IDE-Sisema (MG)') < 0) src.push('IDE-Sisema (MG)'); }
        else if (ero.ok !== true) row.details.push('Risco de erosão: consulta pendente.');
      }
      if (applies(ide.slope) && ide.slope.ok !== true) row.details.push('Declividade: consulta pendente.');
      var c = a.climate_nasa;
      if (c && typeof c === 'object') {
        if (c.ok === true && isNum(c.rain_sum_mm) && isNum(c.available_days) && c.available_days > 0) {
          var period = ymd(c.period_start) && ymd(c.period_end) ? ' (' + ymd(c.period_start).slice(0, 5) + ' a ' + ymd(c.period_end) + ')' : '';
          said.push('Nos últimos ' + int(c.available_days) + ' dias' + period + ', ' + int(c.rain_sum_mm) + ' mm de chuva' + (isNum(c.temp_avg_c) ? ' e temperatura média de ' + num(c.temp_avg_c, 1) + ' °C' : '') + '.');
          src.push('NASA POWER');
        } else row.details.push('Clima dos últimos dias: consulta pendente.');
      }
      if (said.length) row.lead = said.join(' ');
      else { row.seal = PEND; row.lead = 'Consulta pendente. As bases de solo e clima não responderam nesta consulta.'; }
      row.source = src.join(', ') || 'IDE-Sisema e NASA POWER';
      rows.push(row);
    })();

    return rows;
  }
  // A later reading only fills what was pending; an answer is never replaced by a later failure.
  function mergeRows(old, fresh) {
    var by = {}; (fresh || []).forEach(function (r) { by[r.id] = r; });
    var changed = false;
    var out = (old || []).map(function (r) {
      var n = by[r.id];
      if (r.seal === PEND && n && n.seal !== PEND) { changed = true; return n; }
      return r;
    });
    return { rows: out, changed: changed };
  }
  function hasPending(rows) { return (rows || []).some(function (r) { return r.seal === PEND; }); }

  var API = {
    CAR_RE: CAR_RE, num: num, ha: ha, int: int, date: date, modulos: modulos, facts: facts, identity: identity,
    parseQuery: parseQuery, parseView: parseView, viewParam: viewParam, stepFor: stepFor, cellKeys: cellKeys,
    geodesicArea: geodesicArea, distanceText: distanceText, glance: glance, mergeRows: mergeRows, hasPending: hasPending, ANSWER: ANSWER
  };
  root.RXO2 = API;
  if (typeof document === 'undefined' || typeof root.L === 'undefined') return;

  // ------------------------------------------------------------------ aplicativo
  var L = root.L, doc = document, body = doc.body;
  var $ = function (s, el) { return (el || doc).querySelector(s); };
  var esc = function (s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var M = root.RXO2_METRICS = { start: performance.now() };
  function mark(name) { if (M[name] === undefined) { M[name] = Math.round(performance.now()); try { performance.mark('rx:' + name); } catch (e) {} } }
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var narrow = function () { return root.matchMedia('(max-width: 639px)').matches; };

  var ICON = function (id) { return '<svg class="ico" aria-hidden="true" focusable="false"><use href="#i-' + id + '"></use></svg>'; };

  // ---- requests: JSON with a deadline; while the portal modules are still loading, wait instead of failing.
  async function bootReady() {
    for (var i = 0; i < 60; i++) {
      try {
        var r = await fetch('/v1/bootstrap/state', { cache: 'no-store' });
        if (r.ok) { var d = await r.json(); if (d.ready || d.error) return !!d.ready; }
      } catch (e) {}
      await sleep(i < 10 ? 500 : 1000);
    }
    return false;
  }
  async function api(url, opt) {
    opt = opt || {};
    for (var round = 0; round < 2; round++) {
      var ctrl = new AbortController(), timer = setTimeout(function () { ctrl.abort(); }, opt.timeout || 30000);
      var stop = function () { ctrl.abort(); };
      if (opt.signal) { if (opt.signal.aborted) return { ok: false, status: 0, aborted: true }; opt.signal.addEventListener('abort', stop); }
      try {
        var r = await fetch(url, { method: opt.method || 'GET', cache: opt.cache || 'default', signal: ctrl.signal, credentials: 'same-origin' });
        var d = null; try { d = await r.json(); } catch (e) {}
        if (r.status === 404 && d && d.detail === 'Not Found' && round === 0 && await bootReady()) continue;
        return { ok: r.ok, status: r.status, data: d };
      } catch (e) {
        return { ok: false, status: 0, aborted: !!(opt.signal && opt.signal.aborted) };
      } finally {
        clearTimeout(timer);
        if (opt.signal) opt.signal.removeEventListener('abort', stop);
      }
    }
    return { ok: false, status: 404 };
  }

  // ---- toast
  var toastTimer = null;
  function toast(text) {
    var el = $('#aviso'); el.textContent = text; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 2800);
  }

  // ---- map
  var BRASIL = L.latLngBounds([-33.8, -73.99], [5.3, -34.8]);
  var map = L.map('mapa', { zoomControl: false, attributionControl: true, minZoom: 3, maxZoom: 20, worldCopyJump: true, zoomSnap: 0.5, preferCanvas: false });
  map.createPane('imoveis').style.zIndex = '390';
  map.createPane('rotulos').style.zIndex = '395';
  map.getPane('rotulos').style.pointerEvents = 'none';
  map.createPane('selecao').style.zIndex = '420';
  map.createPane('medida').style.zIndex = '430';
  var ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/';
  var imagery = L.tileLayer(ESRI + 'World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxNativeZoom: 18, maxZoom: 20, attribution: 'Imagem: Esri, Maxar, Earthstar Geographics' });
  var places = L.tileLayer(ESRI + 'Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', { pane: 'rotulos', maxNativeZoom: 18, maxZoom: 20, opacity: 0.9 });
  imagery.once('load', function () { mark('mapaPronto'); body.classList.add('mapa-pronto'); });
  var baseOn = false;
  function baseLayers() { if (baseOn) return; baseOn = true; imagery.addTo(map); places.addTo(map); }
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" rel="noopener">Leaflet</a>');
  var renderer = L.canvas({ pane: 'imoveis', padding: 0.5, tolerance: root.matchMedia('(pointer: coarse)').matches ? 6 : 2 });
  var parcelLayer = L.layerGroup().addTo(map);
  var selLayer = L.layerGroup().addTo(map);
  var PARCEL = { color: '#F6F1E4', weight: 1.2, opacity: 0.85, fillColor: '#F6F1E4', fillOpacity: 0.07 };

  // ---- parcels by cell (one request per cell, center first, 6 at a time, one retry)
  var G = { step: 0, box: null, cells: new Map(), parcelCells: new Map(), index: new Map(), queue: [], active: 0, epoch: 0 };
  var MIN_Z = 11;
  function dropCell(k) {
    var c = G.cells.get(k); if (!c) return;
    G.cells.delete(k); if (c.ctrl) c.ctrl.abort();
    c.keys.forEach(function (key) {
      var set = G.parcelCells.get(key); if (!set) return;
      set.delete(k);
      if (!set.size) { G.parcelCells.delete(key); var g = G.index.get(key); if (g) { parcelLayer.removeLayer(g); G.index.delete(key); } }
    });
  }
  function resetGrid() { G.epoch++; Array.from(G.cells.keys()).forEach(dropCell); G.queue = []; G.box = null; G.step = 0; }
  function drawCell(cell, features) {
    (features || []).forEach(function (f, i) {
      if (!f || !f.geometry) return;
      var p = f.properties || {}, key = String(p.cod_imovel || ('sem-codigo:' + cell.key + ':' + i)).toUpperCase();
      cell.keys.add(key);
      var set = G.parcelCells.get(key); if (!set) G.parcelCells.set(key, set = new Set()); set.add(cell.key);
      if (G.index.has(key)) return;
      var g = L.geoJSON(f, { renderer: renderer, pane: 'imoveis', style: function () { return PARCEL; } });
      g.on('click', function (e) { onParcelClick(e, f); });
      g.addTo(parcelLayer); G.index.set(key, g);
    });
    if (features && features.length) mark('imoveisDesenhados');
  }
  function pump() {
    while (G.active < 6 && G.queue.length) {
      var c = G.cells.get(G.queue.shift());
      if (c && c.state === 'queued') fetchCell(c);
    }
  }
  async function fetchCell(cell) {
    cell.state = 'loading'; cell.tries++; G.active++;
    var ctrl = new AbortController(), epoch = G.epoch, s = cell.step; cell.ctrl = ctrl;
    var f = function (v) { return v.toFixed(6); };
    var url = '/v1/live/sicar/viewport-v46?west=' + f(cell.ix * s) + '&south=' + f(cell.iy * s) + '&east=' + f((cell.ix + 1) * s) + '&north=' + f((cell.iy + 1) * s) + '&cell=' + s;
    var res = await api(url, { signal: ctrl.signal, timeout: 50000 });
    G.active = Math.max(0, G.active - 1); cell.ctrl = null; pump();
    if (epoch !== G.epoch || G.cells.get(cell.key) !== cell) return;
    var d = res.data;
    if (res.ok && d && Array.isArray(d.features)) {
      cell.state = 'ok'; cell.truncated = !!d.truncated; cell.partial = Number(d.partial_failures || 0) > 0;
      drawCell(cell, d.features);
      if (cell.partial && cell.tries < 2) retryLater(cell);
    } else if (!res.aborted) {
      cell.state = 'fail'; if (cell.tries < 2) retryLater(cell);
    }
    evict(); notice();
  }
  function retryLater(cell) {
    setTimeout(function () {
      if (G.cells.get(cell.key) !== cell || cell.state === 'loading' || cell.state === 'queued' || (cell.state === 'ok' && !cell.partial)) return;
      cell.state = 'queued'; G.queue.unshift(cell.key); pump();
    }, 1500);
  }
  function plan(keys) {
    var want = new Set(keys);
    Array.from(G.cells.entries()).forEach(function (kv) { if (kv[1].state === 'queued' && !want.has(kv[0])) G.cells.delete(kv[0]); });
    G.queue = [];
    keys.forEach(function (k) {
      var c = G.cells.get(k);
      if (c && c.state !== 'fail') { if (c.state === 'queued') G.queue.push(k); return; }
      var p = k.split(':').map(Number);
      G.cells.set(k, { key: k, step: p[0], ix: p[1], iy: p[2], state: 'queued', tries: 0, keys: c ? c.keys : new Set(), truncated: false, partial: false, ctrl: null });
      G.queue.push(k);
    });
    pump();
  }
  function bounds() { var b = map.getBounds(); return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]; }
  function loadParcels() {
    if (map.getZoom() < MIN_Z) { resetGrid(); notice(); return; }
    var bb = bounds(), w = bb[0], s = bb[1], e = bb[2], n = bb[3];
    if (!(e > w && n > s) || Math.max(e - w, n - s) > 1.2) { notice(); return; }
    var step = stepFor(w, s, e, n, G.step), visible = cellKeys(step, w, s, e, n);
    var box = G.box, inside = box && G.step === step && w >= box[0] && s >= box[1] && e <= box[2] && n <= box[3];
    var covered = visible.every(function (k) { var c = G.cells.get(k); return c && c.state !== 'fail'; });
    if (inside && covered) { evict(); notice(); return; }
    G.step = step;
    var pw = (e - w) * 0.25, ph = (n - s) * 0.25, pad = [w - pw, s - ph, e + pw, n + ph]; G.box = pad;
    var cx = (w + e) / 2, cy = (s + n) / 2, dist = function (k) { var p = k.split(':').map(Number); return Math.hypot((p[1] + 0.5) * step - cx, (p[2] + 0.5) * step - cy); };
    var seen = new Set(visible), margin = cellKeys(step, pad[0], pad[1], pad[2], pad[3]).filter(function (k) { return !seen.has(k); });
    if (visible.length + margin.length > 64) margin = [];
    plan(visible.sort(function (a, b) { return dist(a) - dist(b); }).concat(margin.sort(function (a, b) { return dist(a) - dist(b); })));
    evict(); notice();
  }
  function evict() {
    if (!G.step) return;
    var bb = bounds(), dw = bb[2] - bb[0], dh = bb[3] - bb[1], R = [bb[0] - dw, bb[1] - dh, bb[2] + dw, bb[3] + dh];
    var settled = cellKeys(G.step, bb[0], bb[1], bb[2], bb[3]).every(function (k) { var c = G.cells.get(k); return c && (c.state === 'ok' || c.state === 'fail'); });
    Array.from(G.cells.entries()).forEach(function (kv) {
      var c = kv[1], x0 = c.ix * c.step, y0 = c.iy * c.step, far = x0 + c.step < R[0] || x0 > R[2] || y0 + c.step < R[1] || y0 > R[3];
      if (far || (c.step !== G.step && (settled || c.state !== 'ok'))) dropCell(kv[0]);
    });
  }
  // One quiet status line under the search box - never a label over the map.
  function notice() {
    var el = $('#situacao'), msg = '', retry = false;
    if (S.page !== 'mapa' || S.pin) return;
    if (map.getZoom() < MIN_Z) msg = 'Aproxime o mapa para ver os imóveis do CAR.';
    else if (G.step) {
      var bb = bounds(), vis = cellKeys(G.step, bb[0], bb[1], bb[2], bb[3]).map(function (k) { return G.cells.get(k); });
      var pending = vis.some(function (c) { return !c || c.state === 'queued' || c.state === 'loading'; });
      var failed = vis.some(function (c) { return c && (c.state === 'fail' || (c.state === 'ok' && c.partial)); });
      var truncated = vis.some(function (c) { return c && c.state === 'ok' && c.truncated; });
      var drawn = vis.some(function (c) { return c && c.keys && c.keys.size; });
      if (pending) msg = drawn ? '' : 'Carregando os imóveis desta área…';
      else if (failed) { msg = 'Parte dos imóveis desta área não carregou.'; retry = true; }
      else if (truncated) msg = 'Há mais imóveis nesta área do que o SICAR entrega de uma vez. Aproxime para ver todos.';
      else if (!drawn) msg = 'O SICAR não tem imóvel desenhado nesta área do mapa.';
      if (!pending) mark('imoveisCompletos');
    }
    if (el.dataset.msg === msg + retry) return;
    el.dataset.msg = msg + retry;
    el.innerHTML = msg ? '<span>' + esc(msg) + '</span>' + (retry ? '<button type="button" class="link" data-acao="recarregar">Tentar de novo</button>' : '') : '';
    el.hidden = !msg;
  }

  // ---- state
  var S = { page: body.dataset.route === 'imovel' ? 'mapa' : (body.dataset.route || 'mapa'), sel: null, measuring: false, pts: [], panelFlight: new Map(), reading: new Map() };

  // ---- selection and card
  function selectionStyle(g) {
    selLayer.clearLayers();
    if (!g) return;
    L.geoJSON(g, { pane: 'selecao', interactive: false, style: { color: '#1F4D3A', weight: 7, opacity: 0.55, fill: false } }).addTo(selLayer);
    L.geoJSON(g, { pane: 'selecao', interactive: false, style: { color: '#FFFFFF', weight: 2.5, opacity: 1, fillColor: '#FFFFFF', fillOpacity: 0.12 } }).addTo(selLayer);
  }
  function panelOnce(code) {
    var p = S.panelFlight.get(code);
    if (!p) {
      p = api('/v1/live/map-panel/' + encodeURIComponent(code), { timeout: 60000 }).then(function (r) {
        if (!(r.ok && r.data && r.data.ok === true)) S.panelFlight.delete(code);
        return r;
      });
      S.panelFlight.set(code, p);
    }
    return p;
  }
  function select(props, geometry, opts) {
    opts = opts || {};
    var f = facts(props);
    if (!f.code) return;
    closeMeasure();
    S.sel = { code: f.code, props: props || {}, geometry: geometry || null, panel: null, at: Date.now() };
    selectionStyle(geometry);
    setUrl('/novo/imovel/' + f.code);
    renderCard();
    if (opts.fit && geometry) fitSelection(opts.animate);
    mark('cartao');
    panelOnce(f.code).then(function (r) {
      if (!S.sel || S.sel.code !== f.code || !(r.ok && r.data && r.data.ok === true && String(r.data.car_code || '').toUpperCase() === f.code)) return;
      S.sel.panel = r.data;
      if (!S.sel.geometry && r.data.geometry) { S.sel.geometry = r.data.geometry; selectionStyle(r.data.geometry); }
      renderCard(true);
      if (!$('#leitura').hidden) renderReadingHead();
    });
    wakeLater();
  }
  function fitSelection(animate) {
    if (!S.sel || !S.sel.geometry) return;
    var b = L.geoJSON(S.sel.geometry).getBounds(), size = map.getSize();
    var readingOpen = !$('#leitura').hidden;
    var opt = narrow()
      ? { paddingTopLeft: [24, 76], paddingBottomRight: [24, readingOpen ? 40 : Math.min(size.y * 0.7, ($('#cartao').offsetHeight || 380) + 20)], maxZoom: 17 }
      : { paddingTopLeft: [readingOpen ? 480 : 60, 90], paddingBottomRight: [readingOpen ? 80 : Math.min(420, size.x * 0.4), 60], maxZoom: 17 };
    opt.animate = animate !== false;
    map.fitBounds(b, opt);
  }
  function field(label, value, note) {
    if (!value) return '';
    return '<div class="campo"><dt>' + esc(label) + '</dt><dd>' + esc(value) + (note ? '<span class="nota">' + esc(note) + '</span>' : '') + '</dd></div>';
  }
  function plate(id, cls) {
    return '<h2 class="placa ' + (cls || '') + (id.named ? ' com-nome' : '') + '" id="' + (cls === 'placa-leitura' ? 'leituraTitulo' : 'cartaoTitulo') + '">' + id.lines.map(function (l) { return '<span>' + esc(l) + '</span>'; }).join('') + '</h2>';
  }
  function renderCard(update) {
    var el = $('#cartao'), sel = S.sel;
    if (!sel) { el.hidden = true; el.innerHTML = ''; body.classList.remove('com-cartao'); return; }
    var f = facts(sel.props, sel.panel), id = identity(f.code, sel.panel);
    el.innerHTML =
      '<span class="seta" aria-hidden="true"></span>' +
      '<div class="cartao-topo">' + plate(id) +
      '<button type="button" class="icone fechar" data-acao="fechar-cartao" aria-label="Fechar o cartão">' + ICON('x') + '</button></div>' +
      '<div class="cartao-sub">' + '<span class="lugar">' + esc(f.place || f.code.slice(0, 2)) + '</span>' +
      (id.named ? '<span class="codigo">' + esc(f.code) + '</span>' : '<span class="codigo-nota">Identificado pelo código do CAR</span>') +
      '<button type="button" class="icone copiar" data-acao="copiar-codigo" aria-label="Copiar o código do CAR">' + ICON('copiar') + '</button></div>' +
      '<dl class="campos">' + field('Área declarada', f.area) + field('Situação', f.situacao, f.condicao) + field('Tipo', f.tipo) +
      field('Tamanho', f.modulos) + field('Inscrito em', f.inscricao) + field('Atualizado em', f.atualizacao) + '</dl>' +
      '<button type="button" class="botao principal" data-acao="abrir-leitura">Ver Raio-X</button>' +
      '<div class="acoes"><button type="button" class="botao secundario" data-acao="compartilhar-imovel">' + ICON('compartilhar') + '<span>Compartilhar</span></button>' +
      '<a class="botao secundario" href="/v1/exports/property/' + encodeURIComponent(f.code) + '/kml" download>' + ICON('baixar') + '<span>Mapa KML</span></a></div>';
    el.hidden = !$('#leitura').hidden;
    body.classList.toggle('com-cartao', !el.hidden);
    el.dataset.car = f.code;
    if (!update) { el.classList.remove('entra'); void el.offsetWidth; el.classList.add('entra'); }
    placeCard();
  }
  // Desktop: the card sits beside the property (never over it), with a pointer. Phone: bottom sheet.
  function placeCard() {
    var el = $('#cartao');
    if (el.hidden || !S.sel) return;
    if (narrow() || !S.sel.geometry) {
      el.classList.add('folha'); el.style.left = el.style.top = '';
      // Phone: the sheet covers the bottom of the map, so the property moves into the part that stays visible.
      if (S.sel.geometry && !S.sel.moved) {
        S.sel.moved = true;
        var gb = L.geoJSON(S.sel.geometry).getBounds(), top = map.latLngToContainerPoint(gb.getNorthWest()), bot = map.latLngToContainerPoint(gb.getSouthEast());
        var free = map.getSize().y - el.offsetHeight, lo = 70, hi = free - 12;
        if (bot.y - top.y > hi - lo || bot.x - top.x > map.getSize().x - 24) fitSelection(true);
        else if (top.y < lo || bot.y > hi) map.panBy([0, (top.y + bot.y) / 2 - (lo + hi) / 2]);
      }
      return;
    }
    el.classList.remove('folha');
    var b = L.geoJSON(S.sel.geometry).getBounds(), size = map.getSize();
    var nw = map.latLngToContainerPoint(b.getNorthWest()), se = map.latLngToContainerPoint(b.getSouthEast());
    var w = el.offsetWidth, h = el.offsetHeight, gap = 18, right = size.x - 76, topMin = 84;
    var cy = Math.max(topMin, Math.min((nw.y + se.y) / 2, size.y - 16));
    var x, side;
    if (se.x + gap + w <= right) { x = se.x + gap; side = 'esquerda'; }
    else if (nw.x - gap - w >= 12) { x = nw.x - gap - w; side = 'direita'; }
    else {
      // No room beside the property: move the map once so the card never covers it.
      if (!S.sel.moved) {
        S.sel.moved = true;
        var need = se.x + gap + w - right;
        if (se.x - nw.x + gap + w + 12 <= right && need > 0) map.panBy([need, 0]); else fitSelection(true);
        return;
      }
      x = Math.max(12, right - w); side = 'nenhum';
    }
    var y = Math.max(topMin, Math.min(cy - h / 2, size.y - h - 16));
    el.style.left = Math.round(x) + 'px'; el.style.top = Math.round(y) + 'px';
    el.dataset.seta = side;
    var arrow = $('.seta', el);
    if (arrow) arrow.style.top = Math.round(Math.max(22, Math.min(cy - y, h - 22))) + 'px';
  }
  function closeCard() {
    S.sel = null; selectionStyle(null); renderCard(); closeReading(true);
    setUrl('/novo');
  }

  function onParcelClick(e, feature) {
    if (S.measuring) return;
    L.DomEvent.stopPropagation(e);
    M.clique = Math.round(performance.now());
    var p = feature.properties || {};
    select(p, feature.geometry, {});
  }
  map.on('click', function (e) {
    if (S.measuring) { addPoint(e.latlng); return; }
    if (map.getZoom() < MIN_Z) return;
    // A click on no drawn outline asks SICAR at the point only when the cell there is not a complete answer.
    var k = cellKeys(G.step || 0.04, e.latlng.lng, e.latlng.lat, e.latlng.lng + 1e-7, e.latlng.lat + 1e-7)[0], c = G.cells.get(k);
    if (c && c.state === 'ok' && !c.truncated && !c.partial) { if (S.sel) closeCard(); return; }
    resolvePoint(e.latlng.lat, e.latlng.lng);
  });
  var resolving = 0;
  async function resolvePoint(lat, lon) {
    var mine = ++resolving;
    toast('Procurando o imóvel neste ponto…');
    var r = await api('/v1/live/resolve?lat=' + lat.toFixed(6) + '&lon=' + lon.toFixed(6), { timeout: 45000 });
    if (mine !== resolving) return;
    if (r.ok && r.data && r.data.ok && r.data.property) {
      var p = r.data.property;
      select({ cod_imovel: p.car_code, municipio: p.municipality, uf: p.uf, area: p.area_ha, status_imovel: p.status, condicao: p.condition, tipo_imovel: p.type, m_fiscal: p.fiscal_modules }, r.data.geometry, {});
      $('#aviso').hidden = true;
    } else if (r.status === 404 && r.data && r.data.detail && r.data.detail !== 'Not Found') toast('O SICAR não tem imóvel neste ponto.');
    else toast('A consulta ao SICAR não respondeu agora. Toque de novo para tentar.');
  }

  // ---- engine wake (same rule as the portal: only with intention, 1 per 10 min per tab)
  var wakeTimer = null;
  function wake() {
    var K = 'rx-o2-wake', now = Date.now();
    try { if (now - (+sessionStorage.getItem(K) || 0) < 600000) return; sessionStorage.setItem(K, String(now)); } catch (e) { if (root.__rxO2Woke && now - root.__rxO2Woke < 600000) return; root.__rxO2Woke = now; }
    try { fetch('/v1/live/report-engine/wake', { method: 'POST', cache: 'no-store', keepalive: true, credentials: 'same-origin' }).catch(function () {}); } catch (e) {}
  }
  function wakeLater() { clearTimeout(wakeTimer); wakeTimer = setTimeout(function () { if (S.sel) wake(); }, 4000); }

  // ---- Raio-X em uma olhada
  var ENGINE_CACHE_MS = 190000;
  async function readingAttempt(code, alive) {
    var enc = encodeURIComponent(code);
    var sameCar = function (a) { return !!a && typeof a === 'object' && String(((a.car || {}).properties || {}).cod_imovel || '').toUpperCase() === code; };
    var produced = function () {
      for (var i = 0; i < arguments.length; i++) {
        var o = arguments[i], v = o && typeof o === 'object' ? (o.completed_at || (o.progressive && o.progressive.completed_at)) : null;
        var t = v ? Date.parse(v) : NaN; if (isFinite(t) && t <= Date.now() + 300000) return t;
      }
      return null;
    };
    var first = await api('/v1/live/quick/' + enc + '?deep=1', { timeout: 120000, cache: 'no-store' });
    if (!first.ok || !first.data || first.data.ok === false) return null;
    var d = first.data, ds = d.deep_state || {};
    // Only a cache hit answers on the first response; otherwise the engine may still hold the previous run.
    if (d.mode === 'quick-cache') {
      var done = ds.state === 'ready' && ds.analysis ? ds.analysis : d.analysis;
      if (done) return sameCar(done) ? { analysis: done, at: produced(ds, done, d.analysis) } : null;
    }
    var until = Date.now() + 240000, misses = 0, running = false;
    for (var i = 0; Date.now() < until; i++) {
      await sleep(i < 4 ? 1500 : 2500);
      if (!alive()) return null;
      var st = await api('/v1/live/progressive/status/' + enc, { timeout: 20000, cache: 'no-store' });
      if (st.ok && st.data && st.data.state === 'ready') {
        if (!sameCar(st.data.analysis)) return null;
        var t = produced(st.data, st.data.analysis);
        return { analysis: st.data.analysis, at: t !== null ? t : (running ? Date.now() : null) };
      }
      if (st.ok && st.data && st.data.state === 'running') running = true;
      if (st.data && st.data.state === 'failed') return null;
      misses = st.ok ? 0 : misses + 1; if (misses >= 3) return null;
    }
    return null;
  }
  function readingAlive(code, entry) { return S.reading.get(code) === entry; }
  async function startReading(code, manual) {
    var entry = S.reading.get(code);
    if (entry && (entry.phase === 'loading' || (!manual && entry.phase === 'ready'))) { renderReading(); return; }
    if (manual && entry && entry.phase === 'ready') return refill(code, entry);
    entry = { phase: 'loading', rows: null, at: null, started: Date.now() };
    S.reading.set(code, entry); renderReading();
    var res = null;
    for (var attempt = 0; attempt < 2 && !res; attempt++) {
      if (attempt) await sleep(2000);
      if (!readingAlive(code, entry)) return;
      try { res = await readingAttempt(code, function () { return readingAlive(code, entry); }); } catch (e) { res = null; }
    }
    if (!readingAlive(code, entry)) return;
    if (res) {
      entry.phase = 'ready'; entry.analysis = res.analysis; entry.at = res.at !== null ? res.at : null; entry.received = Date.now();
    } else entry.phase = 'failed';
    entry.done = Date.now();
    mark('leituraPronta');
    renderReading();
  }
  async function refill(code, entry) {
    var base = isNum(entry.at) ? entry.at : entry.done, when = Math.max(Date.now(), base + ENGINE_CACHE_MS);
    entry.fill = { state: 'scheduled', when: when }; renderReading();
    await sleep(Math.max(0, when - Date.now()));
    if (!readingAlive(code, entry)) return;
    entry.fill = { state: 'running' }; renderReading();
    var res = null;
    try { res = await readingAttempt(code, function () { return readingAlive(code, entry); }); } catch (e) { res = null; }
    if (!readingAlive(code, entry)) return;
    if (res) {
      var fact = facts(S.sel && S.sel.code === code ? S.sel.props : {}, S.sel && S.sel.panel);
      var m = mergeRows(entry.rows || glance(entry.analysis, fact, readingWhen(entry)), glance(res.analysis, fact, res.at !== null ? res.at : readingWhen({ received: Date.now() })));
      entry.rows = m.rows; if (m.changed) entry.refilled = res.at || Date.now();
    }
    entry.fill = { state: 'idle', tried: true }; entry.done = Date.now(); renderReading();
  }
  // Without the engine's time, an answer from its cache is at most ENGINE_CACHE_MS old: the day is certain only if that window did not cross midnight.
  function readingWhen(entry) {
    if (isNum(entry.at)) return entry.at;
    var d1 = date(new Date(entry.received).toISOString()), d0 = date(new Date(entry.received - ENGINE_CACHE_MS).toISOString());
    return d1 && d1 === d0 ? d1 : '';
  }
  function renderReadingHead() {
    var sel = S.sel, head = $('#leituraCabeca');
    if (!sel || !head) return;
    var f = facts(sel.props, sel.panel), id = identity(f.code, sel.panel);
    head.innerHTML = plate(id, 'placa-leitura') + (f.place ? '<p class="lugar">' + esc(f.place) + '</p>' : '');
  }
  function sealHtml(row) {
    if (!row.seal) return '<span class="selo vazio" aria-hidden="true"></span>';
    return '<span class="selo ' + row.seal + '" aria-hidden="true"></span>';
  }
  // The answer word is shown on its own line; the sentence keeps the rest, starting with a capital letter.
  function leadText(row) {
    var t = row.seal ? row.lead.replace(/^(Sim|Não|Consulta pendente)\.\s*/, '') : row.lead;
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
  }
  function rowHtml(row, i) {
    var answer = row.seal ? ANSWER[row.seal] : '';
    var metaWhen = row.when ? '<span>' + esc(row.id === 'car' ? row.when : 'Consulta de ' + row.when) + '</span>' : '';
    return '<li class="pergunta ' + (row.seal || 'info') + '" data-pergunta="' + esc(row.id) + '">' +
      '<button type="button" class="pergunta-botao" aria-expanded="false" aria-controls="detalhe-' + i + '">' + sealHtml(row) +
      '<span class="pergunta-texto"><span class="q">' + esc(row.q) + '</span>' +
      (answer ? '<span class="resposta">' + esc(answer) + '</span>' : '') +
      '<span class="lead">' + esc(leadText(row)) + '</span>' +
      '<span class="meta"><span>' + esc(row.source) + '</span>' + metaWhen + '</span></span>' + ICON('abrir') + '</button>' +
      '<div class="detalhe" id="detalhe-' + i + '" hidden>' + row.details.map(function (t) { return '<p>' + esc(t) + '</p>'; }).join('') + '</div></li>';
  }
  function renderReading() {
    var box = $('#leituraCorpo'), sel = S.sel;
    if (!box || !sel || $('#leitura').hidden) return;
    var entry = S.reading.get(sel.code), status = $('#leituraSituacao');
    box.setAttribute('aria-busy', entry && entry.phase === 'loading' ? 'true' : 'false');
    if (!entry || entry.phase === 'loading') {
      var waited = entry ? Date.now() - entry.started : 0;
      status.innerHTML = '<span class="gira" aria-hidden="true"></span><span>Consultando as fontes oficiais. ' + (waited > 20000 ? 'Algumas fontes oficiais demoram a responder; a leitura aparece aqui assim que chegar.' : 'Costuma levar de alguns segundos a dois minutos.') + '</span>';
      box.innerHTML = '<ol class="perguntas carregando">' + ['Tem desmatamento registrado?', 'Tem embargo ou auto de infração no local?', 'Está em área protegida ou tem mineração?', 'O que o CAR declara?', 'Tem água?', 'Como é a terra e o clima?'].map(function (q) {
        return '<li class="pergunta esqueleto"><span class="selo vazio" aria-hidden="true"></span><span class="pergunta-texto"><span class="q">' + esc(q) + '</span><span class="barra"></span></span></li>';
      }).join('') + '</ol>';
      if (entry && !entry.tick) entry.tick = setTimeout(function () { entry.tick = null; if (entry.phase === 'loading') renderReading(); }, 21000);
      return;
    }
    if (entry.phase === 'failed') {
      status.innerHTML = '';
      box.innerHTML = '<div class="estado pendente"><span class="selo pendente" aria-hidden="true"></span><div><p><strong>Consulta pendente.</strong> As fontes oficiais não responderam agora; nada foi presumido.</p><button type="button" class="botao secundario" data-acao="ler-de-novo">Consultar de novo</button></div></div>';
      return;
    }
    var fact = facts(sel.props, sel.panel);
    var rows = entry.rows || glance(entry.analysis, fact, readingWhen(entry));
    entry.rows = rows;
    var stamp = isNum(entry.at) ? 'Consulta feita em ' + dateTime(entry.at) + '.' : (readingWhen(entry) ? 'Consulta de ' + readingWhen(entry) + '.' : '');
    if (isNum(entry.refilled)) stamp += ' Pendências consultadas de novo em ' + dateTime(entry.refilled) + '.';
    status.innerHTML = '<span>' + esc(stamp) + '</span>';
    var fill = '';
    if (hasPending(rows)) {
      var fl = entry.fill || {};
      if (fl.state === 'scheduled') fill = '<p class="refazer">Nova tentativa das consultas pendentes às ' + esc(new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' }).format(new Date(fl.when))) + '.</p>';
      else if (fl.state === 'running') fill = '<p class="refazer"><span class="gira" aria-hidden="true"></span>Consultando de novo as fontes pendentes…</p>';
      else fill = '<p class="refazer"><span>Há consultas pendentes.</span><button type="button" class="botao secundario" data-acao="ler-de-novo">Consultar de novo</button></p>';
    }
    var open = Array.from(box.querySelectorAll('.pergunta-botao[aria-expanded="true"]')).map(function (b) { return b.parentNode.dataset.pergunta; });
    box.innerHTML = '<ol class="perguntas">' + rows.map(rowHtml).join('') + '</ol>' + fill +
      '<p class="rodape-leitura">Consultas em bases públicas oficiais. Não substitui certidão de cartório, vistoria ou laudo técnico.</p>';
    open.forEach(function (id) { var li = box.querySelector('[data-pergunta="' + id + '"]'); if (li) toggleRow(li.querySelector('.pergunta-botao'), true); });
  }
  function toggleRow(btn, force) {
    var li = btn.parentNode, det = li.querySelector('.detalhe'), on = force !== undefined ? force : btn.getAttribute('aria-expanded') !== 'true';
    btn.setAttribute('aria-expanded', on ? 'true' : 'false'); det.hidden = !on; li.classList.toggle('aberta', on);
  }
  var lastFocus = null;
  function openReading() {
    if (!S.sel) return;
    lastFocus = doc.activeElement;
    var el = $('#leitura');
    el.innerHTML = '<div class="leitura-topo"><button type="button" class="icone voltar" data-acao="fechar-leitura" aria-label="Voltar ao cartão">' + ICON('voltar') + '</button><p class="leitura-marca">Raio-X em uma olhada</p></div>' +
      '<div id="leituraCabeca" class="leitura-cabeca"></div><p id="leituraSituacao" class="leitura-situacao" role="status" aria-live="polite"></p><div id="leituraCorpo" class="leitura-corpo"></div>';
    el.hidden = false; body.classList.add('com-leitura');
    $('#cartao').hidden = true;
    renderReadingHead(); renderReading();
    startReading(S.sel.code, false);
    wake();
    if (!narrow()) fitSelection();
    var t = $('.voltar', el); if (t) t.focus({ preventScroll: true });
  }
  function closeReading(silent) {
    var el = $('#leitura');
    if (el.hidden) return;
    el.hidden = true; el.innerHTML = ''; body.classList.remove('com-leitura');
    if (!silent && S.sel) { $('#cartao').hidden = false; body.classList.add('com-cartao'); placeCard(); var b = $('[data-acao="abrir-leitura"]'); if (b) b.focus({ preventScroll: true }); }
  }

  // ---- search
  var sugg = { items: [], active: -1, timer: null, seq: 0 };
  function showSuggestions(items) {
    var ul = $('#sugestoes'), input = $('#q');
    sugg.items = items; sugg.active = -1;
    ul.innerHTML = items.map(function (it, i) { return '<li role="option" id="sug-' + i + '" data-i="' + i + '" aria-selected="false">' + esc(it.name) + ' <span>(' + esc(it.uf) + ')</span></li>'; }).join('');
    ul.hidden = !items.length; input.setAttribute('aria-expanded', items.length ? 'true' : 'false');
  }
  function hideSuggestions() { showSuggestions([]); $('#q').removeAttribute('aria-activedescendant'); }
  // A message from the search or the link stays until the person touches the map or the search box.
  function say(msg, kind) {
    S.pin = !!msg;
    var el = $('#situacao'); el.dataset.msg = ''; el.hidden = !msg; el.innerHTML = msg ? '<span class="' + (kind || '') + '">' + esc(msg) + '</span>' : '';
  }
  async function citySuggest(q) {
    var mine = ++sugg.seq;
    var r = await api('/v1/live/cities?q=' + encodeURIComponent(q), { timeout: 8000 });
    if (mine !== sugg.seq) return null;
    if (r.ok && r.data && Array.isArray(r.data.items)) return r.data.items;
    return r.status === 422 ? [] : null;
  }
  function goCity(it) {
    hideSuggestions();
    var bb = (it.boundingbox || []).map(Number);
    if (bb.length === 4 && bb.every(isFinite)) map.fitBounds([[bb[0], bb[2]], [bb[1], bb[3]]], { maxZoom: 13 });
    else if (isFinite(it.lat) && isFinite(it.lon)) map.setView([it.lat, it.lon], 12);
    $('#q').value = it.name + ' (' + it.uf + ')';
    say('');
  }
  async function openCar(code, fromLink) {
    say('Buscando o imóvel no SICAR…');
    var early = fromLink && !S.hasView ? setTimeout(baseLayers, 1200) : null;
    var r = await api('/v1/live/car/' + encodeURIComponent(code), { timeout: 80000 });
    clearTimeout(early);
    var car = r.data && r.data.car;
    if (r.ok && car && car.ok && car.geometry && String((car.properties || {}).cod_imovel || '').toUpperCase() === code) {
      say('');
      select(car.properties, car.geometry, { fit: !fromLink || !S.hasView, animate: false });
      baseLayers();
      mark('cartaoPeloLink');
      return true;
    }
    var nf = r.status === 404 && r.data && r.data.detail && r.data.detail.car && r.data.detail.car.not_found === true;
    baseLayers();
    if (fromLink && !S.hasView) map.fitBounds(BRASIL, { animate: false });
    say(nf ? 'O SICAR não tem imóvel com este código do CAR.' : 'O SICAR não respondeu agora. Tente de novo em instantes.', nf ? 'vazio' : 'pendente');
    if (fromLink && !nf) { var el = $('#situacao'); el.insertAdjacentHTML('beforeend', '<button type="button" class="link" data-acao="abrir-link">Tentar de novo</button>'); }
    return false;
  }
  var pin = null;
  async function onSearch(ev) {
    if (ev) ev.preventDefault();
    var input = $('#q'), text = input.value;
    if (sugg.active >= 0 && sugg.items[sugg.active]) return goCity(sugg.items[sugg.active]);
    var q = parseQuery(text);
    hideSuggestions();
    if (q.kind === 'car') { input.value = q.code; return openCar(q.code); }
    if (q.kind === 'car-partial') return say('O código do CAR está incompleto. Ele tem este formato: MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F.', 'vazio');
    if (q.kind === 'coord') {
      say('');
      map.setView([q.lat, q.lon], 16);
      if (pin) map.removeLayer(pin);
      pin = L.circleMarker([q.lat, q.lon], { pane: 'medida', radius: 7, color: '#FFFFFF', weight: 3, fillColor: '#1F4D3A', fillOpacity: 1, interactive: false }).addTo(map);
      return resolvePoint(q.lat, q.lon);
    }
    if (q.kind === 'coord-outside') return say('A coordenada fica fora do Brasil. Use latitude e longitude, por exemplo -18.8912, -44.1819.', 'vazio');
    if (q.kind === 'coord-invalid') return say('Não entendemos a coordenada. Use latitude e longitude, por exemplo -18.8912, -44.1819.', 'vazio');
    if (q.kind === 'city') {
      say('Buscando o município…');
      var items = await citySuggest(q.q);
      if (items === null) return say('A busca de município não respondeu agora. Tente de novo.', 'pendente');
      if (!items.length) return say('Nenhum município com esse nome. Confira a grafia ou busque pelo código do CAR.', 'vazio');
      return goCity(items[0]);
    }
    say('Digite o código do CAR, o nome do município ou uma coordenada.', 'vazio');
  }
  function onInput() {
    var text = $('#q').value, q = parseQuery(text);
    clearTimeout(sugg.timer);
    if (q.kind !== 'city') { hideSuggestions(); return; }
    sugg.timer = setTimeout(async function () {
      var items = await citySuggest(q.q);
      if (items && $('#q').value === text) showSuggestions(items);
    }, 180);
  }
  function onKey(e) {
    var n = sugg.items.length;
    if (e.key === 'Escape') { hideSuggestions(); return; }
    if (!n || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
    e.preventDefault();
    sugg.active = (sugg.active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
    Array.from($('#sugestoes').children).forEach(function (li, i) { li.setAttribute('aria-selected', i === sugg.active ? 'true' : 'false'); });
    $('#q').setAttribute('aria-activedescendant', 'sug-' + sugg.active);
  }

  // ---- tools: location, measure, share
  var me = null;
  function locate() {
    if (!navigator.geolocation) return toast('Este navegador não informa a localização.');
    toast('Buscando sua localização…');
    navigator.geolocation.getCurrentPosition(function (pos) {
      var ll = [pos.coords.latitude, pos.coords.longitude];
      if (me) map.removeLayer(me);
      me = L.layerGroup([
        L.circle(ll, { pane: 'medida', radius: Math.min(pos.coords.accuracy || 0, 2000), color: '#FFFFFF', weight: 1, fillColor: '#FFFFFF', fillOpacity: 0.12, interactive: false }),
        L.circleMarker(ll, { pane: 'medida', radius: 7, color: '#FFFFFF', weight: 3, fillColor: '#2F6FB0', fillOpacity: 1, interactive: false })
      ]).addTo(map);
      map.setView(ll, Math.max(map.getZoom(), 15));
      $('#aviso').hidden = true;
    }, function (err) {
      toast(err && err.code === 1 ? 'A localização está bloqueada. Libere nas permissões do navegador.' : 'Não foi possível obter sua localização agora.');
    }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
  }
  var measureLayer = L.layerGroup().addTo(map);
  function toggleMeasure() { if (S.measuring) closeMeasure(); else openMeasure(); }
  function openMeasure() {
    S.measuring = true; S.pts = [];
    body.classList.add('medindo');
    $('[data-ferramenta="medir"]').setAttribute('aria-pressed', 'true');
    $('#medida').hidden = false;
    drawMeasure();
  }
  function closeMeasure() {
    if (!S.measuring) return;
    S.measuring = false; S.pts = []; measureLayer.clearLayers();
    body.classList.remove('medindo');
    $('[data-ferramenta="medir"]').setAttribute('aria-pressed', 'false');
    $('#medida').hidden = true;
  }
  function addPoint(ll) { S.pts.push(ll); drawMeasure(); }
  function drawMeasure() {
    measureLayer.clearLayers();
    var pts = S.pts, dist = 0;
    for (var i = 1; i < pts.length; i++) dist += map.distance(pts[i - 1], pts[i]);
    if (pts.length >= 3) L.polygon(pts, { pane: 'medida', color: '#FFD97A', weight: 2, dashArray: '6 6', fillColor: '#FFD97A', fillOpacity: 0.12, interactive: false }).addTo(measureLayer);
    if (pts.length >= 2) L.polyline(pts, { pane: 'medida', color: '#FFD97A', weight: 3, interactive: false }).addTo(measureLayer);
    pts.forEach(function (p) { L.circleMarker(p, { pane: 'medida', radius: 5, color: '#1E2320', weight: 2, fillColor: '#FFD97A', fillOpacity: 1, interactive: false }).addTo(measureLayer); });
    var area = geodesicArea(pts);
    $('#medidaValor').innerHTML = pts.length < 2
      ? '<span class="medida-dica">Toque no mapa para marcar os pontos.</span>'
      : '<span><b>' + esc(distanceText(dist)) + '</b> de distância</span>' + (pts.length >= 3 ? '<span><b>' + esc(ha(area / 10000)) + '</b> de área</span>' : '');
    $('[data-acao="desfazer-ponto"]').disabled = !pts.length;
  }
  async function share(url, title) {
    if (navigator.share && root.matchMedia('(pointer: coarse)').matches) {
      try { await navigator.share({ title: title, url: url }); return; } catch (e) { if (e && e.name === 'AbortError') return; }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast('Link copiado.');
    } catch (e) {
      toast('Não foi possível copiar. O link está na barra de endereço.');
    }
  }
  function viewUrl(path) {
    var c = map.getCenter();
    return location.origin + path + '?v=' + viewParam(c.lat, c.lng, map.getZoom());
  }

  // ---- routes and address bar
  var urlTimer = null;
  function currentPath() { return S.sel ? '/novo/imovel/' + S.sel.code : '/novo'; }
  function setUrl(path) {
    if (S.page !== 'mapa') return;
    var c = map.getCenter(), u = path + '?v=' + viewParam(c.lat, c.lng, map.getZoom());
    if (location.pathname + location.search !== u) history.replaceState({ page: 'mapa' }, '', u);
    doc.title = S.sel ? S.sel.code + ' · Raio-X Territorial' : 'Raio-X Territorial';
  }
  map.on('moveend', function () {
    clearTimeout(urlTimer); urlTimer = setTimeout(function () { setUrl(currentPath()); }, 400);
    loadParcels(); placeCard();
  });
  map.on('movestart', function () { $('#situacao').dataset.msg = ''; });
  map.on('move', function () { if (!narrow()) root.requestAnimationFrame(placeCard); });
  map.on('zoomend', notice);

  var PAGES = {
    prospeccao: { h: 'Prospecção', p: ['Encontrar as terras que servem para você: por cultura, tamanho, pivô, embargo, situação do CAR e distância de uma cidade.', 'Ainda está em construção. Enquanto isso, o mapa mostra cada imóvel do CAR e o Raio-X dele.'] },
    precos: { h: 'Preços', p: ['Hoje, tudo o que está nesta tela é grátis: o mapa, o cartão do imóvel e o Raio-X em uma olhada.', 'Os planos pagos ainda não estão à venda. Quando estiverem, os preços aparecem aqui antes de qualquer cobrança.'] },
    entrar: { h: 'Entrar', p: ['As contas ainda não existem. Nada do que você faz aqui pede cadastro.', 'Quando existirem, a conta vai servir para salvar imóveis e acompanhar mudanças neles.'] },
    'nao-encontrado': { h: 'Página não encontrada', p: ['Este endereço não corresponde a um código do CAR válido.', 'O código do CAR tem este formato: MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F.'] }
  };
  function showPage(page, push) {
    S.page = PAGES[page] ? page : 'mapa';
    var isMap = S.page === 'mapa';
    $('#vistaMapa').hidden = !isMap;
    var pv = $('#vistaPagina');
    pv.hidden = isMap;
    doc.querySelectorAll('[data-nav]').forEach(function (a) {
      if (a.dataset.nav === S.page) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    body.dataset.pagina = S.page;
    if (!isMap) {
      var c = PAGES[S.page];
      pv.innerHTML = '<div class="pagina"><h1 tabindex="-1">' + esc(c.h) + '</h1>' + c.p.map(function (t) { return '<p>' + esc(t) + '</p>'; }).join('') +
        '<a class="botao principal" href="/novo" data-nav-link="mapa">Abrir o mapa</a></div>';
      doc.title = c.h + ' · Raio-X Territorial';
      if (push) history.pushState({ page: S.page }, '', '/novo/' + (S.page === 'nao-encontrado' ? '' : S.page));
      var h = $('h1', pv); if (h) h.focus({ preventScroll: true });
    } else {
      if (push) history.pushState({ page: 'mapa' }, '', viewUrl(currentPath()).replace(location.origin, ''));
      setTimeout(function () { map.invalidateSize(); notice(); placeCard(); }, 0);
    }
  }
  root.addEventListener('popstate', function () {
    var m = /^\/novo\/(prospeccao|precos|entrar)\/?$/.exec(location.pathname);
    showPage(m ? m[1] : 'mapa', false);
  });

  // ---- events (one delegated listener)
  doc.addEventListener('click', function (e) {
    var t = e.target.closest('[data-acao],[data-ferramenta],[data-nav],[data-nav-link],.pergunta-botao,#sugestoes li');
    if (!t) return;
    if (t.matches('#sugestoes li')) { goCity(sugg.items[+t.dataset.i]); return; }
    if (t.matches('.pergunta-botao')) { toggleRow(t); return; }
    var nav = t.dataset.nav || t.dataset.navLink;
    if (nav && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) { e.preventDefault(); showPage(nav, true); return; }
    var tool = t.dataset.ferramenta;
    if (tool === 'mais') map.zoomIn();
    else if (tool === 'menos') map.zoomOut();
    else if (tool === 'local') locate();
    else if (tool === 'medir') toggleMeasure();
    else if (tool === 'link') share(viewUrl(currentPath()), 'Raio-X Territorial');
    var act = t.dataset.acao;
    if (!act) return;
    if (act === 'fechar-cartao') closeCard();
    else if (act === 'copiar-codigo' && S.sel) {
      var code = S.sel.code;
      if (navigator.clipboard) navigator.clipboard.writeText(code).then(function () { toast('Código do CAR copiado.'); }, function () { toast('Não foi possível copiar o código.'); });
      else toast('Não foi possível copiar o código.');
    }
    else if (act === 'compartilhar-imovel' && S.sel) share(viewUrl('/novo/imovel/' + S.sel.code), S.sel.code);
    else if (act === 'abrir-leitura') openReading();
    else if (act === 'fechar-leitura') closeReading(false);
    else if (act === 'ler-de-novo' && S.sel) startReading(S.sel.code, true);
    else if (act === 'desfazer-ponto') { S.pts.pop(); drawMeasure(); }
    else if (act === 'limpar-medida') { S.pts = []; drawMeasure(); }
    else if (act === 'fechar-medida') closeMeasure();
    else if (act === 'recarregar') { Array.from(G.cells.values()).forEach(function (c) { if (c.state === 'fail' || c.partial) { c.state = 'fail'; c.tries = 0; } }); G.box = null; loadParcels(); }
    else if (act === 'abrir-link') openCar(body.dataset.car, true);
  });
  doc.addEventListener('pointerover', function (e) { if (e.target.closest && e.target.closest('[data-acao="abrir-leitura"]')) wake(); });
  doc.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (S.measuring) closeMeasure();
    else if (!$('#leitura').hidden) closeReading(false);
    else if (S.sel) closeCard();
  });
  $('#busca').addEventListener('submit', onSearch);
  $('#q').addEventListener('input', function () { if (S.pin) { S.pin = false; } onInput(); });
  ['pointerdown', 'wheel', 'keydown'].forEach(function (t) { $('#mapa').addEventListener(t, function () { if (S.pin) { S.pin = false; $('#situacao').dataset.msg = ''; } }, { passive: true }); });
  $('#q').addEventListener('keydown', onKey);
  doc.addEventListener('click', function (e) { if (!e.target.closest('#busca')) hideSuggestions(); });
  root.addEventListener('resize', function () { placeCard(); });

  // ---- boot
  var params = new URLSearchParams(location.search), v = parseView(params.get('v'));
  S.hasView = !!v;
  if (v) map.setView([v.lat, v.lon], v.z); else map.fitBounds(BRASIL, { animate: false });
  var linkCar = body.dataset.route === 'imovel' && CAR_RE.test(body.dataset.car || '');
  // A property link without a view waits for the property before asking for satellite tiles (no Brazil-wide tiles thrown away).
  if (!linkCar || v) baseLayers();
  showPage(S.page, false);
  if (linkCar) openCar(body.dataset.car, true);
  loadParcels(); notice();
})(typeof window !== 'undefined' ? window : globalThis);
