"""T1 · regra do crédito rural sobre supressão de vegetação depois de 31/07/2019 (MCR 2-9).

Texto conferido no Banco Central (estudo F1, item 14, amostras p9_bcb_res_cmn_5303 e 5193):

* MCR 2-9-17 (Res. CMN 5.303, de 12/05/2026): a instituição financeira verifica se houve supressão
  da vegetação nativa após 31/07/2019 no imóvel, pela lista do Ministério do Meio Ambiente feita com
  o PRODES (BiomasBR), com início em 04/01/2027 (imóvel acima de 15 módulos fiscais), 01/07/2027
  (acima de 4 e até 15) e 03/01/2028 (até 4). 2-9-17-A: assentamento e povos e comunidades
  tradicionais com CAR do perímetro coletivo seguem a data de até 4 módulos fiscais;
* MCR 2-9-18: constatada a supressão, o crédito com recursos controlados ou direcionados fica
  CONDICIONADO à apresentação de um documento (ASV ou UAS, PRAD ou PRA, TAC, laudo de sensoriamento
  remoto da instituição financeira ou Termo de Compromisso Ambiental). Não é vedação.

Uma fonte só para o texto: relatório, lente do PRODES e narrativa leem daqui.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

CUTOFF_TEXT = "31/07/2019"
NORM = "Res. CMN 5.303/2026"
# (limite inferior exclusivo em módulos fiscais, data de início, faixa)
START_DATES = (
    (15.0, date(2027, 1, 4), "acima de 15 módulos fiscais"),
    (4.0, date(2027, 7, 1), "acima de 4 e até 15 módulos fiscais"),
    (float("-inf"), date(2028, 1, 3), "até 4 módulos fiscais"),
)
COLLECTIVE_TYPES = {"AST", "PCT"}  # assentamento; povos e comunidades tradicionais (MCR 2-9-17-A)
DOCUMENTS = ("autorização de supressão de vegetação ou de uso alternativo do solo, PRAD ou PRA, TAC, "
             "laudo de sensoriamento remoto do banco ou Termo de Compromisso Ambiental")
_BRASILIA = timezone(timedelta(hours=-3))


def today_brasilia() -> date:
    return datetime.now(_BRASILIA).date()


def _fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _fmt_modules(value: float) -> str:
    text = f"{value:.2f}".replace(".", ",")
    return f"{text} {'módulo fiscal' if value < 2 else 'módulos fiscais'}"


def start_date_for(fiscal_modules: Any, property_type: Any = None) -> tuple[date, str] | None:
    """Data em que a verificação começa para o porte do imóvel; None quando o porte não é conhecido."""
    if str(property_type or "").strip().upper() in COLLECTIVE_TYPES:
        return START_DATES[-1][1], START_DATES[-1][2]
    try:
        mf = float(fiscal_modules)
    except (TypeError, ValueError):
        return None
    if mf != mf or mf < 0:  # NaN ou negativo: porte desconhecido
        return None
    for floor, start, label in START_DATES:
        if mf > floor:
            return start, label
    return None


def _schedule_sentence() -> str:
    return (f"A verificação começa em {_fmt_date(START_DATES[0][1])} para imóveis acima de 15 módulos fiscais, "
            f"em {_fmt_date(START_DATES[1][1])} acima de 4 até 15 e em {_fmt_date(START_DATES[2][1])} até 4.")


def property_sentence(fiscal_modules: Any, property_type: Any = None, today: date | None = None) -> str:
    found = start_date_for(fiscal_modules, property_type)
    if not found:
        return ""
    start, _label = found
    today = today or today_brasilia()
    verb = "começa em" if today < start else "vale desde"
    if str(property_type or "").strip().upper() in COLLECTIVE_TYPES:
        who = "Para este imóvel de uso coletivo"
    else:
        who = f"Para este imóvel ({_fmt_modules(float(fiscal_modules))})"
    return f"{who}, a verificação {verb} {_fmt_date(start)}."


def basis_text(fiscal_modules: Any = None, property_type: Any = None, today: date | None = None) -> str:
    """Linha "Base regulatória" do relatório."""
    parts = [
        f"Manual de Crédito Rural, MCR 2-9-17 e 2-9-18 ({NORM}): o banco verifica, pela lista do Ministério do Meio "
        f"Ambiente feita com o PRODES, se houve supressão de vegetação nativa no imóvel depois de {CUTOFF_TEXT}.",
        _schedule_sentence(),
        property_sentence(fiscal_modules, property_type, today),
        f"Se houver supressão, o crédito com recursos controlados ou direcionados fica condicionado a um documento "
        f"({DOCUMENTS}); não é proibição automática.",
    ]
    return " ".join(p for p in parts if p)


def why_text(fiscal_modules: Any = None, property_type: Any = None, today: date | None = None) -> str:
    """Frase curta do "por que importa" na narrativa."""
    found = start_date_for(fiscal_modules, property_type)
    when = ""
    if found:
        start = found[0]
        today = today or today_brasilia()
        when = f" ({'a partir de' if today < start else 'desde'} {_fmt_date(start)} para o porte deste imóvel)"
    return (f"No crédito rural, o MCR manda o banco verificar supressão de vegetação nativa depois de {CUTOFF_TEXT}{when}; "
            "constatada, o crédito fica condicionado a documento. Por isso esse recorte aparece separado do histórico antigo.")
