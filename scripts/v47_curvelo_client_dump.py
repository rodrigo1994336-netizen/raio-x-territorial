from __future__ import annotations

import json

import sicar_integrity_v47 as sicar
import sicar_overlap_hardening_v47  # noqa: F401 - patches overlap engine before query
from sicar_integrity_display_v47 import format_snapshot_ptbr, panel_table_rows
from scripts.v47_block2_real_bigquery_gate import CURVELO_CAR, inspect_admin_mesh_schema


def main() -> None:
    client = sicar._client()
    inspect_admin_mesh_schema(client)
    result = sicar.query_car_integrity_v47(CURVELO_CAR, client=client)
    if not result.get("ok"):
        raise RuntimeError(result.get("detail") or "Curvelo query unavailable")

    prop = result.get("property") or {}
    overlap = result.get("overlap") or {}
    municipality = result.get("municipality_boundary") or {}
    uf = result.get("uf_boundary") or {}
    table = panel_table_rows(result)

    payload = {
        "car_code": CURVELO_CAR,
        "snapshot": result.get("snapshot"),
        "snapshot_display": format_snapshot_ptbr(result.get("snapshot")),
        "property": {
            "id_municipio": prop.get("id_municipio"),
            "sigla_uf": prop.get("sigla_uf"),
            "declared_area_ha": prop.get("declared_area_ha"),
            "measured_area_ha": prop.get("measured_area_ha"),
        },
        "overlap": {
            "distinct_car_count": overlap.get("distinct_car_count"),
            "union_area_ha": overlap.get("union_area_ha"),
            "property_pct": overlap.get("property_pct"),
            "state": overlap.get("state"),
        },
        "municipality_boundary": {
            "outside_ha": municipality.get("outside_ha"),
            "outside_pct": municipality.get("outside_pct"),
            "has_outside_area": municipality.get("has_outside_area"),
            "state": municipality.get("state"),
            "label": municipality.get("label"),
        },
        "uf_boundary": {
            "outside_ha": uf.get("outside_ha"),
            "outside_pct": uf.get("outside_pct"),
            "has_outside_area": uf.get("has_outside_area"),
            "state": uf.get("state"),
            "label": uf.get("label"),
        },
        "table_rows": table,
    }
    print("RX_V47_CURVELO_CLIENT_PAYLOAD=" + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
