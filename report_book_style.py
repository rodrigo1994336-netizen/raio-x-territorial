from __future__ import annotations

from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Spacer

import report_engine_v5 as base

# Editorial contract: all narrative prose is justified; headings remain left aligned.
# Tables/callouts are centered inside the same 165 mm editorial grid so every page
# follows a predictable visual axis.
for key in ('body','small','cell','bullet','quote'):
    s=base.S[key]
    s.alignment=TA_JUSTIFY
    s.allowWidows=0
    s.allowOrphans=0

base.S['body'].fontSize=8.0
base.S['body'].leading=11.6
base.S['body'].spaceAfter=5
base.S['body'].firstLineIndent=0
base.S['small'].fontSize=6.4
base.S['small'].leading=8.8
base.S['cell'].fontSize=6.45
base.S['cell'].leading=8.8
base.S['bullet'].fontSize=7.45
base.S['bullet'].leading=10.5
base.S['quote'].fontSize=10.0
base.S['quote'].leading=14.2

for key in ('cover','h1','h2','tag','cellb','th'):
    base.S[key].alignment=TA_LEFT
for key in ('h1','h2'):
    base.S[key].keepWithNext=1
base.S['h1'].spaceBefore=2
base.S['h1'].spaceAfter=5
base.S['h2'].spaceBefore=8
base.S['h2'].spaceAfter=4

_orig_section=base._section
_orig_info=base._info
_orig_kpis=base._kpis
_orig_callout=base._callout
_orig_sources=base._sources_table
_orig_decision=base._decision_columns
_orig_image=base._image


def _section(title,subtitle=None):
    out=_orig_section(title,subtitle)
    # Thin editorial rule gives each chapter the same visual opening.
    insert_at=1 if not subtitle else 2
    out.insert(insert_at,HRFlowable(width=165*mm,thickness=.55,color=base.LINE,hAlign='CENTER'))
    out.insert(insert_at+1,Spacer(1,1.6*mm))
    return out


def _center(flowable):
    try:flowable.hAlign='CENTER'
    except Exception:pass
    return flowable


def _info(rows,widths=None,headers=None):
    return _center(_orig_info(rows,widths,headers))


def _kpis(items):
    return _center(_orig_kpis(items))


def _callout(title,text,tone='info'):
    return _center(_orig_callout(title,text,tone))


def _sources_table(sources):
    return _center(_orig_sources(sources))


def _decision_columns(nar):
    return _center(_orig_decision(nar))


def _image(path,caption):
    out=_orig_image(path,caption)
    for x in out:
        _center(x)
    return out

base._section=_section
base._info=_info
base._kpis=_kpis
base._callout=_callout
base._sources_table=_sources_table
base._decision_columns=_decision_columns
base._image=_image

print('RX_REPORT_BOOK_STYLE=justified_symmetric_editorial_grid',flush=True)
