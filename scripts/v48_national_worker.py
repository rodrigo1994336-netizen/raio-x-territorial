from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
SOURCE_TABLE = f"{DATASET}.area_imovel"
CANONICAL_MANIFEST_FINGERPRINT = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
BUCKET = "raio-x-territorial-car-metodo-afp-plataforma"
PAGE_SIZE = 10_000
MAX_BQ_BYTES = 10 * 1024**3
MIN_ZOOM = 10
MAX_ZOOM = 16
TIPPECANOE_VERSION = "2.79.0"
SCHEMA_VERSION = "v48-national-worker-metrics-1"
PUBLICATION_ROOT = f"car/national/{CANONICAL_MANIFEST_FINGERPRINT}"


def die(message: str) -> None:
    raise RuntimeError(message)


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
        AND geometria IS NOT NULL
    ), per_car AS (
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
        ST_UNION_AGG(geometria) AS geometria
      FROM source_rows
      GROUP BY id_imovel
    )
    SELECT
      id_imovel,
      attrs.id_municipio AS id_municipio,
      attrs.status AS status,
      SAFE_CAST(attrs.area AS FLOAT64) AS area_ha,
      source_row_count,
      ST_GEOMETRYTYPE(geometria) AS geometry_type,
      ST_NUMPOINTS(geometria) AS geometry_points,
      ST_ASGEOJSON(geometria) AS geometry_geojson
    FROM per_car
    WHERE geometria IS NOT NULL
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
    if PAGE_SIZE != 10_000:
        die("page_size_contract_changed")
    if MAX_BQ_BYTES != 10 * 1024**3:
        die("bigquery_guard_contract_changed")


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


