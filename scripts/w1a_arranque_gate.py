"""W1a gate: the portal opens without a per-tab reload, never serves an incomplete page,
and serves its JS/CSS as hashed immutable files that rebuild the canonical HTML exactly.

Runs in-process (no browser, no external network). Positive control: on the code
before W1a this gate fails (the ready HTML carries the V26 reload-once-per-tab script,
the app HTML is served while the deferred load is still pending, no hashed assets).

    PYTHONPATH=. python scripts/w1a_arranque_gate.py
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

RELEASE = "V8_OPERATIONAL_ZERO_COST"
if os.environ.get("RX_RELEASE") != RELEASE:  # sitecustomize reads it at interpreter start
    env = dict(os.environ, RX_RELEASE=RELEASE)
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")) if x)
    sys.exit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], env=env))

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []
INFO: dict = {}


def check(ok: bool, msg: str) -> None:
    if not ok:
        FAIL.append(msg)
        print("FAIL", msg, flush=True)


import sitecustomize  # noqa: E402,F401
import portal_api  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

client = TestClient(portal_api.app)


def guard_state() -> dict:
    return getattr(sys.modules.get("portal_boot_guard_v26"), "STATE", None) or {}


def is_app(html: str) -> bool:
    return 'id="map"' in html


# 1. Live boot window: every GET / must be the boot page or the final, complete HTML.
samples = []
deadline = time.time() + 90
while time.time() < deadline:
    r = client.get("/")
    st = dict(guard_state())  # read AFTER the answer: ready never flips back, so "not ready" here held during the request
    samples.append((bool(st.get("ready")), r.headers.get("x-raiox-boot"), r.text))
    if st.get("ready") or st.get("error"):
        break
    time.sleep(0.05)
state = guard_state()
check(bool(state.get("ready")), f"deferred portal load not ready: {state}")
if not state.get("ready"):
    print(json.dumps({"ok": False, "failures": FAIL}), flush=True)
    sys.exit(1)

import portal_v8  # noqa: E402

CANONICAL = portal_v8.PORTAL_HTML
INFO["boot_samples"] = {"total": len(samples), "boot_page": sum(1 for s in samples if not is_app(s[2])),
                        "app_before_ready": sum(1 for s in samples if is_app(s[2]) and not s[0])}

# 2. Ready response: headers and no reload path.
r = client.get("/", headers={"accept-encoding": "gzip"})
html = r.text
check(r.status_code == 200, f"GET / status {r.status_code}")
check(is_app(html), "GET / after ready is not the app")
cc = r.headers.get("cache-control", "")
check("no-store" not in cc and ("no-cache" in cc or "max-age=0" in cc), f"HTML cache-control must revalidate, got {cc!r}")
check(bool(r.headers.get("etag")), "HTML without ETag")
check("rx-v26-ready-reload" not in html, "ready HTML still carries the V26 reload-once-per-tab script")
check("rxBootGuard" not in html, "ready HTML still carries the boot overlay")
check("rxBootGuard" not in CANONICAL, "PORTAL_HTML still injects the boot overlay")
if r.headers.get("etag"):
    r304 = client.get("/", headers={"if-none-match": r.headers["etag"]})
    check(r304.status_code == 304, f"If-None-Match did not give 304 ({r304.status_code})")

# 3. Assets: hashed, immutable, compressed, and together they rebuild the canonical HTML.
refs = re.findall(r'(?:src|href)="(/static/rx/[0-9a-f]{12,}\.(?:js|css))"', html)
check(any(x.endswith(".js") for x in refs) and any(x.endswith(".css") for x in refs), f"no hashed JS/CSS referenced: {refs}")
assets: dict[str, str] = {}
for path in sorted(set(refs)):
    a = client.get(path, headers={"accept-encoding": "identity"})
    check(a.status_code == 200, f"{path} status {a.status_code}")
    cc = a.headers.get("cache-control", "")
    check("immutable" in cc and "max-age=31536000" in cc, f"{path} cache-control {cc!r}")
    digest = hashlib.sha256(a.content).hexdigest()
    check(digest.startswith(path.rsplit("/", 1)[1].split(".")[0]), f"{path} name is not its content hash")
    ctype = a.headers.get("content-type", "")
    check(("javascript" in ctype) if path.endswith(".js") else ("text/css" in ctype), f"{path} content-type {ctype!r}")
    g = client.get(path, headers={"accept-encoding": "gzip"})
    check(g.content == a.content, f"{path} gzip variant differs")
    assets[path] = a.content.decode("utf-8")
for dup in ("rx-v26-ready-reload", "rxBootGuard"):
    check(not any(dup in body for body in assets.values()), f"asset still carries {dup}")


def inline_back(served: str) -> str:
    """Independent inverse of the served page (does not use portal_boot_assets_w1a)."""
    out = served
    m = re.search(r'<link rel="stylesheet" href="(/static/rx/[0-9a-f]+\.css)">(<link rel="preload" as="script" href="/static/rx/[0-9a-f]+\.js">)?', out)
    styles = []
    if m:
        css = assets[m.group(1)]
        styles = css.split("\n", 1)[1].split("\n/*rx-w1a-part*/\n")
        out = out.replace(m.group(0), "", 1)
    out = re.sub(r'<style((?: id="[^"]*")?)>/\*rx-css-(\d+)\*/</style>', lambda x: f"<style{x.group(1)}>{styles[int(x.group(2))]}</style>", out)
    j = re.search(r'<script src="(/static/rx/[0-9a-f]+\.js)"[^>]*></script>', out)
    scripts = []
    if j:
        js = assets[j.group(1)]
        scripts = json.JSONDecoder().raw_decode(js, js.index("var C=") + 6)[0]
        out = out.replace(j.group(0), "", 1)
    out = re.sub(r'<script((?: id="[^"]*")?)>rxW1aRun\((\d+)\)</script>', lambda x: f"<script{x.group(1)}>{scripts[int(x.group(2))]}</script>", out)
    out = re.sub(r"<script>addEventListener\('DOMContentLoaded',function\(\)\{window\.rxPortalBootReady=true\}\)</script>", "", out, count=1)
    out = out.replace("<!--rx-w1a-unpkg-preconnect-->", '<link rel="preconnect" href="https://unpkg.com" crossorigin><link rel="dns-prefetch" href="//unpkg.com">')
    return out.replace("/static/vendor/leaflet-1.9.4/", "https://unpkg.com/leaflet@1.9.4/dist/")


rebuilt = inline_back(html)
check(rebuilt == CANONICAL, "served HTML + assets do not rebuild PORTAL_HTML exactly (content lost, reordered or altered)")
if rebuilt != CANONICAL:
    i = next((k for k, (x, y) in enumerate(zip(rebuilt, CANONICAL)) if x != y), min(len(rebuilt), len(CANONICAL)))
    print("first difference at", i, repr(rebuilt[max(0, i - 80):i + 80]), "vs", repr(CANONICAL[max(0, i - 80):i + 80]))


class Counter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.tags.append((tag, dict(attrs).get("src") or ""))


ref = Counter()
ref.feed(CANONICAL)
served_tags = Counter()
served_tags.feed(html)
inline_js = sum(len(x) for x in re.findall(r"<script(?: id=\"[^\"]*\")?>(.*?)</script>", html, re.S))
inline_css = sum(len(re.sub(r"/\*rx-css-\d+\*/", "", x)) for x in re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
canon_js = sum(len(x) for x in re.findall(r"<script[^>]*>(.*?)</script>", CANONICAL, re.S))
INFO.update(canonical_bytes=len(CANONICAL.encode()), served_bytes=len(html.encode()), inline_js_chars=inline_js,
            inline_css_chars=inline_css, canonical_inline_js_chars=canon_js, parser_tags_canonical=len(ref.tags),
            parser_tags_served=len(served_tags.tags))
check(inline_js <= 3000, f"{inline_js} chars of JS still inline (expected only tiny runners)")
check(inline_css <= 200, f"{inline_css} chars of CSS still inline")
check(len(html.encode()) <= len(CANONICAL.encode()) // 3, f"served HTML {len(html.encode())} B is not much smaller than {len(CANONICAL.encode())} B")
# same number of <script>/<style> start tags + the bundle tag: nothing hidden in comments or strings was split
check(len(served_tags.tags) == len(ref.tags) + 2, f"script/style elements: canonical {len(ref.tags)}, served {len(served_tags.tags)} (+2 expected)")

# 4. Leaflet from our domain, byte-identical to the published 1.9.4.
SRI = {"leaflet.js": "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=", "leaflet.css": "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="}
check("unpkg.com/leaflet" not in html, "served HTML still loads Leaflet from unpkg")
for name, sri in SRI.items():
    lr = client.get("/static/vendor/leaflet-1.9.4/" + name, headers={"accept-encoding": "identity"})
    got = "sha256-" + base64.b64encode(hashlib.sha256(lr.content).digest()).decode()
    check(lr.status_code == 200 and got == sri, f"local {name}: status {lr.status_code} sri {got}")
    check("immutable" in lr.headers.get("cache-control", ""), f"local {name} not immutable")
for img in ("layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"):
    ir = client.get("/static/vendor/leaflet-1.9.4/images/" + img)
    check(ir.status_code == 200 and ir.content[:4] == b"\x89PNG", f"local leaflet image {img}: {ir.status_code}")

# 5. Deterministic boot contract: not ready -> boot page (never the app), failed -> honest boot page.
saved = dict(state)
try:
    state.update(ready=False, error=None)
    b = client.get("/")
    check(not is_app(b.text) and "rxBootGuard" in b.text, "pending state served the app HTML (incomplete page risk)")
    check("no-store" in b.headers.get("cache-control", ""), "boot page must be no-store")
    check(b.headers.get("x-raiox-boot") == "pending", f"boot page header {b.headers.get('x-raiox-boot')!r}")
    state.update(ready=False, error="RuntimeError:gate")
    f = client.get("/")
    check(not is_app(f.text) and b.headers is not None and f.headers.get("x-raiox-boot") == "failed", "failed state served the app HTML")
    check("pronto" not in f.text.lower(), "failed boot page must not claim success")
finally:
    state.clear()
    state.update(saved)
after = client.get("/")
check(is_app(after.text) and after.headers.get("etag") == r.headers.get("etag"), "state restore did not return to the same ready HTML")
INFO["boot_samples_app_before_ready_is_partial"] = [not (s[2] == html) for s in samples if is_app(s[2]) and not s[0]][:3]
check(INFO["boot_samples"]["app_before_ready"] == 0, f"app HTML served {INFO['boot_samples']['app_before_ready']}x before the deferred load was ready")

# 6. Service worker never keeps the boot page as offline shell and caches hashed assets.
sw = client.get("/sw.js").text
check("X-RaioX-Boot" in sw, "service worker may cache the boot page as the offline shell")
check("/static/rx/" in sw, "service worker has no cache-first rule for hashed assets")

# 7. Last resort when the JS file cannot load: the canonical inline page, still flagged ready.
inl = client.get("/?rx-inline=1")
check(inl.headers.get("x-raiox-boot") == "ready-inline" and is_app(inl.text), "inline fallback page missing")
check(inl.text.replace("<script>addEventListener('DOMContentLoaded',function(){window.rxPortalBootReady=true})</script>", "", 1) == CANONICAL, "inline fallback is not the canonical page")
check("location.replace('/?rx-inline=1')" in html, "bundle load failure has no inline fallback")

# 8. HEAD / untouched (health checks), other routes untouched.
check(client.head("/").status_code == 200, "HEAD / changed")
check(client.get("/v1/bootstrap/state").json().get("ready") is True, "bootstrap state endpoint changed")

print(json.dumps({"ok": not FAIL, "failures": FAIL, "info": INFO}, ensure_ascii=False), flush=True)
sys.exit(1 if FAIL else 0)
