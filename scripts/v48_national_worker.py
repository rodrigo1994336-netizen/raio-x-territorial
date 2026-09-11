from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import shutil
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sicar_geometry_normalization_v48 as geometry_contract

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
SOURCE_TABLE = f"{DATASET}.area_imovel"
CANONICAL_MANIFEST_FINGERPRINT = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
BUCKET = "raio-x-territorial-car-metodo-afp-plataforma"
PAGE_SIZE = 10_000
MAX_BQ_BYTES = 2 * 1024**3
MIN_ZOOM = 10
MAX_ZOOM = 16
TIPPECANOE_VERSION = "2.79.0"
SCHEMA_VERSION = "v48-national-worker-metrics-2"
PUBLICATION_ROOT = f"car/national/{CANONICAL_MANIFEST_FINGERPRINT}"


def die(message: str) -> None:
    raise RuntimeError(message)


def _none_default(value: Any, default: Any) -> Any:
    return default if value is None else value


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(block_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def extraction_sql() -> str:
    return f"""
    WITH source_rows AS (
      SELECT id_imovel, id_municipio, status, area, data_atualizacao, geometria
      FROM `{SOURCE_TABLE}`
      WHERE sigla_uf=@uf
        AND data_extracao=@snapshot
    ), per_car_source AS (
      SELECT
        id_imovel,
        ARRAY_AGG(
          STRUCT(id_municipio, status, area, data_atualizacao)
          ORDER BY data_atualizacao DESC NULLS LAST,
                   id_municipio ASC,
                   status ASC
          LIMIT 1
        )[OFFSET(0)] AS attrs,
        COUNT(*) AS source_row_count,
        COUNTIF(geometria IS NOT NULL) AS geometry_row_count,
        ST_UNION_AGG(geometria) AS source_geometry
      FROM source_rows
      GROUP BY id_imovel
    )
    {geometry_contract.normalization_ctes('per_car_source')}
    SELECT
      id_imovel,
      attrs.id_municipio AS id_municipio,
      attrs.status AS status,
      SAFE_CAST(attrs.area AS FLOAT64) AS area_ha,
      source_row_count,
      geometry_row_count,
      source_geometry_type,
      render_geometry_type,
      ST_NUMPOINTS(render_geometry) AS geometry_points,
      source_geometry_fingerprint,
      render_geometry_fingerprint,
      discarded_line_components,
      discarded_line_length_m,
      discarded_point_components,
      polygon_area_before_m2,
      polygon_area_after_m2,
      polygon_area_difference_m2,
      polygon_area_tolerance_m2,
      has_no_polygonal_component,
      discarded_nonpolygon_components,
      normalization_applied,
      ST_ASGEOJSON(render_geometry) AS geometry_geojson
    FROM normalized_geometry_metrics
    ORDER BY id_imovel
    """


def static_contract() -> None:
    sql = extraction_sql().upper()
    if PROJECT != "metodo-afp-plataforma":
        die("project_contract_changed")
    if "DATA_EXTRACAO=@SNAPSHOT" not in sql:
        die("exact_snapshot_predicate_missing")
    if "MAX(DATA_EXTRACAO)" in sql:
        die("latest_snapshot_fallback_detected")
    if "ST_DUMP(SOURCE_GEOMETRY, 2)" not in sql:
        die("canonical_polygonal_extraction_missing")
    if "GEOMETRIA IS NOT NULL" in sql.split("PER_CAR_SOURCE", 1)[0]:
        die("null_geometry_filtered_before_explicit_classification")
    if PAGE_SIZE != 10_000:
        die("page_size_contract_changed")
    if MAX_BQ_BYTES != 2 * 1024**3:
        die("bigquery_guard_contract_changed")
    if geometry_contract.NORMALIZATION_VERSION != "v48-polygonal-extraction-1":
        die("geometry_normalization_version_changed")


class ResourceSampler:
    def __init__(self, temp_root: Path, psutil_module: Any) -> None:
        self.temp_root = temp_root
        self.psutil = psutil_module
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.peak_rss_bytes = 0
        self.peak_system_cpu_percent = 0.0
        self.peak_temp_used_bytes = 0

    def start(self) -> None:
        self.psutil.cpu_percent(interval=None)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)
        self._sample()

    def _run(self) -> None:
        while not self.stop_event.wait(0.5):
            self._sample()

    def _sample(self) -> None:
        try:
            root = self.psutil.Process(os.getpid())
            rss = 0
            for proc in [root] + root.children(recursive=True):
                try:
                    rss += int(proc.memory_info().rss)
                except Exception:
                    pass
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
            self.peak_system_cpu_percent = max(
                self.peak_system_cpu_percent, float(self.psutil.cpu_percent(interval=None))
            )
            disk = shutil.disk_usage(self.temp_root)
            self.peak_temp_used_bytes = max(self.peak_temp_used_bytes, int(disk.total - disk.free))
        except Exception:
            pass


