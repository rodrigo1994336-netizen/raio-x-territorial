from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML


def once(old: str, new: str, error: str) -> None:
    global html
    if html.count(old) != 1:
        raise RuntimeError(error)
    html = html.replace(old, new, 1)


# V45 renders its fast panel asynchronously and may render again after V46's
# compatibility timers. Therefore dates/status/type must be normalized in the
# panel renderer itself, not only by a later DOM pass.
once(
    " const text=v=>(v===null||v===undefined||v==='')?'Não informado':String(v);\n const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();",
    " const text=v=>(v===null||v===undefined||v==='')?'Não informado':String(v);\n const rx46PanelDate=v=>{const m=String(v||'').match(/(\\d{4})-(\\d{2})-(\\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:text(v)};\n const rx46PanelStatus=v=>{const raw=String(v||'').trim(),dict={AT:'Ativo',PE:'Pendente',CA:'Cancelado',SU:'Suspenso',IN:'Inativo'},label=dict[raw.toUpperCase()]||(raw.length>3?raw:'Situação informada'),low=label.toLowerCase();return {raw,label,cls:/ativ/.test(low)?'active':/pendent/.test(low)?'pending':/cancel|suspens|inativ/.test(low)?'bad':''}};\n const rx46PanelType=v=>{const raw=String(v||'').trim(),dict={IRU:'Imóvel Rural',AST:'Assentamento',PCT:'Povos e Comunidades Tradicionais'};return dict[raw.toUpperCase()]||(raw.length>3?raw:'Tipo informado')};\n const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();",
    "v46_panel_normalization_helpers_missing",
)
once(
    "   const dates=[p.created_at?`Cadastro: ${esc(text(p.created_at))}`:'',p.updated_at?`Atualização: ${esc(text(p.updated_at))}`:''].filter(Boolean).join(' · ')||'Datas não informadas nesta fonte';",
    "   const dates=[p.created_at?`Cadastro: ${esc(rx46PanelDate(p.created_at))}`:'',p.updated_at?`Atualização: ${esc(rx46PanelDate(p.updated_at))}`:''].filter(Boolean).join(' · ')||'Datas não informadas nesta fonte';",
    "v46_panel_date_render_missing",
)
once(
    '<div class="rx45-kpi"><small>Situação CAR</small><b>${esc(text(p.car_status))}</b></div>',
    '<div class="rx45-kpi"><small>Situação CAR</small><b data-rx46="1"><span class="rx46-status-seal ${rx46PanelStatus(p.car_status).cls}">${esc(rx46PanelStatus(p.car_status).label)}</span>${rx46PanelStatus(p.car_status).raw&&rx46PanelStatus(p.car_status).raw!==rx46PanelStatus(p.car_status).label?`<span class="rx46-code-mini">${esc(rx46PanelStatus(p.car_status).raw)}</span>`:\'\'}</b></div>',
    "v46_panel_status_render_missing",
)
once(
    '<div class="rx45-row"><b>Tipo do imóvel</b><span>${esc(text(p.property_type))}</span></div>',
    '<div class="rx45-row"><b>Tipo do imóvel</b><span data-rx46="1">${esc(rx46PanelType(p.property_type))}</span></div>',
    "v46_panel_type_render_missing",
)

# The CTA must materialize the truthful V45 shell synchronously from the data
# already selected on the map. The authoritative /map-panel request remains the
# asynchronous enrichment path; a slow upstream CAR must not leave the CTA blank.
once(
    " function render(p){const h=q('#rx43SnapshotHost');if(!h||!p)return;h.innerHTML=panelHtml(p);const card=h.querySelector('.rx45-panel-card');if(card)card.__rxSourceAudit=(p.source_audit&&Array.isArray(p.source_audit.registry))?JSON.parse(JSON.stringify(p.source_audit)):null;bind()}\n async function load(car){if(!car||busy)return;",
    " function render(p){const h=q('#rx43SnapshotHost');if(!h||!p)return;h.innerHTML=panelHtml(p);const card=h.querySelector('.rx45-panel-card');if(card)card.__rxSourceAudit=(p.source_audit&&Array.isArray(p.source_audit.registry))?JSON.parse(JSON.stringify(p.source_audit)):null;bind()}\n window.rxV46RenderV45Immediate=function(p){if(!p)return;const ha=Number(p.area_ha),m2=Number(p.area_m2),defaults=[{id:'embargo',label:'Embargos',state:'not_consulted'},{id:'prodes',label:'PRODES',state:'not_consulted'},{id:'indigenous_land',label:'Terra Indígena',state:'not_consulted'},{id:'legal_reserve',label:'Reserva Legal',state:'not_consulted'},{id:'conservation_unit',label:'Un. Conservação',state:'not_consulted'},{id:'registry',label:'Matrícula',state:'not_consulted'},{id:'public_forest',label:'Floresta Pública',state:'not_consulted'},{id:'snci',label:'SNCI',state:'not_consulted'}],safe={...p,car_status:p.car_status||p.status,property_type:p.property_type||p.type,area_m2:Number.isFinite(m2)?m2:(Number.isFinite(ha)?ha*10000:null),risk:p.risk||{state:'not_classified',label:'RISCO NÃO CLASSIFICADO',detail:'As fontes de restrição ainda não foram consultadas neste painel rápido. Fonte não consultada não significa ausência de ocorrência.'},source_audit:p.source_audit||null,compliance_sources:(p.compliance_sources&&p.compliance_sources.length)?p.compliance_sources:defaults};render(safe)};\n async function load(car){if(!car||busy)return;",
    "v46_v45_immediate_renderer_missing",
)
once(
    "function openFull(){if(!selected||typeof legacyOpen!=='function')return;closeAnchor();const payload={...selected,status:selected.status||selected.car_status,type:selected.type||selected.property_type,geometry:selected.geometry};window.current={...payload};legacyOpen(payload,payload.geometry);postOpen()}",
    "function openFull(){if(!selected||typeof legacyOpen!=='function')return;closeAnchor();const payload={...selected,status:selected.status||selected.car_status,type:selected.type||selected.property_type,geometry:selected.geometry};window.current={...payload};legacyOpen(payload,payload.geometry);window.rxV46RenderV45Immediate?.(payload);postOpen()}",
    "v46_cta_immediate_v45_missing",
)

