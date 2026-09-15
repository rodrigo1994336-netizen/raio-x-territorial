"""F1B: the report engine never hands out the previous consultation as the current one.

Runs the real engine modules (report_api + report_v9_patch + report_quick_v22) in-process with the
analysis replaced by a counter (no network) and a 1 s engine cache, and crosses the cache expiry:

* quick?deep=1 after the cache expired: deep_state is NOT the previous run's "ready" (it is "running");
* /progressive/status after the cache expired, with no quick call: NOT the previous "ready";
* every "ready" carries completed_at = the time the data was produced (the cache stamp), and a cache hit
  (quick-cache) carries the same time, not the time of the answer.

Positive controls: each defect is put back (no "running" mark before scheduling the run, a "ready" with no
age limit, completed_at taken at answer time) and its rule must fail for its own reason.

Run: PYTHONPATH=. RX_RELEASE=OFF python scripts/f1b_motor_estado_check.py   (called by scripts/f1b_tela_gate.py)
Last line: F1B_MOTOR_ESTADO=PASS | FAIL [...]
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["RX_CACHE_TTL_SECONDS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import report_api as base  # noqa: E402
import report_quick_v22 as quick  # noqa: E402
import report_v9_patch as prog  # noqa: E402

CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        FAILURES.append(label)


async def observe() -> dict:
    calls = {"n": 0}

    async def fake_uncached(code):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return {"car": {"ok": True, "properties": {"cod_imovel": code}}, "marker": f"run{calls['n']}"}

    saved = (base._analyze_uncached, base._report_summary, quick.fetch_car_live_resilient)
    base._analyze_uncached = fake_uncached
    base._report_summary = lambda r: {"marker": r.get("marker"), "car": r.get("car")}
    quick.fetch_car_live_resilient = lambda code: {"ok": True, "properties": {"cod_imovel": code}}
    base._CACHE.clear(); prog._PROGRESS.clear(); prog._PROGRESS_TASKS.clear(); prog._QUICK.clear(); prog._PRODUCED.clear()
    obs: dict = {}
    try:
        await quick.quick_analysis_v24(CAR, deep=True)
        await asyncio.sleep(0.4)
        st1 = await prog.progressive_status(CAR)
        obs["run1"] = {"state": st1.get("state"), "marker": (st1.get("analysis") or {}).get("marker"), "completed_at": st1.get("completed_at")}
        produced = base._CACHE[CAR][0] if CAR in base._CACHE else None
        await asyncio.sleep(0.3)
        hit = await quick.quick_analysis_v24(CAR, deep=True)  # still inside the 1 s cache
        prog_at = ((hit.get("analysis") or {}).get("progressive") or {}).get("completed_at")
        obs["cache_hit"] = {"mode": hit.get("mode"), "completed_at": prog_at, "deep_completed_at": (hit.get("deep_state") or {}).get("completed_at"),
                            "age_s": None if not prog_at or produced is None else round(time.time() - datetime.fromisoformat(prog_at).timestamp(), 2),
                            "produced_age_s": None if produced is None else round(time.monotonic() - produced, 2)}
        # cross the cache expiry; the quick layer's own 120 s memo is not the engine cache
        await asyncio.sleep(0.8)
        prog._QUICK.clear()
        r2 = await quick.quick_analysis_v24(CAR, deep=True)
        ds = r2.get("deep_state") or {}
        obs["after_expiry_quick"] = {"mode": r2.get("mode"), "state": ds.get("state"), "marker": (ds.get("analysis") or {}).get("marker")}
        await asyncio.sleep(0.4)
        st2 = await prog.progressive_status(CAR)
        obs["run2"] = {"state": st2.get("state"), "marker": (st2.get("analysis") or {}).get("marker")}
        # status alone after the expiry (nobody called quick): the old "ready" must not come back as current
        await asyncio.sleep(1.2)
        st3 = await prog.progressive_status(CAR)
        obs["after_expiry_status"] = {"state": st3.get("state"), "marker": (st3.get("analysis") or {}).get("marker")}
        await asyncio.sleep(0.4)
        st4 = await prog.progressive_status(CAR)
        obs["run3"] = {"state": st4.get("state"), "marker": (st4.get("analysis") or {}).get("marker")}
        obs["public_keys"] = sorted(st4)
    finally:
        base._analyze_uncached, base._report_summary, quick.fetch_car_live_resilient = saved
        for t in list(prog._PROGRESS_TASKS.values()):
            t.cancel()
    return obs


def judge(o: dict) -> list[str]:
    p = []
    if o["run1"]["state"] != "ready" or o["run1"]["marker"] != "run1":
        p.append(f"first run not ready: {o['run1']}")
    try:
        t1 = datetime.fromisoformat(o["run1"]["completed_at"]).timestamp()
        if not (time.time() - 30 < t1 <= time.time() + 1):
            p.append(f"completed_at is not the production time: {o['run1']['completed_at']}")
    except Exception:
        p.append(f"ready without completed_at: {o['run1']}")
    ch = o["cache_hit"]
    if ch["mode"] != "quick-cache" or ch["age_s"] is None or ch["produced_age_s"] is None or abs(ch["age_s"] - ch["produced_age_s"]) > 0.3 or ch["age_s"] < 0.3:
        p.append(f"cache hit carries the answer time, not the production time: {ch}")
    q = o["after_expiry_quick"]
    if q["state"] == "ready" or q["marker"] == "run1":
        p.append(f"quick after the cache expiry handed out the previous run as ready: {q}")
    if o["run2"]["marker"] != "run2":
        p.append(f"new run not produced: {o['run2']}")
    s = o["after_expiry_status"]
    if s["state"] == "ready" and s["marker"] == "run2":
        p.append(f"status after the cache expiry handed out the previous run as ready: {s}")
    if o["run3"]["marker"] != "run3":
        p.append(f"status did not start a new run for an expired ready: {o['run3']}")
    if "produced_mono" in o.get("public_keys", []) or any("mono" in k for k in o.get("public_keys", [])):
        p.append("internal clock exposed in the public state")
    return p


def mutant_ensure_deep():
    src = inspect.getsource(prog._ensure_deep)
    old = "    _PROGRESS[code]={'state':'running','stage':'queued','started_at':time.monotonic()}\n"
    assert src.count(old) == 1, "positive control anchor missing: running mark"
    ns: dict = {}
    exec(compile(src.replace(old, ""), "<mutant _ensure_deep>", "exec"), prog.__dict__, ns)
    return ns["_ensure_deep"]


def main() -> int:
    obs = asyncio.run(observe())
    probs = judge(obs)
    check(not probs, f"engine state crosses the cache expiry without old data as current {probs if probs else ''}".rstrip())
    print("F1B_MOTOR_EVIDENCE=" + json.dumps(obs, ensure_ascii=False)[:900], flush=True)

    def control(label, patch, reason):
        saved = {k: getattr(prog, k) for k in patch}
        for k, v in patch.items():
            setattr(prog, k, v)
        try:
            got = judge(asyncio.run(observe()))
        finally:
            for k, v in saved.items():
                setattr(prog, k, v)
        check(any(reason in x for x in got), f"positive control catches: {label} (for its reason: {reason!r}; got {got[:2]})")

    control("no running mark before the run is scheduled", {"_ensure_deep": mutant_ensure_deep()}, "quick after the cache expiry handed out the previous run")
    control("ready without age limit", {"_stale_ready": lambda code, state: False}, "status after the cache expiry handed out the previous run")
    control("completed_at taken at answer time", {"completed_at": lambda code: prog._iso_utc(time.monotonic())}, "cache hit carries the answer time")
    print("F1B_MOTOR_ESTADO=" + ("PASS" if not FAILURES else "FAIL " + json.dumps(FAILURES, ensure_ascii=False)), flush=True)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
