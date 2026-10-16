"""O2 - tela nova do Raio-X em /novo (amostra navegável para o dono aprovar).

Um app separado do portal atual:

* não lê nem remenda ``PORTAL_HTML`` (nenhuma âncora de string do portal); a raiz ``/`` continua
  exatamente como está;
* HTML, CSS e JS próprios em ``static/novo/``; o servidor troca os nomes por nomes com hash do
  conteúdo e entrega os arquivos com cache de um ano (``immutable``) e gzip; o HTML vai com
  ``no-cache`` + ETag, como no W1a;
* Leaflet 1.9.4 vendorizado (o mesmo arquivo de ``static/vendor``, conferido pelo SRI publicado)
  e as três fontes OFL do relatório-livro, sem modificação;
* usa os mesmos endpoints do portal: ``/v1/live/sicar/viewport-v46`` (imóveis por célula),
  ``/v1/live/car/{car}``, ``/v1/live/map-panel/{car}`` (nome validado), ``/v1/live/resolve``,
  ``/v1/live/cities`` (IBGE local), ``/v1/live/quick/{car}?deep=1`` +
  ``/v1/live/progressive/status/{car}`` (a mesma leitura da análise completa) e
  ``/v1/exports/property/{car}/kml``.

Rotas: ``/novo``, ``/novo/imovel/{CAR}``, ``/novo/prospeccao``, ``/novo/precos``,
``/novo/entrar`` e ``/novo/a/{arquivo com hash}``. Instalado por ``portal_api`` logo depois do
W1a, então a tela abre mesmo enquanto os módulos do portal carregam em segundo plano (o JS
espera os endpoints responderem, sem recarregar a página).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import html
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "static" / "novo"
LEAFLET_DIR = ROOT / "static" / "vendor" / "leaflet-1.9.4"
PREFIX = "/novo/a/"
IMMUTABLE = "public, max-age=31536000, immutable"
CAR_RE = re.compile(r"^[A-Z]{2}-\d{7}-[0-9A-F]{32}$")
# Published by leafletjs.com for 1.9.4 (same values as portal_boot_assets_w1a.LEAFLET_SRI).
LEAFLET_SRI = {
    "leaflet.js": "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=",
    "leaflet.css": "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=",
}
FONTS = {
    "__FONT_SERIF_SB__": "SourceSerif4Display-Semibold.ttf",
    "__FONT_SANS_RG__": "SourceSans3-Regular.ttf",
    "__FONT_SANS_SB__": "SourceSans3-Semibold.ttf",
}
TYPES = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ttf": "font/ttf",
}
PAGES = {
    "mapa": "Mapa",
    "imovel": "Imóvel",
    "prospeccao": "Prospecção",
    "precos": "Preços",
    "entrar": "Entrar",
    "nao-encontrado": "Página não encontrada",
}
# The page talks only to this origin; the satellite base is the only outside host (same as the portal).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data: blob: https://server.arcgisonline.com; connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'"
)


def _sri(data: bytes) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _hashed(name: str, data: bytes) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{PREFIX}{stem}.{hashlib.sha256(data).hexdigest()[:16]}.{ext}"


def build() -> dict:
    """Read the sources once and return {'assets': {path: (raw, gz, type)}, 'urls': {...}, 'template': str}."""
    assets: dict[str, tuple[bytes, bytes, str]] = {}
    urls: dict[str, str] = {}

    def put(logical: str, data: bytes) -> str:
        path = _hashed(logical, data)
        ctype = TYPES[Path(logical).suffix]
        assets[path] = (data, gzip.compress(data, 6, mtime=0), ctype)
        urls[logical] = path
        return path

    for name in LEAFLET_SRI:
        data = (LEAFLET_DIR / name).read_bytes()
        if _sri(data) != LEAFLET_SRI[name]:
            raise ValueError(f"leaflet_sri_mismatch:{name}")
        put(name, data)
    css = (SRC / "app.css").read_text(encoding="utf-8")
    for marker, font in FONTS.items():
        if marker not in css:
            raise ValueError(f"font_marker_missing:{marker}")
        css = css.replace(marker, put(font, (SRC / "fonts" / font).read_bytes()))
    put("app.css", css.encode("utf-8"))
    put("app.js", (SRC / "app.js").read_bytes())
    template = (SRC / "index.html").read_text(encoding="utf-8")
    for key, marker in (("leaflet.css", "__LEAFLET_CSS__"), ("leaflet.js", "__LEAFLET_JS__"),
                        ("app.css", "__APP_CSS__"), ("app.js", "__APP_JS__")):
        if template.count(marker) != 1:
            raise ValueError(f"template_marker:{marker}")
        template = template.replace(marker, urls[key])
    for marker in ("__TITLE__", "__ROUTE__", "__CAR__", "__FONT_PRELOAD__"):
        if marker not in template:
            raise ValueError(f"template_marker:{marker}")
    template = template.replace("__FONT_PRELOAD__", urls[FONTS["__FONT_SANS_RG__"]])
    return {"assets": assets, "urls": urls, "template": template}


BUILD = build()


def page(route: str, car: str = "") -> bytes:
    label = PAGES.get(route, PAGES["nao-encontrado"])
    title = f"{car} · Raio-X Territorial" if car else ("Raio-X Territorial" if route == "mapa" else f"{label} · Raio-X Territorial")
    out = (BUILD["template"].replace("__TITLE__", html.escape(title))
           .replace("__ROUTE__", html.escape(route)).replace("__CAR__", html.escape(car)))
    return out.encode("utf-8")


def _accepts_gzip(request: Request) -> bool:
    return "gzip" in (request.headers.get("accept-encoding") or "")


def _html(request: Request, route: str, car: str = "", status: int = 200) -> Response:
    body = page(route, car)
    etag = '"o2-' + hashlib.sha256(body).hexdigest()[:24] + '"'
    headers = {
        "Cache-Control": "no-cache",
        "ETag": etag,
        "Vary": "Accept-Encoding",
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
    }
    if status == 200 and etag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    if _accepts_gzip(request):
        headers["Content-Encoding"] = "gzip"
        body = gzip.compress(body, 6, mtime=0)
    return Response(body, status_code=status, media_type="text/html; charset=utf-8", headers=headers)


async def novo_mapa(request: Request):
    return _html(request, "mapa")


async def novo_imovel(request: Request, car_code: str):
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        return _html(request, "nao-encontrado", status=404)
    if code != car_code:
        return Response(status_code=308, headers={"Location": f"/novo/imovel/{code}" + (f"?{request.url.query}" if request.url.query else "")})
    return _html(request, "imovel", code)


def _static_page(route: str):
    async def handler(request: Request):
        return _html(request, route)
    handler.__name__ = f"novo_{route}"
    return handler


async def novo_asset(request: Request, name: str):
    item = BUILD["assets"].get(PREFIX + name)
    if item is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    raw, gz, ctype = item
    headers = {"Cache-Control": IMMUTABLE, "Vary": "Accept-Encoding", "X-Content-Type-Options": "nosniff"}
    body = raw
    if _accepts_gzip(request):
        headers["Content-Encoding"] = "gzip"
        body = gz
    return Response(body, media_type=ctype, headers=headers)


def install(app) -> None:
    if getattr(app.state, "rx_o2_tela_nova", False):
        return
    methods = ["GET", "HEAD"]
    app.add_api_route("/novo", novo_mapa, methods=methods, include_in_schema=False)
    app.add_api_route("/novo/", novo_mapa, methods=methods, include_in_schema=False)
    app.add_api_route("/novo/imovel/{car_code}", novo_imovel, methods=methods, include_in_schema=False)
    for route in ("prospeccao", "precos", "entrar"):
        app.add_api_route(f"/novo/{route}", _static_page(route), methods=methods, include_in_schema=False)
    app.add_api_route("/novo/a/{name}", novo_asset, methods=methods, include_in_schema=False)
    app.state.rx_o2_tela_nova = True
    print(f"RX_O2_TELA_NOVA=installed assets:{len(BUILD['assets'])}", flush=True)