# The card is enriched asynchronously after selection. If the user closes the
# anchor while that request is still in flight, the late response must update the
# selected geometry/data without resurrecting the dismissed popup.
once(
    "let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0;",
    "let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0,anchorOpen=false;",
    "v46_anchor_state_slot_missing",
)
once(
    "function renderAnchor(latlng){const m=mapRef();if(!m||!selected||!latlng)return;",
    "function renderAnchor(latlng){const m=mapRef();if(!anchorOpen||!m||!selected||!latlng)return;",
    "v46_anchor_render_guard_missing",
)
once(
    "function closeAnchor(){const m=mapRef();",
    "function closeAnchor(){anchorOpen=false;const m=mapRef();",
    "v46_anchor_close_guard_missing",
)
once(
    "window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;selected=",
    "window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;anchorOpen=true;selected=",
    "v46_anchor_open_guard_missing",
)

# Leaflet can propagate a layer click to the map even after stopping the DOM
# event. A parcel selection must never be interpreted as an empty-map click.
once(
    "l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')window.rxV46SelectProperty(p,live.geometry,e.latlng);else if(typeof showProperty==='function')showProperty(p,live.geometry)})",
    "l.on('click',e=>{if(e.originalEvent){try{e.originalEvent.__rx46ParcelClick=true;L.DomEvent.stop(e.originalEvent)}catch(x){L.DomEvent.stopPropagation(e.originalEvent)}}const live=l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')window.rxV46SelectProperty(p,live.geometry,e.latlng);else if(typeof showProperty==='function')showProperty(p,live.geometry)})",
    "v46_parcel_click_propagation_guard_missing",
)
once(
    "map.on('click',()=>window.rxV46CloseAnchor?.());",
    "map.on('click',e=>{if(e?.sourceTarget&&e.sourceTarget!==map)return;if(e?.originalEvent?.__rx46ParcelClick)return;window.rxV46CloseAnchor?.()});",
    "v46_empty_map_click_guard_missing",
)

# Some bare-map clicks terminate at the map container without producing the
# expected Leaflet synthetic click. Guarantee the UX contract at the DOM layer too:
# only true map background closes the card; popups, controls and interactive map
# features are ignored.
once(
    "document.addEventListener('click',action,true);",
    "document.addEventListener('click',action,true);document.addEventListener('click',e=>{const t=e.target;if(!t?.closest?.('#map'))return;if(t.closest('.leaflet-popup,.leaflet-control,.leaflet-interactive'))return;window.rxV46CloseAnchor?.()},true);",
    "v46_dom_empty_map_close_guard_missing",
)

html = html.replace(
    "</body>",
    "<!-- RX_MAP_V46_PANEL_NORMALIZATION -->\n<!-- RX_MAP_V46_CTA_IMMEDIATE_V45 -->\n<!-- RX_MAP_V46_ANCHOR_STATE -->\n<!-- RX_MAP_V46_CLICK_PROPAGATION_GUARD -->\n<!-- RX_MAP_V46_DOM_EMPTY_CLOSE_GUARD -->\n</body>",
)
portal_v8.PORTAL_HTML = html

print("RX_MAP_V46_PANEL_NORMALIZATION=dates_status_type_rendered_truthfully", flush=True)
print("RX_MAP_V46_CTA_IMMEDIATE_V45=local_truth_shell_then_async_enrichment", flush=True)
print("RX_MAP_V46_ANCHOR_STATE=late_enrichment_never_reopens_closed_card", flush=True)
print("RX_MAP_V46_CLICK_PROPAGATION=parcel_click_never_closes_anchor", flush=True)
print("RX_MAP_V46_DOM_EMPTY_CLOSE=bare_map_background_closes_anchor", flush=True)
