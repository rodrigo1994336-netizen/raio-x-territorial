from __future__ import annotations

from prodes_lens import derive_prodes_lens


_LENS_KEYS={'Histórico PRODES completo','Recorte pós-31/07/2019','Triagem para crédito rural','Base regulatória','Método de área PRODES'}


def patch_prodes_lens(payload:dict,result:dict):
    env=payload.setdefault('environment',{})
    pd=env.setdefault('prodes',{})
    if (result.get('prodes') or {}).get('ok') is not True:
        # H1: a pending PRODES reading (deploy_app.finalize_prodes) has no lens numbers:
        # never "0 ocorrência(s)", never a credit-screening reading.
        pd['lens']={'status':'CONSULTA PENDENTE','historical':None,'post_2019_07_31':None}
        pd['rows']=[['Histórico PRODES completo','CONSULTA PENDENTE'],['Recorte pós-31/07/2019','CONSULTA PENDENTE']]+[r for r in (pd.get('rows') or []) if r and str(r[0]) not in _LENS_KEYS]
        rules=payload.setdefault('interpretation_rules',[])
        rule='Consulta PRODES pendente nunca é interpretada como zero ocorrência ou ausência de desmatamento.'
        if rule not in rules:rules.append(rule)
        return payload
    car=result.get('car') or {}
    props=car.get('properties') or {}
    lens=derive_prodes_lens(
        result.get('prodes') or {},
        props.get('m_fiscal'),
        car_geometry=car.get('geometry'),
        car_area_ha=props.get('area'),
    )
    pd['lens']=lens
    hist=lens.get('historical') or {}
    post=lens.get('post_2019_07_31') or {}
    credit=lens.get('credit_screening') or {}

    def pct_text(v):
        return '—' if v is None else f'{float(v):.2f}% do CAR'

    original=list(pd.get('rows') or [])
    # Remove prior versions of the same lens rows so the report has one canonical reading.
    original=[r for r in original if not r or str(r[0]) not in {
        'Histórico PRODES completo','Recorte pós-31/07/2019','Triagem para crédito rural','Base regulatória'
    }]
    lens_rows=[
        ['Histórico PRODES completo',
         f"{hist.get('occurrence_count',0)} ocorrência(s) • {float(hist.get('area_unique_ha') or 0):.6f} ha únicos • {pct_text(hist.get('pct_car'))} • anos: {', '.join(str(x) for x in hist.get('years') or []) or '—'}"],
        ['Recorte pós-31/07/2019',
         f"{post.get('occurrence_count',0)} ocorrência(s) • {float(post.get('area_unique_ha') or 0):.6f} ha únicos • {pct_text(post.get('pct_car'))} • anos: {', '.join(str(x) for x in post.get('years') or []) or '—'}"],
        ['Triagem para crédito rural',credit.get('reading') or '—'],
        ['Base regulatória',credit.get('regulatory_basis') or '—'],
        ['Método de área PRODES','Interseção geométrica exata com o CAR + união das geometrias por lente; sobreposições não são somadas em duplicidade.'],
    ]
    pd['rows']=lens_rows+original
    pd['meaning']=str(lens.get('explanation') or '')+' '+str(pd.get('meaning') or '')
    payload['credit_screening']=credit
    rules=payload.setdefault('interpretation_rules',[])
    rule='PRODES é apresentado em duas lentes separadas: histórico completo e recorte posterior a 31/07/2019. Ambas usam área única da união das interseções exatas com o CAR.'
    if rule not in rules:rules.append(rule)
    return payload


def install():
    import live_report_adapter_v9 as v9
    import live_report_adapter_v11 as v11
    v9._patch_prodes_lens=patch_prodes_lens
    v11._patch_prodes_lens=patch_prodes_lens
    print('RX_PRODES_TRUTH_V44=exact_union_two_lenses',flush=True)


install()
