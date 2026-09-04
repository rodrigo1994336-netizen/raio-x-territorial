from __future__ import annotations

from datetime import date, datetime
from typing import Any

CREDIT_CUTOFF=date(2019,7,31)


def _f(v, default=0.0):
    try:return float(v)
    except Exception:return default


def _year(props:dict[str,Any]):
    y=props.get('year') or props.get('ano') or props.get('year_prodes')
    try:return int(str(y)[:4])
    except Exception:return None


def _image_date(props:dict[str,Any]):
    raw=props.get('image_date') or props.get('data_imagem') or props.get('date')
    if not raw:return None
    s=str(raw)[:10]
    for fmt in ('%Y-%m-%d','%d/%m/%Y'):
        try:return datetime.strptime(s,fmt).date()
        except Exception:pass
    return None


def _post_cutoff(props:dict[str,Any]):
    d=_image_date(props)
    if d is not None:return d>CREDIT_CUTOFF
    y=_year(props)
    return bool(y and y>=2020)


def derive_prodes_lens(prodes:dict[str,Any]|None, fiscal_modules:Any=None):
    p=prodes or {}
    ex=p.get('exact') or {}
    occ=ex.get('occurrences') or []
    hist_years=[]; post=[]
    for item in occ:
        props=item.get('properties') or {}
        y=_year(props)
        if y is not None:hist_years.append(y)
        if _post_cutoff(props):post.append(item)
    post_years=[]
    for item in post:
        y=_year(item.get('properties') or {})
        if y is not None:post_years.append(y)
    post_area=sum(_f(x.get('area_intersection_ha')) for x in post)
    try:mf=float(fiscal_modules)
    except Exception:mf=None
    return {
        'historical':{
            'occurrence_count':int(ex.get('occurrence_count') or len(occ) or 0),
            'area_unique_ha':_f(ex.get('area_unique_ha')),
            'years':sorted(set(hist_years)),
        },
        'post_2019_07_31':{
            'occurrence_count':len(post),
            'area_sum_ha':round(post_area,6),
            'years':sorted(set(post_years)),
            'cutoff':'2019-07-31',
        },
        'credit_screening':{
            'mcr_check_required':len(post)>0,
            'fiscal_modules':mf,
            'reading':(
                'Há detecção PRODES posterior a 31/07/2019. Para crédito rural, a ocorrência deve ser conferida conforme o MCR vigente e a documentação ambiental aplicável; isso não equivale automaticamente a impedimento definitivo.'
                if post else
                'Nenhuma detecção PRODES posterior a 31/07/2019 foi identificada entre as ocorrências retornadas nesta consulta. Isso não substitui a verificação da instituição financeira nem outras bases ambientais.'
            ),
            'regulatory_basis':'MCR 2-9: verificação de supressão de vegetação nativa após 31/07/2019.',
        },
        'explanation':'O Raio-X mantém o histórico PRODES completo e, separadamente, destaca o recorte pós-31/07/2019 usado na triagem de crédito rural. Os dois números respondem perguntas diferentes.'
    }
