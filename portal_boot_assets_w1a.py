"""W1a - portal opens without the per-tab reload and serves its JS/CSS as hashed, immutable files.

Why the V26 boot guard reloaded every new tab
---------------------------------------------
``sitecustomize`` loads ~50 portal modules in a background thread *after* uvicorn
is already accepting requests. Each module patches ``portal_v8.PORTAL_HTML`` by
string replacement, so a ``GET /`` answered during that window returns an
incomplete page (base map without V43..V49). V26 put an overlay plus a script in
the HTML that, once ``/v1/bootstrap/state`` said ready, reloaded the tab one time
per ``sessionStorage`` - but the page cannot tell whether *its own* HTML was the
incomplete one, so it reloaded every new tab forever, costing 3-7 s in production.

What this module does instead
-----------------------------
The server knows whether it is ready when it answers. An outermost ASGI
middleware (installed by ``portal_api`` before the app starts):

* while the deferred load is pending or failed: ``GET /`` gets a small boot page
  (``#rxBootGuard``, ``no-store``) that polls and reloads *only then*; the
  incomplete app HTML is never sent;
* when ready: the HTML produced by the ``/`` route (after every string patch) is
  assembled once per content hash - ``<style>`` blocks (no attributes or only an
  ``id``) become one hashed CSS file, such ``<script>`` blocks one hashed JS file
  whose parts run at their original positions (``rxW1aRun(i)``), each as its own
  script (same order, same global scope, same error isolation) - and served with
  ``Cache-Control: no-cache`` + ETag; the assets with a one-year ``immutable``;
* Leaflet 1.9.4 is served from ``/static/vendor/leaflet-1.9.4/`` (vendored,
  SRI-checked) instead of unpkg.

Anchors are untouched: ``PORTAL_HTML`` keeps being the canonical inline string
every module and gate patches or inspects; the split happens on the way out and
is verified by rebuilding the canonical string from the pieces. If the rebuild
differs, the canonical inline HTML is served (never a broken page); the same page
is the last resort when the CSS or JS file cannot be loaded (``/?rx-inline=1``). A ``<style>``
with a relative ``url()`` stays inline (in a file it would resolve under ``/static/rx/``).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSET_PREFIX = "/static/rx/"
VENDOR_PREFIX = "/static/vendor/leaflet-1.9.4/"
VENDOR_DIR = ROOT / "static" / "vendor" / "leaflet-1.9.4"
IMMUTABLE = "public, max-age=31536000, immutable"
HTML_CACHE = "no-cache"
READY_FLAG = "rxPortalBootReady"
# Published by leafletjs.com for 1.9.4; checked at load, a mismatch disables the local copy.
LEAFLET_SRI = {
    "leaflet.js": "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=",
    "leaflet.css": "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=",
}
LEAFLET_CDN = "https://unpkg.com/leaflet@1.9.4/dist/"
UNPKG_PRECONNECT = '<link rel="preconnect" href="https://unpkg.com" crossorigin><link rel="dns-prefetch" href="//unpkg.com">'

_TAG = re.compile(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", re.I | re.S)
_TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png"}

ASSETS: dict[str, tuple[bytes, bytes, str]] = {}  # path -> (raw, gzip, content-type)
_GENERATIONS: dict[str, dict] = {}  # sha256(route html) -> generation
STATS = {"generations": 0, "fallbacks": 0, "last_error": None}


# ---------------------------------------------------------------- boot state
def boot_state() -> str:
    """'ready' | 'pending' | 'failed' | 'direct' (no deferred load in this process)."""
    guard = sys.modules.get("portal_boot_guard_v26")
    state = getattr(guard, "STATE", None)
    if isinstance(state, dict):
        if state.get("ready"):
            return "ready"
        return "failed" if state.get("error") else "pending"
    site = sys.modules.get("sitecustomize")
    return "pending" if getattr(site, "PORTAL_DEFERRED_BOOT", False) else "direct"


BOOT_PAGE = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Raio-X Territorial</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#06110d}
#rxBootGuard{position:fixed;inset:0;z-index:99999;background:#06110d;color:#eef8f2;display:flex;align-items:center;justify-content:center;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}
#rxBootGuard .box{width:min(430px,calc(100vw - 34px));padding:28px;border:1px solid #244136;border-radius:20px;background:#0b1b14;box-shadow:0 30px 90px #000a;text-align:center}
#rxBootGuard .mark{width:52px;height:52px;border-radius:16px;background:#63e6a5;color:#052116;font-weight:950;display:grid;place-items:center;margin:0 auto 16px;font-size:18px}
#rxBootGuard h2{font-size:18px;margin:0 0 8px}#rxBootGuard p{color:#9bb1a6;font-size:13px;line-height:1.55;margin:0 0 18px}
#rxBootGuard .bar{height:6px;border-radius:999px;background:#173026;overflow:hidden}#rxBootGuard .bar i{display:block;height:100%;width:38%;background:#63e6a5;border-radius:999px;animation:rxboot 1s ease-in-out infinite alternate}@keyframes rxboot{to{transform:translateX(165%)}}
#rxBootGuard button{display:none;margin:16px auto 0;min-height:44px;min-width:44px;border:0;border-radius:11px;background:#63e6a5;color:#052116;padding:10px 16px;font:inherit;font-weight:900;font-size:14px;cursor:pointer}
body[data-rx-boot=failed] #rxBootGuard button,body[data-rx-boot=slow] #rxBootGuard button{display:block}
body[data-rx-boot=failed] #rxBootGuard .bar{display:none}
</style></head><body data-rx-boot="__STATE__">
<div id="rxBootGuard"><div class="box"><div class="mark">RX</div><h2 id="rxBootTitle">__TITLE__</h2><p id="rxBootText" aria-live="polite">__TEXT__</p><div class="bar"><i></i></div><button id="rxBootRetry" type="button" onclick="location.reload()">Tentar de novo</button></div></div>
<script>
(function(){
  // Mirrors the server: pending (loading, reloads by itself when ready), failed (nothing is retried
  // on the server, so no promise; the button reloads), slow (pending for too long).
  var S=__COPY__,K='rx-w1a-boot-reloads',tries=0,shown=document.body.getAttribute('data-rx-boot')||'pending';
  function render(kind){
    if(kind===shown||!S[kind])return;shown=kind;
    document.body.setAttribute('data-rx-boot',kind);
    var h=document.getElementById('rxBootTitle'),p=document.getElementById('rxBootText');
    if(h)h.textContent=S[kind].title;if(p)p.textContent=S[kind].text;
  }
  function recent(){try{var now=Date.now(),a=JSON.parse(sessionStorage.getItem(K)||'[]').filter(function(t){return now-t<120000});return a}catch(e){return []}}
  function reloadWhenReady(){
    var a=recent();
    if(a.length>=5){render('slow');return false}
    try{a.push(Date.now());sessionStorage.setItem(K,JSON.stringify(a))}catch(e){}
    location.reload();return true;
  }
  async function tick(){
    tries++;
    try{
      var r=await fetch('/v1/bootstrap/state',{cache:'no-store'});
      if(r.ok){
        var d=await r.json();
        if(d.ready){if(reloadWhenReady())return}
        else if(d.error){render('failed')}
        else if(shown==='failed'){render('pending');tries=0}
      }
    }catch(e){}
    if(shown==='pending'&&tries>45)render('slow');
    setTimeout(tick,shown==='failed'?10000:(tries<10?500:1000));
  }
  tick();
})();
</script>
</body></html>'''

# What the boot page says. "failed" is final for this process (sitecustomize does not retry the
# deferred load), so it promises nothing and names no internals; the button reloads the tab.
BOOT_COPY = {
    "pending": {"title": "Inicializando o Raio-X Territorial", "text": "Carregando o mapa e as consultas. A página abre sozinha assim que estiver pronta."},
    "slow": {"title": "Inicializando o Raio-X Territorial", "text": "A abertura está demorando mais que o normal. Você pode tentar de novo."},
    "failed": {"title": "Raio-X Territorial", "text": "Não foi possível abrir o Raio-X agora. Tente de novo em alguns minutos."},
}


def boot_page(state: str) -> bytes:
    kind = state if state in BOOT_COPY else "pending"
    copy = json.dumps(BOOT_COPY, ensure_ascii=False).replace("</", "<\\/")
    return (BOOT_PAGE.replace("__STATE__", kind).replace("__TITLE__", BOOT_COPY[kind]["title"])
            .replace("__TEXT__", BOOT_COPY[kind]["text"]).replace("__COPY__", copy).encode("utf-8"))


# ------------------------------------------------------------ vendored Leaflet
def _sri(data: bytes) -> str:
    import base64

    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _load_vendor() -> bool:
    try:
        files = {p.name: p for p in VENDOR_DIR.iterdir() if p.is_file()}
        files.update({"images/" + p.name: p for p in (VENDOR_DIR / "images").iterdir() if p.is_file()})
        loaded = {}
        for name, path in files.items():
            if path.suffix not in _TYPES:
                continue
            data = path.read_bytes()
            if name in LEAFLET_SRI and _sri(data) != LEAFLET_SRI[name]:
                raise ValueError(f"leaflet_sri_mismatch:{name}")
            loaded[VENDOR_PREFIX + name] = data
        if not all((VENDOR_PREFIX + n) in loaded for n in LEAFLET_SRI):
            raise FileNotFoundError("leaflet_vendor_incomplete")
    except Exception as exc:  # keep unpkg if the vendored copy is missing or altered
        STATS["last_error"] = f"vendor:{type(exc).__name__}:{str(exc)[:160]}"
        print("RX_W1A_LEAFLET_LOCAL=off:" + STATS["last_error"], flush=True)
        return False
    for path, data in loaded.items():
        _put_asset(path, data)
    return True


def _put_asset(path: str, data: bytes) -> None:
    ctype = _TYPES[Path(path).suffix]
    gz = gzip.compress(data, 6, mtime=0) if not path.endswith(".png") else b""
    ASSETS[path] = (data, gz, ctype)


LEAFLET_LOCAL = _load_vendor()


# ------------------------------------------------------------------- assembly
_ID_ONLY = re.compile(r' id="[A-Za-z][A-Za-z0-9_-]*"')
_STYLESHEET_LINK = re.compile(r"<link\b[^>]*rel=[\"']?stylesheet", re.I)


_CSS_URL = re.compile(r"""url\(\s*['"]?\s*([^'")\s]*)""", re.I)
_CSS_ABSOLUTE = re.compile(r"(?:data:|/|https?:|#)", re.I)


def css_relative_refs(css: str) -> list[str]:
    """References that resolve against the stylesheet URL: inline they meant '/', in /static/rx/ they would not."""
    refs = [m.group(1) or "url()" for m in _CSS_URL.finditer(css) if not _CSS_ABSOLUTE.match(m.group(1))]
    if re.search(r"image-set\(", css, re.I):
        refs.append("image-set(")  # strings inside it are URLs too; not parsed, kept inline
    return refs


def _extractable(kind: str, attrs: str, body: str) -> bool:
    if attrs and not _ID_ONLY.fullmatch(attrs):
        return False  # src/type/media/...: left exactly as written (the id alone stays on the placeholder)
    if kind == "script" and "<!--" in body:
        return False  # HTML "script data escaped" state: keep inline, parsing is not trivial
    if kind == "style" and (re.search(r"@import|@charset", body, re.I) or css_relative_refs(body)):
        return False  # relative url()/image-set would point into /static/rx/ once moved to a file
    return True


def split_html(html: str) -> dict:
    """Canonical HTML -> {'html', 'css', 'js'} (pieces only; verification in assemble())."""
    scripts: list[str] = []
    styles: list[str] = []
    out: list[str] = []
    pos = 0
    first_css_at = None
    for m in _TAG.finditer(html):
        kind, attrs, body = m.group(1).lower(), m.group(2), m.group(3)
        gap = html[pos:m.start()]
        out.append(gap)
        pos = m.end()
        if first_css_at is not None and _STYLESHEET_LINK.search(gap):
            raise ValueError("stylesheet_link_between_styles")  # the single CSS file would change cascade order
        if not _extractable(kind, attrs, body) or m.group(1) != kind or m.group(0)[-len(kind) - 3:] != f"</{kind}>":
            if kind == "style" and first_css_at is not None:
                raise ValueError("inline_style_between_styles")
            out.append(m.group(0))
            continue
        if kind == "style":
            if first_css_at is None:
                first_css_at = len(out)
                out.append("")  # slot for the stylesheet link
            out.append(f"<style{attrs}>/*rx-css-{len(styles)}*/</style>")
            styles.append(body)
        else:
            out.append(f"<script{attrs}>rxW1aRun({len(scripts)})</script>")
            scripts.append(body)
    out.append(html[pos:])
    return {"parts": out, "first_css_at": first_css_at, "styles": styles, "scripts": scripts}


def _js_bundle(scripts: list[str]) -> bytes:
    chunks = json.dumps(scripts, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    code = (
        "/* Raio-X W1a: page scripts in document order; each runs at its original position as its own script. */\n"
        "(function(){var C=" + chunks + ";\n"
        "window.rxW1aRun=function(i){var cur=document.currentScript,s=document.createElement('script');"
        "s.text=C[i]+'\\n//# sourceURL=rx-part-'+i+'.js';C[i]=null;window.rxW1aRan=(window.rxW1aRan||0)+1;"
        "if(cur&&cur.parentNode)cur.parentNode.insertBefore(s,cur.nextSibling);else(document.body||document.head).appendChild(s)};})();\n"
    )
    return code.encode("utf-8")


def rebuild(served: str, assets: dict[str, bytes]) -> str:
    """Inverse of assemble(): the canonical HTML back from the served HTML + assets."""
    html = served
    m = re.search(r'<link rel="stylesheet" href="(/static/rx/[0-9a-f]+\.css)" onerror="[^"]*"><link rel="preload" as="script" href="/static/rx/[0-9a-f]+\.js">', html)
    styles: list[str] = []
    if m:
        styles = _css_parts(assets[m.group(1)].decode("utf-8"))
        html = html.replace(m.group(0), "", 1)
    html = re.sub(r"<style((?: id=\"[^\"]*\")?)>/\*rx-css-(\d+)\*/</style>", lambda x: "<style" + x.group(1) + ">" + styles[int(x.group(2))] + "</style>", html)
    j = re.search(r'<script src="(/static/rx/[0-9a-f]+\.js)" onerror="[^"]*"></script>', html)
    scripts: list[str] = []
    if j:
        text = assets[j.group(1)].decode("utf-8")
        scripts = json.loads(text[text.index("var C=") + 6:text.index(";\nwindow.rxW1aRun")])
        html = html.replace(j.group(0), "", 1)
    html = re.sub(r"<script((?: id=\"[^\"]*\")?)>rxW1aRun\((\d+)\)</script>", lambda x: "<script" + x.group(1) + ">" + scripts[int(x.group(2))] + "</script>", html)
    html = html.replace(_READY_SCRIPT, "", 1)
    html = html.replace("<!--rx-w1a-unpkg-preconnect-->", UNPKG_PRECONNECT)
    return html.replace(VENDOR_PREFIX, LEAFLET_CDN)


_CSS_SEP = "\n/*rx-w1a-part*/\n"
_READY_SCRIPT = f"<script>addEventListener('DOMContentLoaded',function(){{window.{READY_FLAG}=true}})</script>"
# CSS or JS file did not load (network drop, stale offline shell, instance swap during a deploy):
# reload once, then the inline page. One attempt per page even when both files fail; a tab that
# already reloaded in the last 60 s goes straight to the inline page (no reload loop). The inline
# URL keeps the page's own query and hash (a shared /?car= link still opens its property).
_ASSET_RETRY = ("if(window.__rxW1aRetry)return;window.__rxW1aRetry=1;"
                "var q=new URLSearchParams(location.search);q.set('rx-inline','1');var u='/?'+q+location.hash;"
                "try{var k='rx-w1a-asset-retry',t=+sessionStorage.getItem(k)||0;"
                "if(Date.now()-t<60000){location.replace(u)}else{sessionStorage.setItem(k,Date.now());location.reload()}}"
                "catch(e){location.replace(u)}")


def _css_parts(css: str) -> list[str]:
    body = css.split("\n", 1)[1] if css.startswith("/*") else css
    return body.split(_CSS_SEP)


def assemble(html: str) -> dict:
    """Canonical route HTML -> generation {html, etag, assets}; raises if it cannot be verified."""
    if "/*rx-css-" in html or "rxW1aRun(" in html or _CSS_SEP in html:
        raise ValueError("marker_collision")
    pieces = split_html(html)
    parts, styles, scripts = pieces["parts"], pieces["styles"], pieces["scripts"]
    if any(_CSS_SEP in s for s in styles):
        raise ValueError("css_separator_collision")
    new_assets: dict[str, bytes] = {}
    js_path = None
    if scripts:
        js = _js_bundle(scripts)
        js_path = f"{ASSET_PREFIX}{hashlib.sha256(js).hexdigest()[:20]}.js"
        new_assets[js_path] = js
    if styles:
        css = ("/* Raio-X W1a: page styles in document order */\n" + _CSS_SEP.join(styles)).encode("utf-8")
        css_path = f"{ASSET_PREFIX}{hashlib.sha256(css).hexdigest()[:20]}.css"
        new_assets[css_path] = css
        preload = f'<link rel="preload" as="script" href="{js_path}">' if js_path else ""
        if not preload:
            raise ValueError("styles_without_scripts_unsupported")
        parts[pieces["first_css_at"]] = f'<link rel="stylesheet" href="{css_path}" onerror="{_ASSET_RETRY}">{preload}'
    out = "".join(parts)
    if js_path:
        first = re.search(r"<script(?: id=\"[^\"]*\")?>rxW1aRun\(0\)</script>", out).start()
        out = out[:first] + f'<script src="{js_path}" onerror="{_ASSET_RETRY}"></script>' + out[first:]
    end = out.rfind("</body>")
    if end < 0:
        raise ValueError("no_body_end")
    out = out[:end] + _READY_SCRIPT + out[end:]
    if LEAFLET_LOCAL:
        if VENDOR_PREFIX in html or "<!--rx-w1a-unpkg-preconnect-->" in html:
            raise ValueError("marker_collision")
        out = out.replace(UNPKG_PRECONNECT, "<!--rx-w1a-unpkg-preconnect-->").replace(LEAFLET_CDN, VENDOR_PREFIX)
    elif UNPKG_PRECONNECT in html:
        out = out.replace(UNPKG_PRECONNECT, "<!--rx-w1a-unpkg-preconnect-->")
    if rebuild(out, new_assets) != html:
        raise ValueError("rebuild_mismatch")
    body = out.encode("utf-8")
    return {
        "html": body,
        "html_gz": gzip.compress(body, 6, mtime=0),
        "etag": '"rx-' + hashlib.sha256(body).hexdigest()[:24] + '"',
        "assets": new_assets,
    }


def generation_for(route_html: bytes) -> dict:
    key = hashlib.sha256(route_html).hexdigest()
    gen = _GENERATIONS.get(key)
    if gen is not None:
        return gen
    try:
        gen = assemble(route_html.decode("utf-8"))
    except Exception as exc:
        STATS["fallbacks"] += 1
        STATS["last_error"] = f"assemble:{type(exc).__name__}:{str(exc)[:200]}"
        print("RX_W1A_ASSETS=fallback_inline:" + STATS["last_error"], flush=True)
        gen = {"fallback": True}
    else:
        for path, data in gen["assets"].items():
            _put_asset(path, data)
        STATS["generations"] += 1
        print(f"RX_W1A_ASSETS=ready html:{len(gen['html'])} assets:{len(gen['assets'])} leaflet_local:{LEAFLET_LOCAL}", flush=True)
    if len(_GENERATIONS) >= 4:  # HTML changes only on restart; keep memory bounded if something patches at runtime
        _GENERATIONS.pop(next(iter(_GENERATIONS)))
    _GENERATIONS[key] = gen
    return gen


# ----------------------------------------------------------------- middleware
def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v.decode("latin-1")
    return ""


async def _send(send, status: int, headers: list[tuple[bytes, bytes]], body: bytes, head_only: bool = False):
    headers = [h for h in headers if h[0] != b"content-length"] + [(b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head_only else body})


class FastBootMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if path.startswith("/static/"):
            return await self._asset(scope, receive, send, path)
        if path == "/" and scope.get("method") == "GET":
            state = boot_state()
            if state in ("pending", "failed"):
                return await _send(send, 200, [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"x-raiox-boot", state.encode()),
                ], boot_page(state))
            return await self._portal(scope, receive, send)
        return await self.app(scope, receive, send)

    async def _asset(self, scope, receive, send, path):
        item = ASSETS.get(path)
        if item is None:
            return await self.app(scope, receive, send)
        raw, gz, ctype = item
        headers = [(b"content-type", ctype.encode()), (b"cache-control", IMMUTABLE.encode())]
        body = raw
        if gz:
            headers.append((b"vary", b"accept-encoding"))
            if "gzip" in _header(scope, b"accept-encoding"):
                headers.append((b"content-encoding", b"gzip"))
                body = gz
        return await _send(send, 200, headers, body, scope.get("method") == "HEAD")

    async def _portal(self, scope, receive, send):
        start: dict = {}
        chunks: list[bytes] = []

        async def capture(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, capture)
        body = b"".join(chunks)
        headers = list(start.get("headers") or [])
        ctype = dict(headers).get(b"content-type", b"")
        if start.get("status") != 200 or not ctype.startswith(b"text/html"):
            return await _send(send, start.get("status", 500), headers, body)
        inline = b"rx-inline=1" in (scope.get("query_string") or b"")
        gen = None if inline else generation_for(body)
        if gen is None or gen.get("fallback"):
            # canonical inline page (asset load failed twice, or the split could not be verified)
            end = body.rfind(b"</body>")
            body = body[:end] + _READY_SCRIPT.encode() + body[end:] if end >= 0 else body
            return await _send(send, 200, [h for h in headers if h[0] != b"x-raiox-boot"] + [(b"x-raiox-boot", b"ready-inline")], body)
        keep = [h for h in headers if h[0] not in (b"cache-control", b"pragma", b"etag", b"content-length", b"content-encoding")]
        keep += [(b"cache-control", HTML_CACHE.encode()), (b"etag", gen["etag"].encode()), (b"x-raiox-boot", b"ready"), (b"vary", b"accept-encoding")]
        if gen["etag"] in _header(scope, b"if-none-match"):
            return await _send(send, 304, [h for h in keep if h[0] != b"content-type"], b"")
        out = gen["html"]
        if "gzip" in _header(scope, b"accept-encoding"):
            keep.append((b"content-encoding", b"gzip"))
            out = gen["html_gz"]
        return await _send(send, 200, keep, out)


def install(app) -> None:
    if getattr(app.state, "rx_w1a_fast_boot", False):
        return
    app.add_middleware(FastBootMiddleware)
    app.state.rx_w1a_fast_boot = True
    print(f"RX_W1A_FAST_BOOT=installed leaflet_local:{LEAFLET_LOCAL}", flush=True)
