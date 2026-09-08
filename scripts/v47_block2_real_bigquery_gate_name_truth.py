from __future__ import annotations

import os
import sys

import sicar_overlap_hardening_v47  # noqa: F401
import sicar_integrity_v47 as sicar
from scripts import v47_block2_real_bigquery_gate as legacy


def main() -> None:
    legacy.require_configuration()
    client = sicar._client()

    legacy.inspect_admin_mesh_schema(client)
    print("RX_V47_ADMIN_SCHEMA=PASS")
    legacy.estimate_expensive_queries(client)

    raw = (os.getenv("RX_V47_SAMPLE_UFS") or ",".join(legacy.DEFAULT_SAMPLE_UFS)).upper()
    sample_ufs = tuple(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
    if not sample_ufs or any(uf not in legacy.EXPECTED_UFS for uf in sample_ufs):
        legacy.fail(f"RX_V47_SAMPLE_UFS inválido: {sample_ufs}")

    for uf in sample_ufs:
        car_code = legacy.sample_car(client, uf)
        result = sicar.query_car_integrity_v47(car_code, client=client)
        legacy.assert_result(car_code, result)
        print(
            "RX_V47_REAL_SAMPLE=PASS",
            f"uf={uf}",
            f"car={car_code}",
            f"snapshot={result.get('snapshot')}",
            f"overlaps={(result.get('overlap') or {}).get('distinct_car_count')}",
        )

    legacy.assert_curvelo_benchmark(client)

    if (os.getenv("RX_V47_NATIONAL_VALIDATION_APPROVED") or "0").strip() != "1":
        print("RX_V47_UF_PRESENCE_ANY_DATE=PENDING_COST_REVIEW")
        print("RX_V47_BLOCK2_REAL_BIGQUERY_GATE=PRENATIONAL_PASS")
        return

    present = legacy.source_coverage(client)
    missing = sorted(legacy.EXPECTED_UFS - present)
    if missing:
        legacy.fail(f"area_imovel não contém geometria em alguma data para UFs: {missing}")
    print("RX_V47_UF_PRESENCE_ANY_DATE=PASS ufs=27")
    print("RX_V47_BLOCK2_REAL_BIGQUERY_GATE=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RX_V47_BLOCK2_REAL_BIGQUERY_GATE=FAIL {type(exc).__name__}:{exc}", file=sys.stderr)
        raise