def _normalize_process_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def probe_tippecanoe_version() -> dict[str, Any]:
    command = ["tippecanoe", "--version"]
    print(f"RX_V48_DIAGNOSTIC_PROCESS_COMMAND={shlex.join(command)}")
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    stdout = _normalize_process_text(proc.stdout)
    stderr = _normalize_process_text(proc.stderr)
    combined = " ".join(x for x in (stdout, stderr) if x)
    channels = "+".join(name for name, value in (("stdout", stdout), ("stderr", stderr)) if value) or "none"
    print(f"RX_V48_TIPPECANOE_VERSION_EXIT_CODE={proc.returncode}")
    print(f"RX_V48_TIPPECANOE_VERSION_CHANNELS={channels}")
    print(f"RX_V48_TIPPECANOE_VERSION_STDOUT={stdout!r}")
    print(f"RX_V48_TIPPECANOE_VERSION_STDERR={stderr!r}")
    if proc.returncode != 0:
        die(f"tippecanoe_version_probe_failed:exit_code={proc.returncode}:channels={channels}:stdout={stdout!r}:stderr={stderr!r}")
    if TIPPECANOE_VERSION not in combined:
        die(f"tippecanoe_version_mismatch:expected={TIPPECANOE_VERSION}:exit_code={proc.returncode}:channels={channels}:stdout={stdout!r}:stderr={stderr!r}")
    return {"version": combined, "stdout": stdout, "stderr": stderr, "channels": channels, "exit_code": proc.returncode}


