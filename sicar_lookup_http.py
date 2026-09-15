"""HTTP answer for a CAR lookup that did not resolve.

"CAR não localizado" (404) is a statement about the property, so it is only made when
SICAR itself answered the exact query without it (``not_found`` is True). A lookup that
never got an answer (timeout, connection error, service exception) is a pending
consultation: 503 with Retry-After, so the portal and the proxies retry instead of
telling the client that the property does not exist (tentar não é responder).

No imports from the portal or report modules: every route can use it without cycles.
"""
from __future__ import annotations

from fastapi import HTTPException

NOT_FOUND_DETAIL = "CAR não localizado no SICAR."
PENDING_DETAIL = "Consulta ao SICAR pendente: o SICAR não respondeu agora. Tente de novo em instantes."
RETRY_AFTER_SECONDS = 30


def lookup_http_error(car: dict | None, *, not_found_detail=NOT_FOUND_DETAIL, pending_detail=PENDING_DETAIL) -> HTTPException:
    """404 only when SICAR answered without the property; 503 + Retry-After otherwise."""
    car = car if isinstance(car, dict) else {}
    if car.get('not_found') is True:
        return HTTPException(status_code=404, detail=not_found_detail)
    return HTTPException(status_code=503, detail=pending_detail, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
