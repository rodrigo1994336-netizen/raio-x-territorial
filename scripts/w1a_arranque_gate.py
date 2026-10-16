"""W1a gate: the portal opens without a per-tab reload, never serves an incomplete page,
and serves its JS/CSS as hashed immutable files that rebuild the canonical HTML exactly.

Runs in-process (no browser, no external network; Node runs the two JS rules). Positive
control: on the code before W1a this gate fails (the ready HTML carries the V26
reload-once-per-tab script, the app HTML is served while the deferred load is still
pending, no hashed assets). Each rule added after the adversarial review also proves
itself on every run against a known-bad input before judging the real one:

* a hashed CSS or JS file that fails to load -> one reload per page, then the inline page
  (the served onerror handler is executed in Node; the pre-review handler must fail);
* no relative url()/image-set in a style moved to /static/rx/ (checker self-test, and a
  synthetic page with the module guard switched off must be caught);
* the failed boot page promises nothing and offers "Tentar de novo" (the pre-review copy
  must be flagged);
* an old service worker's boot-page shell is never served offline after the upgrade
  (the real /sw.js runs in Node against mocked caches; the same file with the pre-W1a
  shell cache name must serve the boot page).

    PYTHONPATH=. python scripts/w1a_arranque_gate.py
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    m = re.search(r'<link rel="stylesheet" href="(/static/rx/[0-9a-f]+\.css)"(?: onerror="[^"]*")?>(<link rel="preload" as="script" href="/static/rx/[0-9a-f]+\.js">)?', out)
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
FAILED_PAGE = ""
try:
    state.update(ready=False, error=None)
    b = client.get("/")
    check(not is_app(b.text) and "rxBootGuard" in b.text, "pending state served the app HTML (incomplete page risk)")
    check("no-store" in b.headers.get("cache-control", ""), "boot page must be no-store")
    check(b.headers.get("x-raiox-boot") == "pending", f"boot page header {b.headers.get('x-raiox-boot')!r}")
    state.update(ready=False, error="RuntimeError:gate")
    f = client.get("/")
    FAILED_PAGE = f.text
    check(not is_app(f.text) and b.headers is not None and f.headers.get("x-raiox-boot") == "failed", "failed state served the app HTML")
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
check("rx-inline" in html and "location.replace(" in html, "bundle load failure has no inline fallback")

# 8. HEAD / untouched (health checks), other routes untouched.
check(client.head("/").status_code == 200, "HEAD / changed")
check(client.get("/v1/bootstrap/state").json().get("ready") is True, "bootstrap state endpoint changed")


# ------------------------------------------------------------------------------------------
# Rules from the adversarial review. Every rule first proves, on this run, that its checker
# rejects a known-bad input (positive control); a checker that cannot fail is reported as FAIL.
NODE = shutil.which("node")
check(bool(NODE), "node not found: the retry and service-worker rules cannot run (CI installs Node before this step)")
TMP = Path(tempfile.mkdtemp(prefix="rx-w1a-gate-"))


def run_node(js: str, *args: str) -> dict:
    script = TMP / f"sim_{abs(hash(js))}.js"
    script.write_text(js, encoding="utf-8")
    p = subprocess.run([NODE, str(script), *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout)[-600:]}
    return json.loads(p.stdout.strip().splitlines()[-1])


class Attrs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


# 9. A hashed CSS or JS file that fails -> reload once per page, then the inline page, never a loop.
RETRY_SIM = r"""
const handlers=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));
const store=new Map();
function page(now,search='',hash=''){const calls=[],win={};
  const env=[win,{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v))},
    {search,hash,reload:()=>calls.push('reload'),replace:u=>calls.push('replace:'+u)},{now:()=>now}];
  return {fire:code=>new Function('window','sessionStorage','location','Date',code)(...env),calls}}