def run_tippecanoe(ndjson: Path, output: Path, log_path: Path, version_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    probe = version_probe or probe_tippecanoe_version()
    command = [
        "tippecanoe", "--output", str(output), "--layer", "car",
        "--minimum-zoom", str(MIN_ZOOM), "--maximum-zoom", str(MAX_ZOOM),
        "--read-parallel", "--no-feature-limit", "--no-tile-size-limit", "--force", str(ndjson),
    ]
    started = time.perf_counter()
    print(f"RX_V48_DIAGNOSTIC_PROCESS_COMMAND={shlex.join(command)}")
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        print(f"RX_V48_DIAGNOSTIC_PROCESS_EXIT_CODE={proc.returncode}", file=sys.stderr)
        print(f"RX_V48_DIAGNOSTIC_PROCESS_FAILED_COMMAND={shlex.join(command)}", file=sys.stderr)
        print(tail, file=sys.stderr)
        die(f"tippecanoe_failed:{proc.returncode}:{tail}")
    if not output.exists() or output.stat().st_size <= 127:
        die("pmtiles_missing_or_too_small")
    with output.open("rb") as handle:
        if not handle.read(7).startswith(b"PMTiles"):
            die("pmtiles_magic_invalid")
    return {"version": probe["version"], "version_channels": probe["channels"], "version_exit_code": probe["exit_code"], "elapsed_seconds": round(elapsed, 3), "log_bytes": log_path.stat().st_size}

def upload_immutable_file(bucket: Any, local_path: Path, object_name: str, metadata: dict[str, str], content_type: str) -> dict[str, Any]:
    from google.api_core.exceptions import PreconditionFailed

    blob = bucket.blob(object_name)
    local_sha = sha256_file(local_path)
    full_meta = {**metadata, "sha256": local_sha}
    started = time.perf_counter()
    try:
        blob.metadata = full_meta
        blob.upload_from_filename(str(local_path), content_type=content_type, if_generation_match=0, timeout=1800)
        blob.reload()
        disposition = "created"
    except PreconditionFailed:
        blob.reload()
        remote_meta = blob.metadata or {}
        if remote_meta.get("sha256") != local_sha:
            die(f"immutable_object_sha_conflict:{object_name}")
        if int(_none_default(blob.size, -1)) != local_path.stat().st_size:
            die(f"immutable_object_size_conflict:{object_name}")
        disposition = "reused_identical"
    return {
        "object": object_name,
        "generation": str(blob.generation or ""),
        "size_bytes": int(_none_default(blob.size, local_path.stat().st_size)),
        "sha256": local_sha,
        "disposition": disposition,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _audit_record(row: Any, car_code: str) -> dict[str, Any]:
    return {
        "car_code": car_code,
        "source_geometry_type": row.get("source_geometry_type"),
        "render_geometry_type": row.get("render_geometry_type"),
        "source_geometry_fingerprint": row.get("source_geometry_fingerprint"),
        "render_geometry_fingerprint": row.get("render_geometry_fingerprint"),
        "discarded_line_components": int(_none_default(row.get("discarded_line_components"), 0)),
        "discarded_line_length_m": round(float(_none_default(row.get("discarded_line_length_m"), 0.0)), 6),
        "discarded_point_components": int(_none_default(row.get("discarded_point_components"), 0)),
        "polygon_area_before_m2": round(float(_none_default(row.get("polygon_area_before_m2"), 0.0)), 6),
        "polygon_area_after_m2": round(float(_none_default(row.get("polygon_area_after_m2"), 0.0)), 6),
        "polygon_area_difference_m2": round(float(_none_default(row.get("polygon_area_difference_m2"), 0.0)), 9),
        "polygon_area_tolerance_m2": round(float(_none_default(row.get("polygon_area_tolerance_m2"), 0.0)), 9),
    }


def row_to_feature(row: Any, uf: str, snapshot: str) -> tuple[dict[str, Any] | None, int, str | None, dict[str, Any] | None]:
    car_code = str(row.get("id_imovel") or "")
    if not car_code.startswith(f"{uf}-"):
        die(f"wrong_uf_car_detected:{uf}:{car_code}")
    source_rows = int(_none_default(row.get("source_row_count"), 0))
    if source_rows <= 0:
        die(f"invalid_source_row_count:{car_code}:{source_rows}")
    geometry_rows = int(_none_default(row.get("geometry_row_count"), 0))
    if geometry_rows == 0:
        return None, 0, "no_geometry_published", {
            "car_code": car_code,
            "state": "no_geometry_published",
            "snapshot": snapshot,
        }
    if bool(row.get("has_no_polygonal_component")):
        record = _audit_record(row, car_code)
        record["state"] = "no_polygonal_component"
        record["snapshot"] = snapshot
        return None, 0, "no_polygonal_component", record

    difference = float(_none_default(row.get("polygon_area_difference_m2"), 0.0))
    tolerance = float(_none_default(row.get("polygon_area_tolerance_m2"), 0.0))
    if difference > tolerance:
        die(f"polygon_area_invariant_failed:{car_code}:{difference}>{tolerance}")

    raw_geometry = row.get("geometry_geojson")
    if not raw_geometry:
        die(f"normalized_geometry_missing:{car_code}")
    geometry = json.loads(str(raw_geometry))
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        die(f"normalized_geometry_type_invalid:{geometry.get('type')}:{car_code}")
    polygon_parts = 1 if geometry["type"] == "Polygon" else len(geometry.get("coordinates") or [])
    area_raw = row.get("area_ha")
    area_ha = None if area_raw is None else float(area_raw)
    normalized = bool(row.get("normalization_applied"))
    discarded = bool(row.get("discarded_nonpolygon_components"))
    if normalized and not discarded:
        die(f"geometrycollection_normalized_without_discard_audit:{car_code}")

    feature = {
        "type": "Feature",
        "properties": {
            "car_code": car_code,
            "id_municipio": str(row.get("id_municipio") or ""),
            "status": str(row.get("status") or ""),
            "area_ha": area_ha,
            "snapshot": snapshot,
            "geometry_normalized": normalized,
            "geometry_normalization_version": geometry_contract.NORMALIZATION_VERSION,
        },
        "geometry": geometry,
    }
    audit = _audit_record(row, car_code) if normalized else None
    if audit is not None:
        audit["state"] = "normalized_polygonal"
        audit["snapshot"] = snapshot
    return feature, polygon_parts, None, audit


def extract_geojsonl(*, client: Any, bigquery: Any, uf: str, snapshot: dt.date, expected_source_rows: int, destination: Path, shape_func: Any) -> dict[str, Any]:
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", uf),
        bigquery.ScalarQueryParameter("snapshot", "DATE", snapshot),
    ]
    dry_config = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry_job = client.query(extraction_sql(), job_config=dry_config)
    estimated_bytes = int(_none_default(dry_job.total_bytes_processed, 0))
    if estimated_bytes > MAX_BQ_BYTES:
        die(f"bigquery_dryrun_guard:{estimated_bytes}>{MAX_BQ_BYTES}")

    config = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        use_query_cache=False,
        maximum_bytes_billed=MAX_BQ_BYTES,
    )
    started = time.perf_counter()
    job = client.query(extraction_sql(), job_config=config)
    rows = job.result(timeout=10800, page_size=PAGE_SIZE)
    ndjson_fingerprint = hashlib.sha256()
    source_geometry_set_fingerprint = hashlib.sha256()
    render_geometry_set_fingerprint = hashlib.sha256()
    feature_count = polygon_count = duplicate_car_count = total_geometry_points = 0
    source_row_count_actual = distinct_car_count = 0
    normalization_applied_count = discarded_line_components = discarded_point_components = 0
    discarded_line_length_m = 0.0
    previous_car = ""
    sample: dict[str, Any] | None = None
    normalization_records: list[dict[str, Any]] = []
    excluded_geometry_records: list[dict[str, Any]] = []

    with destination.open("wb") as out:
        for row in rows:
            car_code = str(row.get("id_imovel") or "")
            if previous_car and car_code <= previous_car:
                die(f"non_deterministic_car_order:{previous_car}:{car_code}")
            previous_car = car_code
            distinct_car_count += 1
            row_count = int(_none_default(row.get("source_row_count"), 0))
            if row_count <= 0:
                die(f"invalid_source_row_count:{car_code}:{row_count}")
            source_row_count_actual += row_count
            if row_count > 1:
                duplicate_car_count += 1

            source_fp = str(row.get("source_geometry_fingerprint") or "NO_GEOMETRY")
            render_fp = str(row.get("render_geometry_fingerprint") or "NO_RENDER_GEOMETRY")
            source_geometry_set_fingerprint.update(f"{car_code}:{source_fp}\n".encode("utf-8"))
            render_geometry_set_fingerprint.update(f"{car_code}:{render_fp}\n".encode("utf-8"))

            feature, parts, excluded_state, audit = row_to_feature(row, uf, snapshot.isoformat())
            if excluded_state:
                if audit is None:
                    audit = {"car_code": car_code, "state": excluded_state, "snapshot": snapshot.isoformat()}
                excluded_geometry_records.append(audit)
                continue
            if feature is None:
                die(f"feature_missing_without_exclusion:{car_code}")

            polygon_count += parts
            total_geometry_points += int(_none_default(row.get("geometry_points"), 0))
            if audit is not None:
                normalization_applied_count += 1
                normalization_records.append(audit)
                discarded_line_components += int(_none_default(audit.get("discarded_line_components"), 0))
                discarded_point_components += int(_none_default(audit.get("discarded_point_components"), 0))
                discarded_line_length_m += float(_none_default(audit.get("discarded_line_length_m"), 0.0))

            line = (json.dumps(feature, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            out.write(line)
            ndjson_fingerprint.update(line)
            feature_count += 1
            if sample is None:
                geom = shape_func(feature["geometry"])
                point = geom.representative_point()
                if not geom.is_empty and math.isfinite(point.x) and math.isfinite(point.y):
                    sample = {"car_code": car_code, "id_municipio": feature["properties"]["id_municipio"], "lon": float(point.x), "lat": float(point.y)}

    elapsed = time.perf_counter() - started
    if source_row_count_actual != int(expected_source_rows):
        die(f"canonical_area_imovel_row_count_drift:{source_row_count_actual}!={expected_source_rows}")
    if feature_count + len(excluded_geometry_records) != distinct_car_count:
        die("distinct_car_geometry_classification_reconciliation_failed")
    if feature_count <= 0 or not sample or destination.stat().st_size <= 0:
        die("empty_or_unusable_canonical_geometry")

    no_geometry_records = [x for x in excluded_geometry_records if x.get("state") == "no_geometry_published"]
    no_polygon_records = [x for x in excluded_geometry_records if x.get("state") == "no_polygonal_component"]
    return {
        "schema_version": SCHEMA_VERSION,
        **geometry_contract.normalization_contract_fields(),
        "dry_run_bytes": estimated_bytes,
        "maximum_bytes_billed": MAX_BQ_BYTES,
        "total_bytes_processed": int(_none_default(job.total_bytes_processed, 0)),
        "total_bytes_billed": int(_none_default(job.total_bytes_billed, 0)),
        "page_size": PAGE_SIZE,
        "elapsed_seconds": round(elapsed, 3),
        "source_row_count_expected": int(expected_source_rows),
        "source_row_count_actual": source_row_count_actual,
        "distinct_car_count": distinct_car_count,
        "feature_count": feature_count,
        "excluded_car_count": len(excluded_geometry_records),
        "no_geometry_car_count": len(no_geometry_records),
        "no_geometry_car_ids": [x["car_code"] for x in no_geometry_records],
        "no_polygonal_car_count": len(no_polygon_records),
        "no_polygonal_car_ids": [x["car_code"] for x in no_polygon_records],
        "normalization_applied_count": normalization_applied_count,
        "normalization_records": normalization_records,
        "excluded_geometry_records": excluded_geometry_records,
        "discarded_line_components": discarded_line_components,
        "discarded_line_length_m": round(discarded_line_length_m, 6),
        "discarded_point_components": discarded_point_components,
        "polygon_count": polygon_count,
        "duplicate_car_count": duplicate_car_count,
        "total_geometry_points": total_geometry_points,
        "geojsonl_bytes": destination.stat().st_size,
        "fingerprint_sha256": ndjson_fingerprint.hexdigest(),
        "source_geometry_set_fingerprint_sha256": source_geometry_set_fingerprint.hexdigest(),
        "render_geometry_set_fingerprint_sha256": render_geometry_set_fingerprint.hexdigest(),
        "sample": sample,
    }


def main() -> None:
    static_contract()
    import psutil
    from google.cloud import bigquery, storage
    from shapely.geometry import shape
    import sicar_canonical_manifest_v48 as canonical

    uf = os.getenv("RX_V48_UF", "").strip().upper()
    if uf not in canonical.UF_NAMES:
        die(f"invalid_uf:{uf}")
    manifest = canonical.load_manifest()
    if manifest.get("content_fingerprint_sha256") != CANONICAL_MANIFEST_FINGERPRINT:
        die("canonical_manifest_fingerprint_mismatch")
    entry = canonical.canonical_entry(uf)
    if not entry:
        die(f"canonical_snapshot_unavailable:{uf}")
    snapshot_text = str(entry.get("snapshot") or "")
    snapshot = dt.date.fromisoformat(snapshot_text)
    expected_source_rows = int(_none_default(((entry.get("tables") or {}).get("area_imovel") or {}).get("row_count"), 0))
    if expected_source_rows <= 0:
        die(f"canonical_area_imovel_count_missing:{uf}")
    requested_snapshot = os.getenv("RX_V48_SNAPSHOT", snapshot_text).strip()
    if requested_snapshot != snapshot_text:
        die(f"snapshot_env_manifest_divergence:{requested_snapshot}!={snapshot_text}")
    expected_fp = os.getenv("RX_V48_EXPECT_SOURCE_FINGERPRINT", "").strip().lower()
    env_project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if env_project and env_project != PROJECT:
        die(f"runtime_project_mismatch:{env_project}")

    base = Path(os.getenv("RX_V48_WORKDIR", "/mnt/disks/rxv48"))
    if not base.exists():
        base = Path("/tmp/rxv48")
    base.mkdir(parents=True, exist_ok=True)
    work = base / f"national-{CANONICAL_MANIFEST_FINGERPRINT[:12]}-{uf.lower()}"
    work.mkdir(parents=True, exist_ok=True)
    ndjson = work / f"{uf}.geojsonl"
    pmtiles = work / f"{uf}.pmtiles"
    tippecanoe_log = work / "tippecanoe.log"
    receipt_path = work / "prepared-receipt.json"
    commit_path = work / "commit-manifest.json"
    metrics_path = work / "worker-metrics.json"

    tippecanoe_probe = probe_tippecanoe_version()
    print("RX_V48_TOOLCHAIN_PREFLIGHT=PASS tippecanoe_before_bigquery=true")
    bucket_name = os.getenv("RX_V48_GCS_BUCKET", BUCKET).strip() or BUCKET
    try:
        location_bucket = storage.Client(project=PROJECT).bucket(bucket_name)
        location_bucket.reload()
        print(f"RX_V48_RUNTIME_BUCKET_LOCATION={location_bucket.location}")
    except Exception as exc:
        print(f"RX_V48_RUNTIME_BUCKET_LOCATION=UNAVAILABLE:{type(exc).__name__}:{exc}")

    sampler = ResourceSampler(base, psutil)
    overall_started = time.perf_counter()
    sampler.start()
    try:
        bq = bigquery.Client(project=PROJECT)
        extraction = extract_geojsonl(
            client=bq,
            bigquery=bigquery,
            uf=uf,
            snapshot=snapshot,
            expected_source_rows=expected_source_rows,
            destination=ndjson,
            shape_func=shape,
        )
        source_fp = extraction["fingerprint_sha256"]
        if expected_fp and source_fp != expected_fp:
            die(f"rebuild_source_fingerprint_changed:{source_fp}!={expected_fp}")
        tippecanoe = run_tippecanoe(ndjson, pmtiles, tippecanoe_log, tippecanoe_probe)
        pmtiles_sha = sha256_file(pmtiles)
        pmtiles_size = pmtiles.stat().st_size

        receipt = {
            "schema_version": "v48-national-prepared-receipt-1",
            "status": "prepared",
            "canonical_manifest_fingerprint": CANONICAL_MANIFEST_FINGERPRINT,
            "uf": uf,
            "snapshot_date": snapshot_text,
            "analysis_snapshot": snapshot_text,
            "map_snapshot": snapshot_text,
            "analysis_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "map_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "source_fingerprint_sha256": source_fp,
            "source_geometry_set_fingerprint_sha256": extraction["source_geometry_set_fingerprint_sha256"],
            "render_geometry_set_fingerprint_sha256": extraction["render_geometry_set_fingerprint_sha256"],
            "source_row_count_expected": expected_source_rows,
            "source_row_count_actual": extraction["source_row_count_actual"],
            "distinct_car_count": extraction["distinct_car_count"],
            "feature_count": extraction["feature_count"],
            "no_geometry_car_count": extraction["no_geometry_car_count"],
            "no_geometry_car_ids": extraction["no_geometry_car_ids"],
            "no_polygonal_car_count": extraction["no_polygonal_car_count"],
            "no_polygonal_car_ids": extraction["no_polygonal_car_ids"],
            "normalization_applied_count": extraction["normalization_applied_count"],
            "pmtiles": {"sha256": pmtiles_sha, "size_bytes": pmtiles_size},
            "active_json_updated": False,
        }
        write_json(receipt_path, receipt)
        common_meta = {
            "canonical_manifest_fingerprint": CANONICAL_MANIFEST_FINGERPRINT,
            "uf": uf,
            "snapshot_date": snapshot_text,
            "analysis_snapshot": snapshot_text,
            "map_snapshot": snapshot_text,
            "analysis_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "map_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "source_fingerprint": source_fp,
            "source_geometry_set_fingerprint": extraction["source_geometry_set_fingerprint_sha256"],
            "render_geometry_set_fingerprint": extraction["render_geometry_set_fingerprint_sha256"],
            "pmtiles_sha256": pmtiles_sha,
            "pmtiles_size_bytes": str(pmtiles_size),
        }
        root = PUBLICATION_ROOT
        receipt_object = f"{root}/receipts/{uf}/{source_fp}.json"
        pmtiles_object = f"{root}/uf/{uf}/{source_fp}.pmtiles"
        manifest_object = f"{root}/manifests/uf/{uf}/{source_fp}.json"
        storage_client = storage.Client(project=PROJECT)
        bucket = storage_client.bucket(os.getenv("RX_V48_GCS_BUCKET", BUCKET).strip() or BUCKET)
        receipt_upload = upload_immutable_file(bucket, receipt_path, receipt_object, common_meta, "application/json")
        pmtiles_upload = upload_immutable_file(bucket, pmtiles, pmtiles_object, common_meta, "application/vnd.pmtiles")

        sampler.stop()
        finished_at = utcnow()
        total_elapsed = round(time.perf_counter() - overall_started, 3)
        commit = {
            "schema_version": "v48-national-publication-commit-1",
            "status": "committed",
            "canonical_manifest_fingerprint": CANONICAL_MANIFEST_FINGERPRINT,
            "uf": uf,
            "snapshot_date": snapshot_text,
            "analysis_snapshot": snapshot_text,
            "map_snapshot": snapshot_text,
            "analysis_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "map_geometry_normalization": geometry_contract.NORMALIZATION_VERSION,
            "source_fingerprint_sha256": source_fp,
            "source_geometry_set_fingerprint_sha256": extraction["source_geometry_set_fingerprint_sha256"],
            "render_geometry_set_fingerprint_sha256": extraction["render_geometry_set_fingerprint_sha256"],
            "source_row_count_expected": expected_source_rows,
            "source_row_count_actual": extraction["source_row_count_actual"],
            "distinct_car_count": extraction["distinct_car_count"],
            "feature_count": extraction["feature_count"],
            "no_geometry_car_count": extraction["no_geometry_car_count"],
            "no_geometry_car_ids": extraction["no_geometry_car_ids"],
            "no_polygonal_car_count": extraction["no_polygonal_car_count"],
            "no_polygonal_car_ids": extraction["no_polygonal_car_ids"],
            "normalization_applied_count": extraction["normalization_applied_count"],
            "pmtiles": {"object": pmtiles_object, "generation": pmtiles_upload["generation"], "sha256": pmtiles_sha, "size_bytes": pmtiles_size},
            "prepared_receipt": {"object": receipt_object, "generation": receipt_upload["generation"]},
            "run_metrics": {
                "source": extraction,
                "tippecanoe": tippecanoe,
                "receipt_upload_seconds": receipt_upload["elapsed_seconds"],
                "pmtiles_upload_seconds": pmtiles_upload["elapsed_seconds"],
                "worker_total_elapsed_seconds": total_elapsed,
                "resources": {
                    "machine": platform.node(),
                    "platform": platform.platform(),
                    "cpu_count": psutil.cpu_count(logical=True),
                    "peak_rss_bytes": sampler.peak_rss_bytes,
                    "peak_system_cpu_percent": round(sampler.peak_system_cpu_percent, 2),
                    "peak_temp_used_bytes": sampler.peak_temp_used_bytes,
                },
                "finished_at": finished_at,
            },
            "recovery": {"mode": "NORMAL_BUILD", "original_run_metrics_available": True},
            "active_json_updated": False,
        }
        write_json(commit_path, commit)
        manifest_upload = upload_immutable_file(bucket, commit_path, manifest_object, common_meta, "application/json")
        metrics = {**commit, "commit_manifest_upload": manifest_upload}
        write_json(metrics_path, metrics)
        print(f"RX_V48_NATIONAL_UF={uf}")
        print(f"RX_V48_NATIONAL_SNAPSHOT={snapshot_text}")
        print(f"RX_V48_NATIONAL_SOURCE_ROWS={extraction['source_row_count_actual']}")
        print(f"RX_V48_NATIONAL_DISTINCT_CARS={extraction['distinct_car_count']}")
        print(f"RX_V48_NATIONAL_FEATURES={extraction['feature_count']}")
        print(f"RX_V48_NATIONAL_NORMALIZED_CARS={extraction['normalization_applied_count']}")
        print(f"RX_V48_NATIONAL_NO_GEOMETRY_CARS={extraction['no_geometry_car_count']}")
        print(f"RX_V48_NATIONAL_NO_POLYGON_CARS={extraction['no_polygonal_car_count']}")
        print(f"RX_V48_NATIONAL_DISCARDED_LINES={extraction['discarded_line_components']}")
        print(f"RX_V48_NATIONAL_DISCARDED_LINE_LENGTH_M={extraction['discarded_line_length_m']}")
        print(f"RX_V48_NATIONAL_DISCARDED_POINTS={extraction['discarded_point_components']}")
        print(f"RX_V48_NATIONAL_SOURCE_GEOMETRY_SET_FP={extraction['source_geometry_set_fingerprint_sha256']}")
        print(f"RX_V48_NATIONAL_RENDER_GEOMETRY_SET_FP={extraction['render_geometry_set_fingerprint_sha256']}")
        print(f"RX_V48_NATIONAL_FINGERPRINT={source_fp}")
        print(f"RX_V48_NATIONAL_PMTILES_BYTES={pmtiles_size}")
        print(f"RX_V48_NATIONAL_PMTILES_SHA256={pmtiles_sha}")
        print(f"RX_V48_NATIONAL_BQ_BILLED_BYTES={extraction['total_bytes_billed']}")
        print(f"RX_V48_NATIONAL_MANIFEST_OBJECT=gs://{bucket.name}/{manifest_object}")
        print("RX_V48_NATIONAL_MAP_ANALYSIS_SNAPSHOT_EQUAL=PASS")
        print("RX_V48_NATIONAL_MAP_ANALYSIS_GEOMETRY_NORMALIZATION_EQUAL=PASS")
        print("RX_V48_NATIONAL_ACTIVE_JSON_UPDATED=NO")
        print("RX_V48_NATIONAL_WORKER=PASS")
    finally:
        if sampler.thread.is_alive():
            sampler.stop()


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-check", action="store_true")
    args = parser.parse_args()
    if args.import_check:
        static_contract()
        import sicar_canonical_manifest_v48  # noqa: F401
        print("RX_V48_NATIONAL_WORKER_IMPORT_CHECK=PASS")
        return
    main()


if __name__ == "__main__":
    try:
        cli()
    except Exception as exc:
        print(f"RX_V48_NATIONAL_WORKER=FAIL_CLOSED:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise
