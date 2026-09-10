from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHON_CRITICAL = (
    "scripts/v48_geometry_truth_real_gate.py",
    "scripts/v48_national_finalize.py",
    "scripts/v48_national_worker.py",
    "sicar_integrity_v47.py",
)
JS_CRITICAL = ("portal_car_integrity_ui_v47.py",)

NUMERIC_NAMES = r"(?:count|row|rows|area|pct|percent|bytes|size|length|seconds|points|components|geometry)"
PY_NUMERIC_OR = re.compile(rf"(?:int|float)\([^\n]*?\bor\b\s*-?\d+(?:\.\d+)?")
JS_NUMERIC_OR = re.compile(r"(?:count|area|pct|percent|bytes|size|length|seconds|points|components|geometry)[A-Za-z0-9_.?]*\s*\|\|\s*-?\d", re.I)
FORBIDDEN_SNIPPETS = {
    "scripts/v48_geometry_truth_real_gate.py": (
        'geometry_row_count") or -1',
    ),
    "scripts/v48_national_finalize.py": (
        'commit.get("normalization_applied_count") or source.get(',
        'commit.get("no_geometry_car_count") or source.get(',
        'commit.get("no_polygonal_car_count") or source.get(',
        'commit.get("distinct_car_count") or source.get(',
        'commit.get("feature_count") or source.get(',
    ),
    "sicar_integrity_v47.py": (
        "if not property_ha",
        "if property_area and measured is not None",
        "if property_measured and union_area is not None",
        "outside_ha and outside_ha >",
    ),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> None:
    findings: list[str] = []
    for rel in PYTHON_CRITICAL:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for regex, label in ((PY_NUMERIC_OR, "numeric_truthy_fallback"),):
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{rel}:{line}:{label}:{match.group(0)[:120]}")
        for snippet in FORBIDDEN_SNIPPETS.get(rel, ()):
            if snippet in text:
                findings.append(f"{rel}:forbidden_snippet:{snippet}")

    for rel in JS_CRITICAL:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for match in JS_NUMERIC_OR.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{rel}:{line}:js_numeric_truthy_fallback:{match.group(0)[:120]}")

    if findings:
        print("RX_ZERO_NOT_ABSENCE_GATE=FAIL")
        for item in findings:
            print(item)
        fail(f"zero_not_absence_violations:{len(findings)}")

    print("RX_ZERO_NOT_ABSENCE_GATE=PASS")
    print("RX_ZERO_NOT_ABSENCE_SCOPE=geometry_critical_path")


if __name__ == "__main__":
    main()
