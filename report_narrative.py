from __future__ import annotations

from typing import Any


def _s(v: Any, default='-') -> str:
    return default if v is None or v == '' else str(v)


def _n(v, default=0):
    try:return int(v)
    except Exception:return default


def _f(v, default=0.0):
    try:return float(v)
    except Exception:return default


def _status(srcs, term):
    term=term.lower()
    for x in srcs or []:
        if term in str(x.get('name') or '').lower():
            return str(x.get('status') or '')
    return ''


def build_narrative(payload: dict[str,Any]) -> dict[str,Any]:
    prop=payload.get('property') or {}; env=payload.get('environment') or {}; enf=payload.get('enforcement') or {}; mining=payload.get('mining') or {}; water=payload.get('water') or {}; prod=payload.get('productive') or {}; con=payload.get('conclusion') or {}; land=payload.get('land') or {}; sources=payload.get('sources') or []
    pd=env.get('prodes') or {}
    prodes_n=_n(pd.get('count')); prodes_area=_f(pd.get('area_ha'))
    emb=_n(enf.get('embargo_count'))
    anm=_n(mining.get('process_count'))
    rare=str(mining.get('rare_earth_signal') or '').upper()=='SIM'
    area=_s(prop.get('area_ha'))
    city=f"{_s(prop.get('municipality'))}/{_s(prop.get('uf'))}"

    if emb:
        one=f"O imóvel de {area} ha em {city} merece atenção imediata porque encontramos embargo ambiental intersectando a área analisada."
    elif prodes_n:
        one=f"O imóvel de {area} ha em {city} tem um quadro que pede diligência: há {prodes_n} ocorrência(s) PRODES intersectando a área, mas isso não significa automaticamente irregularidade."
    elif anm or rare:
        one=f"O imóvel de {area} ha em {city} não mostrou embargo na leitura atual, mas há interesse mineral que merece ser entendido antes de qualquer decisão patrimonial."
    else:
        one=f"O imóvel de {area} ha em {city} não apresentou alerta crítico nas principais fontes que responderam, mas a conclusão continua condicionada às bases efetivamente consultadas."

    found=[]
    found.append(f"CAR localizado e geometria real usada como base para todos os cruzamentos.")
    if prodes_n:
        found.append(f"PRODES: {prodes_n} ocorrência(s), somando cerca de {prodes_area:.2f} ha de interseção única.")
    else:
        found.append("PRODES: nenhuma ocorrência intersectante foi localizada na consulta que respondeu.")
    if emb:
        found.append(f"Fiscalização ambiental: {emb} embargo(s) intersectante(s) identificado(s).")
    else:
        found.append("Fiscalização ambiental: nenhum embargo intersectante apareceu nas fontes que responderam.")
    if anm:
        found.append(f"Mineração: {anm} processo(s) ANM intersectam o imóvel.")
    if rare:
        found.append("Terras raras: existe sinal de interesse mineral em processo ANM e/ou camada pública do SGB; isso é triagem, não prova de jazida.")
    if water.get('grant_count') not in (None,'NÃO CONSULTADO'):
        found.append(f"Água: {_s(water.get('grant_count'))} outorga(s) intersectante(s) localizada(s) nas fontes consultadas.")
    if water.get('pivot_count') not in (None,'NÃO CONSULTADO'):
        found.append(f"Irrigação: {_s(water.get('pivot_count'))} pivô(s) central(is) intersectante(s) na base disponível.")

    why=[]
    if prodes_n:
        why.append("Uma ocorrência PRODES mostra que houve desmatamento mapeado naquele local. Para saber se isso representa problema jurídico, é preciso olhar data, autorização, área consolidada e o contexto ambiental da época.")
    if emb:
        why.append("Embargo é diferente de simples alerta cartográfico: ele exige conferência imediata do ato, da área atingida, da vigência e dos efeitos sobre compra, crédito, uso e garantia.")
    if anm:
        why.append("Processo minerário pode afetar negociação, percepção de valor e uso futuro da terra. Ele não significa que exista uma jazida economicamente aproveitável.")
    if rare:
        why.append("Sinal de terras raras é interessante porque pode indicar relevância geológica regional, mas só pesquisa mineral de campo pode avançar de favorabilidade para ocorrência, recurso ou reserva.")
    if not why:
        why.append("O principal valor desta leitura é reduzir surpresa: ela organiza o que já sabemos, o que ainda não sabemos e quais documentos ou verificações podem mudar a decisão.")

    attention=[]
    for x in (payload.get('attention_points') or [])[:8]:
        if x and x not in attention: attention.append(str(x))
    if 'NÃO CONSULT' in _status(sources,'registro de imóveis').upper() or 'RESTR' in _status(sources,'registro de imóveis').upper():
        attention.append("Matrícula e cadeia dominial continuam sendo uma etapa separada: CAR e SIGEF não comprovam quem é o proprietário registral atual.")
    unavailable=[x.get('name') for x in sources if str(x.get('status') or '').upper() in {'INDISPONÍVEL','PARCIAL','NÃO EXECUTADA','NÃO CONSULTADA'}]
    if unavailable:
        attention.append(f"Algumas bases não entregaram resposta completa nesta emissão: {', '.join(unavailable[:5])}. Isso é ponto cego, não resultado negativo.")

    next_steps=[]
    for x in (con.get('diligence') or [])[:10]:
        if x and x not in next_steps: next_steps.append(str(x))
    if not next_steps:
        next_steps=[
            "Confirmar matrícula atualizada e titularidade registral.",
            "Revisar alertas ambientais por data e fundamento.",
            "Repetir fontes críticas na data da negociação.",
        ]

    good=[]
    for x in (con.get('positives') or [])[:6]:
        if x and x not in good: good.append(str(x))
    if not good: good.append("O imóvel foi localizado em base real e o sistema conseguiu iniciar os cruzamentos oficiais.")

    money=[]
    if emb: money.append("Embargo pode afetar crédito, prazo de fechamento e necessidade de assessoria técnica/jurídica.")
    if prodes_n: money.append("Ocorrências ambientais podem gerar custo de diligência, regularização ou renegociação, dependendo do enquadramento real.")
    if anm: money.append("Direitos minerários podem alterar percepção de valor, uso e estratégia de negociação.")
    if not money: money.append("Nenhum custo extraordinário pode ser inferido apenas pela ausência de alertas; documentos e fontes pendentes ainda podem mudar a leitura.")

    return {
        'one_sentence':one,
        'what_we_found':found,
        'why_it_matters':why,
        'attention':attention,
        'next_steps':next_steps,
        'good_points':good,
        'things_that_may_cost_money':money,
        'tone_rule':'Linguagem simples, clara e envolvente; nenhuma simplificação pode transformar incerteza em certeza.'
    }
