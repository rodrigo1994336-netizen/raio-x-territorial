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


def _consulted(srcs, term):
    st=_status(srcs,term).upper()
    return ('CONSULTAD' in st and 'NÃO' not in st and 'NAO' not in st) or st=='OK'


def _missing_or_partial(srcs):
    out=[]
    for x in srcs or []:
        st=str(x.get('status') or '').upper()
        if any(k in st for k in ('NÃO CONSULT','NAO CONSULT','INDISPON','PARCIAL','RESTRITA','NÃO EXECUT','NAO EXECUT')):
            out.append(str(x.get('name') or 'fonte'))
    return out


def build_narrative(payload: dict[str,Any]) -> dict[str,Any]:
    prop=payload.get('property') or {}; env=payload.get('environment') or {}; enf=payload.get('enforcement') or {}; mining=payload.get('mining') or {}; water=payload.get('water') or {}; con=payload.get('conclusion') or {}; sources=payload.get('sources') or []
    pd=env.get('prodes') or {}; lens=pd.get('lens') or {}; hist=lens.get('historical') or {}; post=lens.get('post_2019_07_31') or {}; credit=lens.get('credit_screening') or {}
    prodes_n=_n(hist.get('occurrence_count',pd.get('count'))); prodes_area=_f(hist.get('area_unique_ha',pd.get('area_ha')))
    post_n=_n(post.get('occurrence_count')); post_area=_f(post.get('area_sum_ha'))
    emb=_n(enf.get('embargo_count')); anm=_n(mining.get('process_count')); rare=str(mining.get('rare_earth_signal') or '').upper()=='SIM'
    area=_s(prop.get('area_ha')); city=f"{_s(prop.get('municipality'))}/{_s(prop.get('uf'))}"

    if emb:
        one=f"O imóvel de {area} ha em {city} merece atenção imediata porque encontramos embargo ambiental intersectando a área analisada."
    elif post_n:
        one=f"O imóvel de {area} ha em {city} tem {prodes_n} ocorrência(s) PRODES no histórico e {post_n} ocorrência(s) no recorte pós-31/07/2019 usado na triagem de crédito; isso exige diligência, mas não prova irregularidade por si só."
    elif prodes_n:
        one=f"O imóvel de {area} ha em {city} tem {prodes_n} ocorrência(s) PRODES históricas, sem detecção pós-31/07/2019 entre as ocorrências retornadas nesta consulta."
    elif anm or rare:
        one=f"O imóvel de {area} ha em {city} não mostrou embargo na leitura atual, mas há interesse mineral que merece ser entendido antes de qualquer decisão patrimonial."
    else:
        one=f"O imóvel de {area} ha em {city} não apresentou alerta crítico nas principais fontes que responderam, mas a conclusão continua condicionada às bases efetivamente consultadas."

    found=['CAR localizado e geometria real usada como base para os cruzamentos.']
    if prodes_n:
        years=', '.join(str(x) for x in hist.get('years') or []) or 'anos não informados'
        found.append(f"PRODES histórico: {prodes_n} ocorrência(s), cerca de {prodes_area:.2f} ha de interseção única; anos identificados: {years}.")
        found.append(f"Recorte pós-31/07/2019: {post_n} ocorrência(s), soma aproximada de {post_area:.2f} ha nas ocorrências retornadas.")
    else:
        found.append('PRODES: nenhuma ocorrência intersectante foi localizada na consulta que respondeu.')
    found.append(f"Fiscalização ambiental: {emb} embargo(s) intersectante(s) identificado(s)." if emb else 'Fiscalização ambiental: nenhum embargo intersectante apareceu nas fontes que responderam.')
    if anm: found.append(f"Mineração: {anm} processo(s) ANM intersectam o imóvel.")
    if rare: found.append('Terras raras: existe sinal de interesse mineral em processo ANM e/ou camada pública do SGB; isso é triagem, não prova de jazida.')
    if water.get('grant_count') not in (None,'NÃO CONSULTADO'): found.append(f"Água: {_s(water.get('grant_count'))} outorga(s) intersectante(s) localizada(s) nas fontes consultadas.")
    if water.get('pivot_count') not in (None,'NÃO CONSULTADO'): found.append(f"Irrigação: {_s(water.get('pivot_count'))} pivô(s) central(is) intersectante(s) na base disponível.")

    why=[]
    if prodes_n:
        why.append('O histórico PRODES ajuda a reconstruir quando houve desmatamento mapeado. Ocorrência cartográfica não equivale automaticamente a infração; data, autorização e enquadramento ambiental continuam necessários.')
    if post_n:
        why.append('Para crédito rural, o MCR exige atenção especial à supressão de vegetação nativa posterior a 31/07/2019. Por isso o Raio-X mostra esse recorte separado do histórico antigo, em vez de misturar tudo em um único número.')
    if emb: why.append('Embargo é diferente de simples alerta cartográfico: exige conferência imediata do ato, da área atingida, da vigência e dos efeitos sobre compra, crédito, uso e garantia.')
    if anm: why.append('Processo minerário pode afetar negociação, percepção de valor e uso futuro da terra. Ele não significa que exista uma jazida economicamente aproveitável.')
    if rare: why.append('Sinal de terras raras é interessante porque pode indicar relevância geológica regional, mas só pesquisa mineral de campo pode avançar de favorabilidade para ocorrência, recurso ou reserva.')
    if not why: why.append('O principal valor desta leitura é reduzir surpresa: ela organiza o que já sabemos, o que ainda não sabemos e quais documentos ou verificações podem mudar a decisão.')

    attention=[]
    for x in (payload.get('attention_points') or [])[:8]:
        if x and x not in attention: attention.append(str(x))
    if not _consulted(sources,'registro de imóveis'):
        attention.append('Matrícula e cadeia dominial continuam sendo uma etapa separada: CAR e SIGEF não comprovam quem é o proprietário registral atual.')
    if post_n:
        attention.append(f"Há {post_n} ocorrência(s) PRODES no recorte pós-31/07/2019. A análise de crédito deve conferir documentação ambiental e a regra vigente; o Raio-X não transforma isso em impedimento automático.")
    missing=_missing_or_partial(sources)
    if missing:
        attention.append(f"Algumas bases não entregaram resposta completa nesta emissão: {', '.join(missing[:6])}. Isso é ponto cego, não resultado negativo.")

    next_steps=[]
    if not _consulted(sources,'registro de imóveis'): next_steps.append('Obter matrícula atualizada e verificar titularidade, ônus e cadeia dominial.')
    if not _consulted(sources,'snci'): next_steps.append('Completar a consulta SNCI/INCRA quando o conector público/autenticado estiver disponível.')
    if post_n: next_steps.append('Conferir cada ocorrência PRODES pós-31/07/2019 por data, autorização e documento ambiental aplicável à operação de crédito.')
    elif prodes_n: next_steps.append('Interpretar as ocorrências PRODES históricas por data e contexto ambiental, sem tratá-las automaticamente como infração atual.')
    if _n(water.get('grant_count'))>0: next_steps.append('Conferir processo, portaria, vigência, finalidade, autoridade emissora e condições das outorgas que intersectam o imóvel.')
    if not _consulted(sources,'patrimônio') and not _consulted(sources,'iphan'): next_steps.append('Completar patrimônio arqueológico/IPHAN e registrar distância dos sítios mais próximos.')
    if not _consulted(sources,'floresta pública'): next_steps.append('Completar o cruzamento com o Cadastro Nacional de Florestas Públicas.')
    if not _consulted(sources,'aeródrom'): next_steps.append('Completar aeródromos e infraestrutura logística regional.')
    if not _consulted(sources,'conab'): next_steps.append('Completar safras monitoradas e armazéns CONAB na região.')
    if not _consulted(sources,'composição do solo') and not _consulted(sources,'soilgrids'): next_steps.append('Completar composição físico-química estimada do solo: argila, areia, silte, pH, carbono, CTC e nitrogênio.')
    partials=[x for x in missing if 'PARCIAL' in _status(sources,x).upper()]
    if partials: next_steps.append('Reexecutar as fontes que retornaram parcialmente com consulta paginada/recortada, sem elevar o consumo de memória.')
    if not next_steps: next_steps.append('Repetir as fontes críticas na data da negociação para detectar mudanças posteriores a esta emissão.')

    good=[]
    good.append('CAR localizado com geometria real.')
    if _consulted(sources,'ibama') and not emb: good.append('Nenhum embargo ambiental intersectante foi localizado nas fontes de fiscalização que responderam.')
    if _consulted(sources,'anm') and not anm: good.append('Nenhum processo ANM intersectante foi localizado na consulta atual.')
    if _consulted(sources,'outorgas'): good.append('A situação hídrica foi efetivamente consultada, em vez de inferida por ausência de informação.')

    money=[]
    if emb: money.append('Embargo pode afetar crédito, prazo de fechamento e necessidade de assessoria técnica/jurídica.')
    if post_n: money.append('Detecção PRODES pós-31/07/2019 pode exigir documentação adicional na análise de crédito rural e deve ser verificada antes de fechar a operação.')
    elif prodes_n: money.append('Ocorrências PRODES históricas podem gerar custo de diligência ou regularização dependendo do enquadramento real.')
    if anm: money.append('Direitos minerários podem alterar percepção de valor, uso e estratégia de negociação.')
    if not money: money.append('Nenhum custo extraordinário pode ser inferido apenas pela ausência de alertas; documentos e fontes pendentes ainda podem mudar a leitura.')

    return {
        'one_sentence':one,
        'what_we_found':found,
        'why_it_matters':why,
        'attention':attention,
        'next_steps':next_steps,
        'good_points':good,
        'things_that_may_cost_money':money,
        'credit_screening':credit,
        'tone_rule':'Linguagem simples, clara e envolvente; nenhuma simplificação pode transformar incerteza em certeza.'
    }
