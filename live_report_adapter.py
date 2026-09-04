from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

from report_engine import build_premium_property_report

REPORT_DIR = Path('/tmp/raiox_reports')
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _s(v: Any, default='-') -> str:
    return default if v is None or v == '' else str(v)


def _pct(part: float | None, total: float | None) -> float:
    try:
        if not total:
            return 0.0
        return round((float(part or 0) / float(total)) * 100.0, 2)
    except Exception:
        return 0.0


def _source_row(name: str, ok: bool | None, description: str):
    if ok is True:
        return {'name': name, 'description': description, 'status': 'CONSULTADA', 'level': 'ok'}
    if ok is False:
        return {'name': name, 'description': description, 'status': 'INDISPONÍVEL', 'level': 'attention'}
    return {'name': name, 'description': description, 'status': 'NÃO CONSULTADA', 'level': 'neutral'}


def _extract_prodes_occurrences(result: dict[str, Any]) -> list[dict[str, Any]]:
    ex = ((result.get('prodes') or {}).get('exact') or {}).get('occurrences') or []
    rows = []
    for item in ex:
        p = item.get('properties') or {}
        rows.append({
            'area_ha': round(float(item.get('area_intersection_ha') or 0), 6),
            'year': p.get('year'),
            'class_name': p.get('class_name'),
            'image_date': p.get('image_date'),
            'satellite': p.get('satellite'),
            'sensor': p.get('sensor'),
        })
    rows.sort(key=lambda x: (x.get('year') or 0, x.get('area_ha') or 0))
    return rows


def _iter_polygons(geom):
    if geom is None or geom.is_empty:
        return
    gt = geom.geom_type
    if gt == 'Polygon':
        yield geom
    elif gt == 'MultiPolygon':
        for g in geom.geoms:
            yield g
    elif gt == 'GeometryCollection':
        for g in geom.geoms:
            yield from _iter_polygons(g)


def build_technical_map(result: dict[str, Any], path: str | Path, include_prodes: bool = True) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    car_geom = shape((result.get('car') or {}).get('geometry'))
    minx, miny, maxx, maxy = car_geom.bounds
    dx = max(maxx-minx, 1e-6); dy = max(maxy-miny, 1e-6)
    pad_x = dx * .13; pad_y = dy * .13
    minx -= pad_x; maxx += pad_x; miny -= pad_y; maxy += pad_y

    W, H = 1600, 920
    img = Image.new('RGB', (W, H), '#F4F6F5')
    draw = ImageDraw.Draw(img, 'RGBA')
    left, top, right, bottom = 85, 65, W-85, H-125
    draw.rectangle((left, top, right, bottom), fill='#EEF1EF', outline='#CAD3CE', width=2)

    # coordinate grid
    for i in range(1, 5):
        x = left + (right-left)*i/5
        y = top + (bottom-top)*i/5
        draw.line((x, top, x, bottom), fill=(171,181,176,110), width=1)
        draw.line((left, y, right, y), fill=(171,181,176,110), width=1)

    def xy(lon, lat):
        x = left + (float(lon)-minx)/(maxx-minx)*(right-left)
        y = bottom - (float(lat)-miny)/(maxy-miny)*(bottom-top)
        return (x, y)

    # PRODES intersections, not treated as proof of illegality.
    if include_prodes:
        for h in (result.get('prodes') or {}).get('hits') or []:
            for feat in h.get('features') or []:
                try:
                    inter = car_geom.intersection(shape(feat.get('geometry')))
                    for poly in _iter_polygons(inter):
                        pts = [xy(x, y) for x, y in poly.exterior.coords]
                        if len(pts) >= 3:
                            draw.polygon(pts, fill=(197,58,58,95), outline=(197,58,58,210))
                except Exception:
                    pass

    # property boundary always on top
    for poly in _iter_polygons(car_geom):
        pts = [xy(x, y) for x, y in poly.exterior.coords]
        if len(pts) >= 3:
            draw.line(pts, fill=(14,96,59,255), width=6, joint='curve')

    try:
        font_b = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 30)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 22)
        small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18)
    except Exception:
        font_b = font = small = None

    p = (result.get('car') or {}).get('properties') or {}
    title = f"{_s(p.get('municipio'),'Imóvel rural')}/{_s(p.get('uf'))} • {_s(p.get('cod_imovel'))}"
    draw.text((left, 18), title, fill='#172028', font=font_b)
    draw.rectangle((left, H-92, left+34, H-58), fill='#0E603B')
    draw.text((left+46, H-91), 'Limite do CAR', fill='#344054', font=font)
    if include_prodes:
        draw.rectangle((left+315, H-92, left+349, H-58), fill=(197,58,58,150))
        draw.text((left+361, H-91), 'Interseção PRODES', fill='#344054', font=font)
    draw.text((right-390, H-88), 'Mapa técnico gerado pelo Raio-X Territorial', fill='#667085', font=small)

    img.save(path, format='PNG', optimize=True)
    return str(path)


