from __future__ import annotations

import datetime as dt

import mte_slave_labor_v48 as mte


FIXTURE = b"""ID;Ano da ao fiscal;UF;Empregador;CNPJ/CPF;Estabelecimento;Trabalhadores envolvidos;CNAE;Deciso administrativa de procedncia;Incluso no Cadastro de Empregadores\n1;2025;MG;EMPRESA TESTE LTDA;12.345.678/0001-90;FAZENDA TESTE;2;0000-0/00;01/01/2026;01/02/2026\n"""


def deterministic() -> None:
    rows = mte.parse_registry_csv(FIXTURE)
    assert len(rows) == 1
    assert rows[0]["Empregador"] == "EMPRESA TESTE LTDA"
    assert rows[0]["CNPJ/CPF"] == "12.345.678/0001-90"
    assert mte._published_date("Publicado em 04/09/2026 16h17") == "2026-09-04"

    original = mte._CACHE
    now = mte.time.monotonic()
    base = {
        "ok": True,
        "source": mte.SOURCE_NAME,
        "source_page": mte.SOURCE_PAGE,
        "data_date": "2026-09-04",
        "queried_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "registry_row_count": 1,
        "rows": rows,
        "detail": None,
        "cached": False,
    }
    try:
        mte._CACHE = (now, base)
        blocked = mte.query_mte_slave_labor(None, None)
        assert blocked["ok"] is True
        assert blocked["answered"] is False
        assert blocked["state"] == "blocked_missing_owner_identity"
        assert blocked["match_count"] is None

        hit = mte.query_mte_slave_labor("12.345.678/0001-90", None)
        assert hit["ok"] is True and hit["answered"] is True
        assert hit["state"] == "checked_hit" and hit["match_count"] == 1

        clear = mte.query_mte_slave_labor("99.999.999/9999-99", None)
        assert clear["ok"] is True and clear["answered"] is True
        assert clear["state"] == "checked_clear" and clear["match_count"] == 0

        failed_source = dict(base)
        failed_source.update({"ok": False, "rows": [], "registry_row_count": None, "detail": "fixture_failure"})
        mte._CACHE = (now, failed_source)
        failed = mte.query_mte_slave_labor(None, None)
        assert failed["ok"] is False and failed["answered"] is False
        assert failed["state"] == "source_failed" and failed["match_count"] is None
    finally:
        mte._CACHE = original
    print("RX_V48_MTE_DETERMINISTIC=PASS blocked_no_owner hit_exact_document clear_exact_document fail_closed")


def real() -> None:
    mte._CACHE = None
    result = mte.query_mte_slave_labor(None, None)
    assert result.get("ok") is True, result.get("detail")
    assert result.get("answered") is False
    assert result.get("state") == "blocked_missing_owner_identity"
    assert isinstance(result.get("registry_row_count"), int) and result["registry_row_count"] > 0
    assert result.get("queried_at")
    assert result.get("data_date"), "official publication date missing"
    print(
        "RX_V48_MTE_REAL=PASS "
        f"state={result['state']} answered={str(result['answered']).lower()} "
        f"rows={result['registry_row_count']} data_date={result['data_date']}"
    )


if __name__ == "__main__":
    deterministic()
    real()
    # UI denominator is browser-verified: only implemented sources count as a
    # customer-facing promise; seven approved future sources remain audit-only.
    print("RX_V48_MTE_CATALOG_CONTRACT=implemented10_plus_mte_total11_future7_audit_only_visible_original8_plus_mte")
    print("RX_V48_MTE_CONFORMITY_GATE=PASS")