def run_tippecanoe(ndjson: Path, output: Path, log_path: Path) -> dict[str, Any]:
    version = subprocess.run(["tippecanoe", "--version"], check=True, text=True, capture_output=True).stdout.strip()
    if TIPPECANOE_VERSION not in version:
        die(f"tippecanoe_version_mismatch:{version}")
    command = [
        "tippecanoe", "--output", str(output), "--layer", "car",
        "--minimum-zoom", str(MIN_ZOOM), "--maximum-zoom", str(MAX_ZOOM),
        "--read-parallel", "--no-feature-limit", "--no-tile-size-limit", "--force", str(ndjson),
    ]
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        die(f"tippecanoe_failed:{proc.returncode}:{tail}")
    if not output.exists() or output.stat().st_size <= 127:
        die("pmtiles_missing_or_too_small")
    with output.open("rb") as handle:
        if not handle.read(7).startswith(b"PMTiles"):
            die("pmtiles_magic_invalid")
    return {"version": version, "elapsed_seconds": round(elapsed, 3), "log_bytes": log_path.stat().st_size}


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
        if int(blob.size or -1) != local_path.stat().st_size:
            die(f"immutable_object_size_conflict:{object_name}")
        disposition = "reused_identical"
    return {
        "object": object_name,
        "generation": str(blob.generation or ""),
        "size_bytes": int(blob.size or local_path.stat().st_size),
        "sha256": local_sha,
        "disposition": disposition,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def row_to_feature(row: Any, uf: str, snapshot: str) -> tuple[dict[str, Any], int]:
    raw_geometry = row.get("geometry_geojson")
    if not raw_geometry:
        die(f"missing_geometry:{row.get('id_imovel')}")
    geometry = json.loads(str(raw_geometry))
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        die(f"unexpected_geometry_type:{geometry.get('type')}:{row.get('id_imovel')}")
    polygon_parts = 1 if geometry["type"] == "Polygon" else len(geometry.get("coordinates") or [])
    car_code = str(row.get("id_imovel") or "")
    if not car_code.startswith(f"{uf}-"):
        die(f"wrong_uf_car_detected:{uf}:{car_code}")
    feature = {
        "type": "Feature",
        "properties": {
            "car_code": car_code,
            "id_municipio": str(row.get("id_municipio") or ""),
            "status": str(row.get("status") or ""),
            "area_ha": float(row.get("area_ha") or 0.0),
            "snapshot": snapshot,
        },
        "geometry": geometry,
    }
    return feature, polygon_parts


def extract_geojsonl(*, client: Any, bigquery: Any, uf: str, snapshot: dt.date, expected_source_rows: int, destination: Path, shape_func: Any) -> dict[str, Any]:
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", uf),
        bigquery.ScalarQueryParameter("snapshot", "DATE", snapshot),
    ]
    dry_config = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry_job = client.query(extraction_sql(), job_config=dry_config)
    estimated_bytes = int(dry_job.total_bytes_processed or 0)
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
    fingerprint = hashlib.sha256()
    feature_count = polygon_count = duplicate_car_count = total_geometry_points = source_row_count_actual = 0
    previous_car = ""
    sample: dict[str, Any] | None = None
    with destination.open("wb") as out:
        for row in rows:
            feature, parts = row_to_feature(row, uf, snapshot.isoformat())
            car_code = feature["properties"]["car_code"]
            if previous_car and car_code <= previous_car:
                die(f"non_deterministic_car_order:{previous_car}:{car_code}")
            previous_car = car_code
            row_count = int(row.get("source_row_count") or 0)
            if row_count <= 0:
                die(f"invalid_source_row_count:{car_code}:{row_count}")
            source_row_count_actual += row_count
            if row_count > 1:
                duplicate_car_count += 1
            polygon_count += parts
            total_geometry_points += int(row.get("geometry_points") or 0)
            line = (json.dumps(feature, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            out.write(line)
            fingerprint.update(line)
            feature_count += 1
            if sample is None:
                geom = shape_func(feature["geometry"])
                point = geom.representative_point()
                if not geom.is_empty and math.isfinite(point.x) and math.isfinite(point.y):
                    sample = {"car_code": car_code, "id_municipio": feature["properties"]["id_municipio"], "lon": float(point.x), "lat": float(point.y)}
    elapsed = time.perf_counter() - started
    if source_row_count_actual != int(expected_source_rows):
        die(f"canonical_area_imovel_row_count_drift:{source_row_count_actual}!={expected_source_rows}")
    if feature_count <= 0 or not sample or destination.stat().st_size <= 0:
        die("empty_or_unusable_canonical_geometry")
    return {
        "dry_run_bytes": estimated_bytes,
        "maximum_bytes_billed": MAX_BQ_BYTES,
        "total_bytes_processed": int(job.total_bytes_processed or 0),
        "total_bytes_billed": int(job.total_bytes_billed or 0),
        "page_size": PAGE_SIZE,
        "elapsed_seconds": round(elapsed, 3),
        "source_row_count_expected": int(expected_source_rows),
        "source_row_count_actual": source_row_count_actual,
        "feature_count": feature_count,
        "polygon_count": polygon_count,
        "duplicate_car_count": duplicate_car_count,
        "total_geometry_points": total_geometry_points,
        "geojsonl_bytes": destination.stat().st_size,
        "fingerprint_sha256": fingerprint.hexdigest(),
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
    expected_source_rows = int(((entry.get("tables") or {}).get("area_imovel") or {}).get("row_count") or 0)
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
        tippecanoe = run_tippecanoe(ndjson, pmtiles, tippecanoe_log)
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
            "source_fingerprint_sha256": source_fp,
            "source_row_count_expected": expected_source_rows,
            "source_row_count_actual": extraction["source_row_count_actual"],
            "feature_count": extraction["feature_count"],
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
            "source_fingerprint": source_fp,
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
            "source_fingerprint_sha256": source_fp,
            "source_row_count_expected": expected_source_rows,
            "source_row_count_actual": extraction["source_row_count_actual"],
            "feature_count": extraction["feature_count"],
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
        print(f"RX_V48_NATIONAL_FEATURES={extraction['feature_count']}")
        print(f"RX_V48_NATIONAL_FINGERPRINT={source_fp}")
        print(f"RX_V48_NATIONAL_PMTILES_BYTES={pmtiles_size}")
        print(f"RX_V48_NATIONAL_PMTILES_SHA256={pmtiles_sha}")
        print(f"RX_V48_NATIONAL_BQ_BILLED_BYTES={extraction['total_bytes_billed']}")
        print(f"RX_V48_NATIONAL_MANIFEST_OBJECT=gs://{bucket.name}/{manifest_object}")
        print("RX_V48_NATIONAL_MAP_ANALYSIS_SNAPSHOT_EQUAL=PASS")
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