def build_live_payload(result: dict[str, Any], report_id: str, generated_at: str, map_path: str) -> dict[str, Any]:
    car = result.get('car') or {}
    props = car.get('properties') or {}
    area_ha = float(props.get('area') or 0)
    sigef = result.get('sigef') or {}
    emb = result.get('embargos_ibama') or {}
    anm = result.get('anm') or {}
    prodes = result.get('prodes') or {}
    pex = prodes.get('exact') or {}
    eex = emb.get('exact') or {}
    aex = anm.get('exact') or {}
    prodes_rows = _extract_prodes_occurrences(result)
    prodes_area = float(pex.get('area_unique_ha') or 0)
    prodes_count = int(pex.get('occurrence_count') or 0)
    emb_count = int(eex.get('occurrence_count') or 0)
    anm_count = int(aex.get('occurrence_count') or 0)

    car_status = _s(props.get('status_imovel'))
    condition = _s(props.get('condicao'))
    sigef_count = int(sigef.get('feature_count') or 0)

    # PRODES is evidence of mapped deforestation, not automatic evidence of an environmental offense.
    if prodes_count:
        env_risk = 'ATENÇÃO'
        env_level = 'attention'
        env_text = f'{prodes_count} ocorrências PRODES intersectam o imóvel; a legalidade depende de data, autorização e enquadramento aplicável.'
    else:
        env_risk = 'BAIXO'
        env_level = 'ok'
        env_text = 'Nenhuma interseção PRODES foi localizada nas camadas consultadas.'

    if emb_count:
        enforcement_risk = 'ALTO'; enforcement_level = 'critical'; enforcement_text = f'{emb_count} embargo(s) IBAMA intersectam o imóvel.'
    else:
        enforcement_risk = 'BAIXO'; enforcement_level = 'ok'; enforcement_text = 'Nenhum embargo IBAMA intersectante foi localizado na consulta atual.'

    if anm_count:
        mining_risk = 'ATENÇÃO'; mining_level = 'attention'; mining_text = f'{anm_count} processo(s) ANM intersectam o imóvel.'
    else:
        mining_risk = 'BAIXO'; mining_level = 'ok'; mining_text = 'Nenhum processo ANM intersectante foi localizado na consulta atual.'

    overall = 'MODERADO' if prodes_count else ('ALTO' if emb_count else 'BAIXO')
    overall_level = 'attention' if overall == 'MODERADO' else ('critical' if overall == 'ALTO' else 'ok')

    exact_rows = []
    for r in prodes_rows[:8]:
        exact_rows.extend([
            ('Ano', r.get('year')),
            ('Área intersectada', f"{r.get('area_ha')} ha"),
            ('Imagem', f"{_s(r.get('satellite'))}/{_s(r.get('sensor'))} • {_s(r.get('image_date'))}"),
        ])

    payload = {
        'report_id': report_id,
        'generated_at': generated_at,
        'source_version': 'Consulta online às fontes oficiais e cálculo espacial exato do motor Raio-X Territorial.',
        'property': {
            'name': f"Imóvel rural • {_s(props.get('municipio'))}/{_s(props.get('uf'))}",
            'car_code': props.get('cod_imovel'),
            'municipality': props.get('municipio'),
            'uf': props.get('uf'),
            'area_ha': round(area_ha, 3),
        },
        'map_image_path': map_path,
        'car_map_image_path': map_path,
        'problem_map_image_path': map_path,
        'environment_map_image_path': map_path,
        'car': {
            'status': car_status,
            'type': props.get('tipo_imovel'),
            'fiscal_modules': props.get('m_fiscal'),
            'analysis_status': condition,
            'summary': f'CAR {_s(props.get("cod_imovel"))} • situação {_s(car_status)} • {_s(condition)}.',
            'fields': [
                ('Código CAR', props.get('cod_imovel')),
                ('Município', props.get('municipio')),
                ('UF', props.get('uf')),
                ('Área declarada', f'{round(area_ha,3)} ha'),
                ('Módulos fiscais', props.get('m_fiscal')),
                ('Status do imóvel', car_status),
                ('Tipo do imóvel', props.get('tipo_imovel')),
                ('Condição', condition),
                ('Fonte', 'SICAR / WFS público'),
                ('Consulta', generated_at),
            ],
            'areas': [],
        },
        'land': {
            'summary': f'SIGEF público: {sigef_count} parcela(s) candidata(s) no envelope do imóvel. SNCI e matrícula ainda não consultados neste ciclo.',
            'risk': 'ATENÇÃO',
            'certifications': [
                ['SIGEF', 'CONSULTADO', sigef_count, 'Espelho público SIGEF/INCRA disponibilizado no PAMGIA/IBAMA'],
                ['SNCI', 'NÃO CONSULTADO', '-', 'Conector específico ainda não ativado neste ciclo'],
            ],
            'matrix': [
                ['CAR', car_status, f'{round(area_ha,3)} ha', 'Cadastro ambiental consultado'],
                ['SIGEF', f'{sigef_count} parcela(s)', '-', 'Não equivale a matrícula imobiliária'],
                ['Matrícula', 'NÃO CONSULTADA', '-', 'Exige fonte registral adequada'],
                ['Detentor/titular', 'NÃO CONSULTADO', '-', 'Não inferido a partir do CAR'],
            ],
            'evidence': {'score': 'LIMITADA', 'text': 'CAR e consulta SIGEF não comprovam, por si sós, titularidade registral atual. Matrícula e cadeia dominial não foram consultadas neste ciclo.'},
        },
        'environment': {
            'prodes': {
                'count': prodes_count,
                'area_ha': round(prodes_area, 6),
                'status': env_risk,
                'summary': env_text,
                'rows': [
                    ('Ocorrências exatas', prodes_count),
                    ('Área única intersectada', f'{round(prodes_area,6)} ha'),
                    ('Percentual do CAR', f'{_pct(prodes_area, area_ha)}%'),
                    ('Anos identificados', ', '.join(str(x.get('year')) for x in prodes_rows if x.get('year')) or '-'),
                    ('Fonte', 'INPE / TerraBrasilis / PRODES'),
                ] + exact_rows[:9],
                'meaning': 'PRODES mapeia desmatamento. A interseção não prova, isoladamente, infração ambiental; é necessário considerar data, autorizações, área consolidada e demais regras aplicáveis.',
            },
            'unique_problem_area_ha': 0,
            'unique_problem_area_pct': 0,
            'layer_rows': [
                ['PRODES', f'{prodes_count} ocorrência(s) • {round(prodes_area,6)} ha', 'INPE/TerraBrasilis'],
                ['Embargo IBAMA', f'{emb_count} ocorrência(s)', 'IBAMA/PAMGIA'],
                ['Terra Indígena', 'NÃO CONSULTADO', 'Conector pendente'],
                ['Unidade de Conservação', 'NÃO CONSULTADO', 'Conector pendente'],
                ['Quilombola / assentamento', 'NÃO CONSULTADO', 'Conector pendente'],
            ],
        },
        'enforcement': {
            'embargo_count': emb_count,
            'embargo_summary': enforcement_text,
            'embargo_status': enforcement_risk,
            'auto_count': 'NÃO CONSULTADO',
            'fine_total_text': 'autos/multas pendentes',
            'autos': [],
        },
        'mining': {
            'process_count': anm_count,
            'overlap_area_ha': round(float(aex.get('area_unique_ha') or 0), 6),
            'rare_earth_count': 0 if anm_count == 0 else 'A CLASSIFICAR',
            'max_maturity': '-',
            'summary': mining_text,
            'risk': mining_risk,
            'processes': [],
        },
        'productive': {
            'aptitude_rows': [['NÃO CONSULTADO', '-']],
            'soil_rows': [['Solo / atributos', 'NÃO CONSULTADO']],
            'terrain_kpis': [
                {'label':'Altitude','value':'NÃO CONSULTADO','note':'DEM pendente'},
                {'label':'Declividade','value':'NÃO CONSULTADO','note':'DEM pendente'},
                {'label':'Mecanização','value':'NÃO CONSULTADO','note':'regra pendente'},
                {'label':'Aptidão','value':'NÃO CONSULTADO','note':'camada pendente'},
            ],
        },
        'water': {
            'grant_count':'NÃO CONSULTADO','pivot_count':'NÃO CONSULTADO','rain_30d':'NÃO CONSULTADO','rain_period':'-',
            'grants':[], 'rain_rows': [('Outorgas','NÃO CONSULTADO'),('Precipitação','NÃO CONSULTADO')],
            'meaning':'O módulo hídrico ainda não foi executado nesta emissão real; o relatório não assume ausência de outorgas ou restrições.'
        },
        'infrastructure': {'airports':[], 'warehouses':[], 'iphan':[]},
        'monitoring': {
            'alerts': [
                ['Foco de calor','detecção dentro/próximo do imóvel','Push / WhatsApp / SMS crítico'],
                ['Embargo','nova interseção ou alteração','Push / WhatsApp'],
                ['PRODES','nova ocorrência cartográfica','Push / WhatsApp'],
                ['ANM','novo processo ou mudança','Push / WhatsApp'],
            ],
            'cadences': [['Semanal','7 dias'],['Quinzenal','15 dias'],['Mensal','1 mês'],['Trimestral','3 meses'],['Semestral','6 meses'],['Anual','12 meses']],
            'ndvi':'NÃO CONSULTADO','ndvi_date_source':'satélite pendente','fire_inside_365d':'NÃO CONSULTADO','fire_5km_365d':'NÃO CONSULTADO','last_fire':'-'
        },
        'quick_read': f'CAR real localizado em {_s(props.get("municipio"))}/{_s(props.get("uf"))}, com {round(area_ha,3)} ha. A consulta encontrou {prodes_count} ocorrência(s) PRODES em interseção exata, {emb_count} embargo(s) IBAMA e {anm_count} processo(s) ANM. Fontes ainda não ativadas aparecem explicitamente como NÃO CONSULTADO.',
        'attention_points': [
            f'PRODES: {prodes_count} ocorrência(s) históricas, totalizando {round(prodes_area,6)} ha de interseção única; isso não equivale automaticamente a infração.',
            'Matrícula, titularidade registral e SNCI ainda não foram consultados neste ciclo.',
            'Outorgas, solo, aptidão, clima, NDVI e infraestrutura ainda precisam entrar no pipeline real.',
        ],
        'executive_summary_rows': [
            ['CAR', f'{round(area_ha,3)} ha • {_s(condition)}', car_status, 'ok'],
            ['Fundiário', f'SIGEF: {sigef_count} parcela(s); matrícula não consultada', 'ATENÇÃO', 'attention'],
            ['Ambiental / PRODES', f'{prodes_count} ocorrência(s) • {round(prodes_area,6)} ha', env_risk, env_level],
            ['Embargos IBAMA', enforcement_text, enforcement_risk, enforcement_level],
            ['Mineração ANM', mining_text, mining_risk, mining_level],
        ],
        'priorities': [
            'Confirmar matrícula e titularidade registral antes de decisão de compra, financiamento ou garantia.',
            'Analisar cronologia e enquadramento das ocorrências PRODES; o mapa de desmatamento não prova ilegalidade por si só.',
            'Completar as camadas ainda marcadas como NÃO CONSULTADO antes de emitir conclusão abrangente.',
        ],
        'compliance': [
            {'label':'CAR','text':f'Cadastro localizado • {round(area_ha,3)} ha','badge':'CONSULTADO','level':'ok'},
            {'label':'SIGEF','text':f'{sigef_count} parcela(s) candidata(s) no envelope do imóvel','badge':'CONSULTADO','level':'ok'},
            {'label':'PRODES','text':f'{prodes_count} ocorrência(s) exatas • {round(prodes_area,6)} ha','badge':env_risk,'level':env_level},
            {'label':'Embargos IBAMA','text':enforcement_text,'badge':enforcement_risk,'level':enforcement_level},
            {'label':'ANM','text':mining_text,'badge':mining_risk,'level':mining_level},
            {'label':'Matrícula','text':'Fonte registral não consultada nesta emissão','badge':'NÃO CONSULTADO','level':'neutral'},
            {'label':'Outorgas','text':'Base hídrica ainda não executada','badge':'NÃO CONSULTADO','level':'neutral'},
            {'label':'Solo / aptidão','text':'Camadas produtivas ainda não executadas','badge':'NÃO CONSULTADO','level':'neutral'},
        ],
        'conclusion': {
            'overall_risk': overall,
            'overall_reason': 'Classificação provisória baseada apenas nas fontes efetivamente consultadas. A presença de PRODES exige análise temporal, mas não é tratada como prova automática de irregularidade.',
            'categories': [
                {'label':'Fundiário','text':'CAR e SIGEF consultados; matrícula e titularidade registral pendentes.','risk':'ATENÇÃO','level':'attention'},
                {'label':'Ambiental','text':env_text,'risk':env_risk,'level':env_level},
                {'label':'Fiscalização','text':enforcement_text,'risk':enforcement_risk,'level':enforcement_level},
                {'label':'Mineral','text':mining_text,'risk':mining_risk,'level':mining_level},
                {'label':'Hídrico','text':'Outorgas e restrições hídricas ainda não consultadas.','risk':'NÃO CLASSIFICADO','level':'neutral'},
                {'label':'Produtivo','text':'Solo, aptidão e relevo ainda não consultados.','risk':'NÃO CLASSIFICADO','level':'neutral'},
            ],
            'main_attention':'O maior ponto de atenção desta emissão é a necessidade de interpretar corretamente as ocorrências PRODES e completar a diligência registral; nenhum desses pontos deve ser inferido além do que as fontes consultadas suportam.',
            'verdict':'Há dados reais suficientes para um Raio-X parcial, mas ainda não para uma conclusão integral sobre aquisição ou financiamento. O sistema marca explicitamente o que foi consultado e o que permanece pendente.',
            'positives':['CAR localizado com geometria real.','Nenhum embargo IBAMA intersectante localizado.','Nenhum processo ANM intersectante localizado.'],
            'risks':[f'{prodes_count} ocorrência(s) PRODES exigem análise temporal e documental.','Matrícula e titularidade registral ainda não confirmadas.','Algumas camadas do relatório completo ainda não foram consultadas nesta emissão.'],
            'opportunities':['Ativar monitoramento contínuo para mudanças futuras.','Completar due diligence com fontes hídricas, produtivas e registrais.'],
            'diligence':['Obter matrícula atualizada e verificar titularidade/ônus.','Conferir cada ocorrência PRODES por data, autorização e enquadramento aplicável.','Executar outorgas, UC/TI/quilombola/assentamentos, solo, aptidão, clima e infraestrutura.','Repetir consultas críticas na data da negociação.'],
            'limit':'O Raio-X Territorial consolida fontes públicas e cálculos geoespaciais. Não substitui certidão registral, vistoria, laudo técnico, autorização ambiental ou parecer jurídico quando necessários.'
        },
        'sources': [
            _source_row('SICAR', car.get('ok'), 'Cadastro Ambiental Rural consultado via WFS público.'),
            _source_row('SIGEF / INCRA (espelho PAMGIA)', sigef.get('ok'), 'Consulta pública de parcelas SIGEF disponibilizada em serviço do IBAMA/PAMGIA.'),
            _source_row('IBAMA / PAMGIA', emb.get('ok'), 'Embargos SISCOM com cruzamento espacial exato.'),
            _source_row('INPE / TerraBrasilis / PRODES', prodes.get('ok'), 'Camadas PRODES consultadas por WFS e intersectadas geometricamente com o CAR.'),
            _source_row('ANM / SIGMINE', anm.get('ok'), 'Processos minerários consultados e intersectados geometricamente.'),
            _source_row('SNCI', None, 'Conector ainda não ativado nesta emissão.'),
            _source_row('Registro de imóveis', None, 'Matrícula e titularidade não consultadas nesta emissão.'),
        ],
        'interpretation_rules': [
            'NÃO CONSULTADO nunca é tratado como ausência de ocorrência.',
            'PRODES indica desmatamento mapeado e não prova, isoladamente, infração ambiental.',
            'CAR não comprova titularidade registral do imóvel.',
            'Processo ANM não comprova jazida, reserva ou viabilidade econômica mineral.',
            'Interseções espaciais exatas são recalculadas localmente sobre a geometria do CAR.',
        ],
    }
    return payload


def generate_live_report(result: dict[str, Any], car_code: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    safe_code = ''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_')
    report_id = f'RX-{stamp}-{safe_code[-8:]}'
    out_dir = REPORT_DIR / report_id
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = build_technical_map(result, out_dir / 'map_environment.png', include_prodes=True)
    payload = build_live_payload(result, report_id, now.isoformat(), map_path)
    payload_path = out_dir / 'payload.json'
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    pdf_path = out_dir / 'raio_x_territorial.pdf'
    digest = build_premium_property_report(pdf_path, payload)
    return {
        'report_id': report_id,
        'pdf_path': str(pdf_path),
        'payload_path': str(payload_path),
        'map_path': str(map_path),
        'sha256': digest,
        'bytes': pdf_path.stat().st_size,
        'payload_sha256': sha256(payload_path.read_bytes()).hexdigest(),
    }
