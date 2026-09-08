from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v48_es_batch_submit as s


EXPECTED_JOB_ID = "rx-v48-es-sicar-20260804-pilot-002"
EXPECTED_TIPPECANOE_SHA256 = "b0fd9df49b6efc988288ea48774822c6de19eb48428017f27ee0b3b01d44f05d"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def normalized_shell_tokens(script: str) -> set[str]:
    # Ignore shell line-continuation formatting: validate semantic tokens,
    # not the exact whitespace/backslash layout of the runnable.
    normalized = script.replace("\\\n", " ")
    return set(normalized.split())


def main() -> None:
    cfg = s.assert_frozen_contract()
    sha, b64 = s.worker_payload()
    job = s.build_job(sha, b64)

    tg = job["taskGroups"][0]
    spec = tg["taskSpec"]
    policy = job["allocationPolicy"]["instances"][0]["policy"]
    disk = policy["disks"][0]
    script = spec["runnables"][0]["script"]["text"]
    tokens = normalized_shell_tokens(script)

    require(s.JOB_ID == EXPECTED_JOB_ID, "job_id_not_pilot_002")
    require(cfg["pilot_uf"] == "ES", "pilot_uf_not_es")
    require(cfg["snapshot_date"] == "2026-08-04", "snapshot_not_frozen")
    require(
        cfg["analysis_snapshot"] == cfg["map_snapshot"] == "2026-08-04",
        "map_analysis_snapshot_divergence",
    )
    require(cfg["national_generation_allowed"] is False, "national_generation_not_blocked")

    require(tg["taskCount"] == "1" and tg["parallelism"] == "1", "not_single_task")
    require(spec["maxRetryCount"] == 0, "retry_must_be_zero")
    require(spec["maxRunDuration"] == "3600s", "max_run_duration_changed")
    require(policy["machineType"] == "e2-standard-8", "machine_type_changed")
    require(policy["provisioningModel"] == "SPOT", "provisioning_not_spot")
    require(disk["newDisk"]["type"] == "pd-balanced", "disk_type_changed")
    require(disk["newDisk"]["sizeGb"] == "100", "disk_size_changed")
    require(job["allocationPolicy"]["serviceAccount"]["email"] == s.RUNTIME_SA, "runtime_sa_changed")

    require(s.TIPPECANOE_VERSION == "2.79.0", "tippecanoe_version_changed")
    require(s.TIPPECANOE_SHA256 == EXPECTED_TIPPECANOE_SHA256, "tippecanoe_sha_changed")

    require(
        "export PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'" in script,
        "explicit_path_missing",
    )
    require("$SUDO" in tokens, "sudo_token_missing")
    require("apt-get" in tokens, "apt_get_token_missing")
    require("update" in tokens, "apt_update_token_missing")
    require("install" in tokens, "apt_install_token_missing")

    for package in ("build-essential", "gcc", "g++", "coreutils"):
        require(package in tokens, f"required_package_missing:{package}")

    for marker in (
        "RX_V48_BATCH_BOOTSTRAP=FAIL_MISSING_COMMAND:$cmd",
        "RX_V48_BATCH_BOOTSTRAP=FAIL_MISSING_CC1:$CC1_PATH",
        "RX_V48_BATCH_BOOTSTRAP=FAIL_MISSING_CC1PLUS:$CC1PLUS_PATH",
        "RX_V48_BATCH_BOOTSTRAP=TOOLCHAIN_OK",
        "RX_V48_BATCH_BOOTSTRAP_CC1=$CC1_PATH",
        "RX_V48_BATCH_BOOTSTRAP_CC1PLUS=$CC1PLUS_PATH",
        "RX_V48_BATCH_FINAL_METRICS=",
        "RX_V48_BATCH_RUNNABLE=PASS",
    ):
        require(marker in script, f"runnable_marker_missing:{marker}")

    # Syntax-check the exact shell payload that Batch will execute.
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    require(len(b64) > 1000, "worker_payload_too_small")

    print(f"RX_V48_PAID_GATE_WORKER_SHA={sha}")
    print(f"RX_V48_PAID_GATE_JOB_ID={EXPECTED_JOB_ID}")
    print("RX_V48_PAID_GATE_RESOURCES=ONE_SPOT_E2_STANDARD_8_PD_BALANCED_100GB")
    print("RX_V48_PAID_GATE_RETRY=0")
    print("RX_V48_PAID_GATE_NATIONAL=FALSE")
    print("RX_V48_PAID_GATE_RUNNABLE_BASH_N=PASS")
    print("RX_V48_PAID_GATE=ONE_ES_SPOT_ATTEMPT_002_ONLY")


if __name__ == "__main__":
    main()
