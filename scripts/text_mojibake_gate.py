from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".7z", ".woff", ".woff2", ".ttf", ".otf", ".db", ".sqlite",
    ".pbf", ".pmtiles", ".tif", ".tiff", ".npy", ".npz", ".parquet",
}
MOJIBAKE = (
    "\u00c3\u00a3", "\u00c3\u00a7", "\u00c3\u00a9", "\u00c3\u00aa",
    "\u00c3\u00b3", "\u00c3\u00a1", "\u00c3\u00ad", "\u00c3\u00ba",
    "\u00e2\u20ac", "\u00c2\u00b7", "\u00c2\u00a0",
)

# Byte de controle em texto (fora de tab, LF e CR) é gravação quebrada: um "\b" que
# virou backspace dentro de uma regex já passou despercebido (F2, 13/09/2026).
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def control_chars(line: str) -> list[str]:
    return sorted({f"\\x{ord(c):02x}" for c in CONTROL_CHARS.findall(line)})


def tracked_files() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / x.decode("utf-8") for x in raw.split(b"\0") if x]


def main() -> None:
    hits: list[str] = []
    decode_errors: list[str] = []
    checked = 0
    for path in tracked_files():
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{path.relative_to(ROOT)}:{exc.start}")
            continue
        checked += 1
        for line_no, line in enumerate(text.split("\n"), 1):  # splitlines() engoliria \x0b, \x0c e \x1c-\x1e
            found = [m.encode("unicode_escape").decode("ascii") for m in MOJIBAKE if m in line]
            found += control_chars(line)
            if found:
                hits.append(f"{path.relative_to(ROOT)}:{line_no}:{','.join(found)}")
    assert not decode_errors, "non_utf8_text_files=" + " | ".join(decode_errors[:20])
    assert not hits, "mojibake_detected=" + " | ".join(hits[:50])
    print(f"TEXT_MOJIBAKE_GATE=PASS checked_text_files={checked}")


if __name__ == "__main__":
    main()