const css=handlers.css||'',js=handlers.js||'';
const first=page(1000000);if(css)first.fire(css);if(js)first.fire(js);        // both files fail
const again=page(1003000);if(css)again.fire(css);if(js)again.fire(js);        // reloaded page fails again
const cssOnly=page(1603000);if(css)cssOnly.fire(css);                          // 10 min later, CSS alone fails
const link=page(1605000,'?car=MG-1','#16/-18/-44');if(js)link.fire(js);         // shared link page fails right after
console.log(JSON.stringify({first:first.calls,again:again.calls,css_only_later:cssOnly.calls,shared_link:link.calls}));
"""
INLINE = "replace:/?rx-inline=1"


def retry_problems(handlers: dict) -> list[str]:
    if not handlers.get("css") or not handlers.get("js"):
        return [f"hashed file without onerror recovery: css={bool(handlers.get('css'))} js={bool(handlers.get('js'))}"]
    src = TMP / "handlers.json"
    src.write_text(json.dumps(handlers), encoding="utf-8")
    r = run_node(RETRY_SIM, str(src))
    if "error" in r:
        return [f"retry handler did not run: {r['error']}"]
    out = []
    if r["first"] != ["reload"]:
        out.append(f"CSS+JS failing on a fresh page must reload exactly once, got {r['first']}")
    if r["again"] != [INLINE]:
        out.append(f"failing again right after the reload must open the inline page once, got {r['again']}")
    if r["css_only_later"] != ["reload"]:
        out.append(f"a CSS failure minutes later must reload first (no sticky inline), got {r['css_only_later']}")
    if r["shared_link"] != ["replace:/?car=MG-1&rx-inline=1#16/-18/-44"]:
        out.append(f"the inline fallback must keep the page query and hash (shared /?car= link), got {r['shared_link']}")
    return out


served = Attrs()
served.feed(html)
handlers = {}
for tag, a in served.items:
    if tag == "link" and a.get("rel") == "stylesheet" and (a.get("href") or "").startswith("/static/rx/"):
        handlers["css"] = a.get("onerror") or ""
    if tag == "script" and (a.get("src") or "").startswith("/static/rx/"):
        handlers["js"] = a.get("onerror") or ""
if NODE:
    PRE_REVIEW_RETRY = ("try{if(!sessionStorage.getItem('rx-w1a-asset-retry')){sessionStorage.setItem('rx-w1a-asset-retry','1');"
                        "location.reload()}else{location.replace('/?rx-inline=1')}}catch(e){location.replace('/?rx-inline=1')}")
    control = retry_problems({"css": PRE_REVIEW_RETRY, "js": PRE_REVIEW_RETRY})
    check(bool(control), "positive control: the pre-review retry handler (no per-page lock, sticky key) passed the retry rule")
    check(bool(retry_problems({"css": "", "js": PRE_REVIEW_RETRY})), "positive control: a CSS link without onerror passed the retry rule")
    real = retry_problems(handlers)
    for p in real:
        check(False, "asset retry: " + p)
    INFO["asset_retry"] = {"handlers_equal": handlers.get("css") == handlers.get("js"), "problems": real, "control_problems": len(control)}

# 10. No relative url()/image-set in a style served from /static/rx/ (it would resolve under /static/rx/).
_URL = re.compile(r"""url\(\s*(["']?)\s*([^"')\s]*)""", re.I)


def relative_css(css: str) -> list[str]:
    """Independent of portal_boot_assets_w1a."""
    refs = [m.group(2) or "url()" for m in _URL.finditer(css) if not re.match(r"(?:data:|/|https?:|#)", m.group(2), re.I)]
    return refs + (["image-set("] if re.search(r"image-set\(", css, re.I) else [])


for bad in (".a{background:url(img/a.png)}", ".a{background:url( 'img/a.png' )}", '@font-face{src:url("f.woff2")}',
            ".a{background:image-set('a.png' 1x)}", ".a{background:url(../x.png)}"):
    check(bool(relative_css(bad)), f"positive control: relative reference not detected in {bad!r}")
for ok in (".a{background:url(data:image/png;base64,AA)}", ".a{background:url('/static/a.png')}", ".a{mask:url(#m)}",
           '.a{background:url("https://x.test/a.png")}', ".a{background:url(//cdn.test/a.png)}", ".a{color:red}"):
    check(not relative_css(ok), f"relative checker flags an absolute reference: {ok!r}")
for path, body in assets.items():
    if path.endswith(".css"):
        check(not relative_css(body), f"{path} carries relative references {relative_css(body)[:5]} (they resolve under /static/rx/)")

import portal_boot_assets_w1a as w1a  # noqa: E402

SYNTH = ('<!doctype html><html><head><style>.a{color:red}</style><style>.b{background:url("img/b.png")}</style></head>'
         '<body><div id="map"></div><script>var rxSynth=1</script></body></html>')


def synthetic_problems() -> list[str]:
    """A future relative url() must stay where it resolves correctly (inline) or the page falls back inline."""
    try:
        gen = w1a.assemble(SYNTH)
    except ValueError:
        return []  # served as the canonical inline page: url() resolves against '/'
    out = []
    for path, data in gen["assets"].items():
        if path.endswith(".css") and relative_css(data.decode("utf-8")):
            out.append(f"relative url() moved into {path}")
    if 'url("img/b.png")' not in gen["html"].decode("utf-8"):
        out.append("the style with a relative url() is neither inline nor refused")
    return out


check(not synthetic_problems(), f"module moves a relative url() into /static/rx/: {synthetic_problems()}")
_guard = w1a.css_relative_refs if hasattr(w1a, "css_relative_refs") else None
if _guard:
    w1a.css_relative_refs = lambda css: []  # mutation: guard removed
    try:
        check(bool(synthetic_problems()), "positive control: with the module guard removed the relative url() rule still passed")
    finally:
        w1a.css_relative_refs = _guard
else:
    check(False, "portal_boot_assets_w1a has no relative url() guard (css_relative_refs)")

# 11. The failed boot page is honest: no promise of an automatic retry, no internals, a way to try again.
PROMISES = re.compile(r"tentaremos|automaticamente|vamos tentar|m[óo]dulo|carregando|inicializando|demorando|pronto|conclu[íi]d", re.I)


def failed_copy_problems(page: str) -> list[str]:
    out = []
    title = re.search(r'<h2 id="rxBootTitle">(.*?)</h2>', page, re.S)
    text = re.search(r'<p id="rxBootText"[^>]*>(.*?)</p>', page, re.S)
    visible = " ".join(x.group(1) for x in (title, text) if x) if (title and text) else re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page, flags=re.S)
    copy = re.search(r"var S=(\{.*?\}),K=", page, re.S)
    failed_js = json.loads(copy.group(1)).get("failed", {}) if copy else {}
    for label, t in (("visible", visible), ("script copy", " ".join(failed_js.values()) if failed_js else "")):
        if not t.strip():
            out.append(f"failed {label} text missing")
        elif PROMISES.search(t):
            out.append(f"failed {label} text promises or talks internals: {PROMISES.search(t).group(0)!r} in {t.strip()[:120]!r}")
    if not re.search(r'<button id="rxBootRetry"[^>]*onclick="location\.reload\(\)"[^>]*>Tentar de novo</button>', page):
        out.append('no "Tentar de novo" button that reloads')
    if not re.search(r"body\[data-rx-boot=failed\][^{]*#rxBootGuard button[^{]*\{display:block", page):
        out.append("button is not shown in the failed state")
    return out


PRE_REVIEW_FAILED = ('<body data-rx-boot="failed"><div id="rxBootGuard"><h2>Inicializando o Raio-X Territorial</h2><p id="rxBootText">'
                     'O portal abriu, mas um módulo ainda não carregou. Tentaremos novamente automaticamente.</p>'
                     '<button id="rxBootRetry" type="button" onclick="location.reload()">Tentar novamente</button></div></body>')
check(len(failed_copy_problems(PRE_REVIEW_FAILED)) >= 2, "positive control: the pre-review failed page passed the honesty rule")
for p in failed_copy_problems(FAILED_PAGE):
    check(False, "failed boot page: " + p)

# 12. After the upgrade an old service worker's boot-page shell is never served offline.
SW_SIM = r"""
const code=require('fs').readFileSync(process.argv[2],'utf8');
const ORIGIN='https://rx.test', OLD_SHELL='rx-field-v43-shell';
const page=boot=>new Response(boot?'<div id="rxBootGuard"></div>':'<div id="map"></div>',
  {status:200,headers:{'content-type':'text/html; charset=utf-8','X-RaioX-Boot':boot||'ready'}});
async function scenario(seedOldBoot,installNet,onlineVisit){
  const store=new Map(),listeners={},key=r=>new URL(typeof r==='string'?r:r.url,ORIGIN).href;
  const caches={open:async n=>{if(!store.has(n))store.set(n,new Map());const m=store.get(n);return{
      put:async(r,res)=>{m.set(key(r),res.clone())},match:async r=>{const v=m.get(key(r));return v?v.clone():undefined},
      keys:async()=>[...m.keys()].map(u=>new Request(u)),delete:async r=>m.delete(key(r))}},
    keys:async()=>[...store.keys()],delete:async n=>store.delete(n),has:async n=>store.has(n)};
  let net=async()=>{throw new TypeError('offline')};
  const self={addEventListener:(t,f)=>{listeners[t]=f},skipWaiting:()=>{},clients:{claim:async()=>{}}};
  new Function('self','caches','location','fetch',code)(self,caches,new URL(ORIGIN),r=>net(r));
  const fire=async(type,extra)=>{const waits=[];let resp;listeners[type]({...extra,waitUntil:p=>waits.push(p),respondWith:p=>{resp=p}});
    await Promise.all(waits);return {resp}};
  const visit=async()=>{const p=(await fire('fetch',{request:new Request(ORIGIN+'/')})).resp;if(!p)return 'unhandled';
    try{const t=await (await p).text();return t.includes('rxBootGuard')?'boot':t.includes('id="map"')?'app':'other'}catch(e){return 'error'}};
  if(seedOldBoot){const c=await caches.open(OLD_SHELL);await c.put(ORIGIN+'/',page('pending'))}
  net=async()=>page(installNet);await fire('install',{});await fire('activate',{});
  let online=null;if(onlineVisit!==undefined){net=async()=>page(onlineVisit);online=await visit()}
  net=async()=>{throw new TypeError('offline')};
  return {online,offline:await visit(),caches:[...store.keys()]};
}
(async()=>{console.log(JSON.stringify({
  deploy_pending:await scenario(true,'pending'),          // old SW saved the boot page; new SW installs while still pending
  deploy_ready:await scenario(true,null),                 // new SW installs when ready: offline shell must work (not vacuous)
  pending_visit:await scenario(false,null,'pending')      // boot page seen online later must not replace the shell
}))})().catch(e=>{console.error(e&&e.stack||e);process.exit(1)});
"""


def sw_problems(sw_js: str) -> list[str]:
    src = TMP / f"sw_{abs(hash(sw_js))}.js"
    src.write_text(sw_js, encoding="utf-8")
    r = run_node(SW_SIM, str(src))
    if "error" in r:
        return [f"service worker simulation did not run: {r['error']}"]
    out = []
    if r["deploy_pending"]["offline"] == "boot":
        out.append(f"offline after the upgrade serves the old boot-page shell: {r['deploy_pending']}")
    if r["deploy_ready"]["offline"] != "app":
        out.append(f"offline shell does not work after a ready install: {r['deploy_ready']}")
    if r["pending_visit"]["online"] != "boot" or r["pending_visit"]["offline"] != "app":
        out.append(f"a boot page seen online replaced the offline shell: {r['pending_visit']}")
    INFO.setdefault("sw_sim", []).append(r)
    return out


sw_js = client.get("/sw.js").text
if NODE:
    old_name = re.sub(r"const SHELL_CACHE=[^;]+;", "const SHELL_CACHE='rx-field-v43-shell';", sw_js, count=1)
    check(old_name != sw_js or "const SHELL_CACHE='rx-field-v43-shell';" in sw_js, "positive control: could not build the pre-W1a shell name variant")
    check(any("old boot-page shell" in p for p in sw_problems(old_name)), "positive control: the pre-W1a shell cache name passed the offline boot-shell rule")
    for p in sw_problems(sw_js):
        check(False, "service worker: " + p)
shutil.rmtree(TMP, ignore_errors=True)

print(json.dumps({"ok": not FAIL, "failures": FAIL, "info": INFO}, ensure_ascii=False), flush=True)
sys.exit(1 if FAIL else 0)
