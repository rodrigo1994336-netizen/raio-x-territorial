from __future__ import annotations

from typing import Any


def _ok(result:dict,key:str)->bool:
    return isinstance(result.get(key),dict) and result.get(key,{}).get('ok') is True


def _status_text(v):
    return str(v or '').strip().upper()


def _dedupe_text(items):
    out=[];seen=set()
    for x in items or []:
        s=str(x or '').strip()
        if not s:continue
        key=' '.join(s.lower().split())
        if key in seen:continue
        seen.add(key);out.append(s)
    return out


def _contains_any(text,*terms):
    t=str(text or '').lower()
    return any(x.lower() in t for x in terms)


def comprehensive_truth_guard(payload:dict[str,Any],result:dict[str,Any],base_guard=None):
    if base_guard:
        payload=base_guard(payload,result)

    water=result.get('water_mg') or {};piv=result.get('pivots_ana') or {};cl=result.get('climate_nasa') or {}
    ide=result.get('ide_layers') or {};sat=payload.get('satellite_imagery') or {};mb=result.get('mapbiomas_coverage') or {}
    cons=result.get('territorial_constraints') or {};autos=result.get('autos_ibama') or {};fire=result.get('fire_live') or {}
    car=result.get('car') or {};sigef=result.get('sigef') or {};anm=result.get('anm') or {};prodes=result.get('prodes') or {};emb=result.get('embargos_ibama') or {}

    # Fundiário: keep the limitation, but stop presenting restricted connectors as forgotten work.
    land=payload.setdefault('land',{})
    sigef_count=int(sigef.get('feature_count') or 0)
    land['summary']=(f'SIGEF público consultado nesta emissão: {sigef_count} parcela(s) candidata(s) no envelope do imóvel. '
                     'SNCI/CCIR, matrícula, ônus e titularidade são integrações registrais/cadastrais separadas e permanecem preparadas para ativação por fonte legalmente habilitada; não são inferidas do CAR.')
    cert=[]
    for row in land.get('certifications') or []:
        r=list(row)
        if r and _contains_any(r[0],'SNCI','CCIR'):
            while len(r)<4:r.append('')
            r[1]='INTEGRAÇÃO PREPARADA — OFF';r[2]='—';r[3]='Ativação depende de fonte/credencial legalmente habilitada.'
        cert.append(r)
    land['certifications']=cert
    matrix=[]
    for row in land.get('matrix') or []:
        r=list(row)
        if r and _contains_any(r[0],'matrícula','detentor','titular'):
            while len(r)<4:r.append('')
            r[1]='INTEGRAÇÃO RESTRITA — OFF';r[2]='—';r[3]='Não inferido do CAR; conector preparado para provedor/fonte autorizada.'
        matrix.append(r)
    land['matrix']=matrix

    # Monitoring values must reflect the real Sentinel and fire engines.
    mon=payload.setdefault('monitoring',{})
    if sat.get('ok') and sat.get('ndvi_mean') is not None:
        mon['ndvi']=f"média {sat.get('ndvi_mean')} • mediana {sat.get('ndvi_median')}"
        mon['ndvi_date_source']=f"Sentinel-2 L2A {sat.get('date') or 'data não informada'} • {sat.get('resolution_m') or 10} m"
    if fire.get('ok'):
        mon['fire_inside_365d']=fire.get('inside_count',0)
        mon['fire_5km_365d']=fire.get('near_count',0)
        nearest=fire.get('nearest') or {}
        mon['last_fire']=nearest.get('date') or nearest.get('data_hora_gmt') or 'nenhum foco retornado no recorte consultado'

    # Productive placeholders: only keep unavailable states that are still genuinely unavailable.
    prod=payload.setdefault('productive',{})
    if prod.get('soil_rows') and any(not _contains_any(r[1] if len(r)>1 else '','NÃO CONSULTADO') for r in prod.get('soil_rows') or []):
        prod['soil_rows']=[r for r in prod.get('soil_rows') or [] if not (len(r)>1 and _status_text(r[1])=='NÃO CONSULTADO')]
    if prod.get('aptitude_rows') and any(not _contains_any(r[0] if r else '','NÃO CONSULTADO') for r in prod.get('aptitude_rows') or []):
        prod['aptitude_rows']=[r for r in prod.get('aptitude_rows') or [] if not (r and _status_text(r[0])=='NÃO CONSULTADO')]
    terrain=[]
    slope=ide.get('slope') or {};apt=ide.get('aptitude') or {}
    for x in prod.get('terrain_kpis') or []:
        y=dict(x);label=str(y.get('label') or '').lower()
        if label=='mecanização' and _status_text(y.get('value'))=='NÃO CONSULTADO':
            if slope.get('ok'):
                y={'label':'Mecanização','value':'TRIAGEM POR DECLIVIDADE','note':'Aptidão operacional depende da classe de declive, solo, umidade e manejo; não é laudo de mecanização.','status':'INFO','level':'info'}
            else:
                y={'label':'Mecanização','value':'NÃO CLASSIFICADA','note':'Sem base suficiente para inferência segura nesta emissão.','status':'PARCIAL','level':'attention'}
        elif label=='altitude' and _status_text(y.get('value'))=='NÃO CONSULTADO':
            y={'label':'Altitude','value':'NÃO MEDIDA NESTA EMISSÃO','note':'A ausência desta métrica não invalida as camadas de declividade/aptidão já consultadas.','status':'PARCIAL','level':'attention'}
        terrain.append(y)
    prod['terrain_kpis']=terrain

    # Remove obsolete generic warnings inserted by the original V1 payload.
    cleaned=[]
    for text in payload.get('attention_points') or []:
        if _contains_any(text,'outorgas, solo, aptidão, clima, ndvi e infraestrutura ainda precisam','solo, aptidão, clima, ndvi e infraestrutura ainda'):
            continue
        cleaned.append(text)
    payload['attention_points']=_dedupe_text(cleaned)

    con=payload.setdefault('conclusion',{})
    con['risks']=[x for x in con.get('risks') or [] if not _contains_any(x,'algumas camadas do relatório completo ainda não foram consultadas','solo, aptidão e relevo ainda não consultados','outorgas e restrições hídricas ainda não consultadas')]
    con['diligence']=[x for x in con.get('diligence') or [] if not _contains_any(x,'executar outorgas, uc/ti/quilombola/assentamentos, solo, aptidão, clima e infraestrutura','completar as camadas ainda marcadas como não consultado')]

    # Rewrite Hídrico category using actual current state, even when zero intersections were found.
    for row in con.get('categories') or []:
        if row.get('label')=='Hídrico':
            if water.get('ok'):
                inside=int(water.get('inside_count') or 0);near=int(water.get('near_count') or 0)
                ptxt=f"Pivôs: {piv.get('intersection_count',0)} intersectante(s)" if piv.get('ok') else 'pivôs: fonte indisponível'
                ctxt=f"clima: {cl.get('rain_sum_mm')} mm no período recente" if cl.get('ok') else 'clima: fonte indisponível'
                row['text']=f'Outorgas consultadas: {inside} dentro do imóvel e {near} no raio; {ptxt}; {ctxt}.'
                row['risk']='ATENÇÃO' if inside else 'TRIAGEM DISPONÍVEL';row['level']='attention' if inside else 'info'
            else:
                row['text']='Fonte de outorgas indisponível nesta emissão; não inferir ausência.';row['risk']='NÃO CLASSIFICADO';row['level']='attention'

    # Build an objective coverage statement from real engines, instead of keeping the old "Raio-X parcial" wording.
    checks={
        'CAR/SICAR':car.get('ok') is True,'SIGEF':sigef.get('ok') is True,'IBAMA embargos':emb.get('ok') is True,
        'IBAMA autos':autos.get('ok') is True,'PRODES':prodes.get('ok') is True,'ANM':anm.get('ok') is True,
        'restrições territoriais':cons.get('ok') is True,'outorgas':water.get('ok') is True,'pivôs':piv.get('ok') is True,
        'clima':cl.get('ok') is True,'solo':(ide.get('soil') or {}).get('ok') is True,
        'aptidão':apt.get('ok') is True,'declividade':slope.get('ok') is True,
        'imagem Sentinel-2':sat.get('ok') is True,'MapBiomas cobertura':mb.get('ok') is True,
    }
    ready=[k for k,v in checks.items() if v];missing=[k for k,v in checks.items() if not v]
    con['coverage']={'consulted_core_count':len(ready),'core_count':len(checks),'consulted_core':ready,'unavailable_core':missing}
    if len(ready)>=12:
        con['verdict']=(f'Raio-X público ampliado executado: {len(ready)}/{len(checks)} núcleos técnicos responderam nesta emissão. '
                        'As integrações registrais/restritas permanecem claramente separadas e não são substituídas por inferência.')
    else:
        con['verdict']=(f'Raio-X executado com {len(ready)}/{len(checks)} núcleos técnicos respondendo. '
                        f'Pontos cegos desta emissão: {", ".join(missing) if missing else "nenhum núcleo público principal"}.')

    # Priorities become due diligence, not a stale development checklist.
    priorities=[]
    priorities.append('Obter matrícula atualizada, ônus e titularidade por fonte registral habilitada antes de decisão patrimonial relevante.')
    if (prodes.get('exact') or {}).get('occurrence_count'):
        priorities.append('Conferir cronologia, autorização e enquadramento das ocorrências PRODES; detecção cartográfica não prova infração isoladamente.')
    if not water.get('ok'):priorities.append('Repetir a consulta de outorgas quando a fonte oficial voltar a responder.')
    if not sat.get('ok'):priorities.append('Repetir a cena Sentinel-2 para obter imagem/NDVI real da propriedade.')
    if not mb.get('ok'):priorities.append('Repetir o recorte MapBiomas de cobertura/pastagem quando o GeoTIFF público voltar a responder.')
    payload['priorities']=priorities

    # Update compliance badges that the base payload left as generic placeholders.
    for row in payload.get('compliance') or []:
        label=str(row.get('label') or '').lower()
        if label=='matrícula':
            row['text']='Integração registral preparada; ativação depende de fonte/provedor legalmente habilitado.';row['badge']='INTEGRAÇÃO RESTRITA — OFF';row['level']='neutral'
        elif label=='outorgas' and water.get('ok'):
            row['text']=f"{int(water.get('inside_count') or 0)} dentro do imóvel; {int(water.get('near_count') or 0)} no raio consultado.";row['badge']='CONSULTADO';row['level']='ok'

    # Source table truth: no source that actually responded may remain labelled as not consulted.
    for src in payload.get('sources') or []:
        st=_status_text(src.get('status'))
        if st in {'NÃO CONSULTADA','NÃO CONSULTADO','NAO CONSULTADA','NAO CONSULTADO'}:
            name=str(src.get('name') or '').lower()
            if ('clima' in name or 'nasa power' in name) and cl.get('ok'):src.update(status='CONSULTADA',level='ok')
            elif 'outorga' in name and water.get('ok'):src.update(status='CONSULTADA',level='ok')
            elif 'piv' in name and piv.get('ok'):src.update(status='CONSULTADA',level='ok')
            elif 'mapbiomas' in name and 'vigor' not in name and mb.get('ok'):src.update(status='CONSULTADA',level='ok')

    payload.setdefault('interpretation_rules',[]).append('A reconciliação final remove placeholders antigos quando o conector efetivamente respondeu; um dado só permanece indisponível/restrito quando essa é a situação real desta emissão.')
    return payload


print('RX_REPORT_TRUTH_GUARD=V16_RECONCILED',flush=True)
