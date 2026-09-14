"""W1a gate: shareable property link (?car=, #z/lat/lon) and card share actions.

Boots the real portal composition in-process (same path as production: sitecustomize ->
deferred modules -> boot guard ready) and inspects the FINAL portal HTML, plus the pure
validators. Fails on origin/main before W1a (positive control: marker/module missing).

Run: RX_RELEASE=V8_OPERATIONAL_ZERO_COST PYTHONPATH=. python scripts/w1a_link_imovel_gate.py
The browser behaviour (card opens, invalid codes inert, buttons at 375/1440) is covered by
.github/scripts/w1a_link_imovel_smoke.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "V8_OPERATIONAL_ZERO_COST"
VALID = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"


def reexec_with_portal_env() -> None:
    # sitecustomize decides portal vs report at import time; make the env explicit.
    if os.environ.get("RX_RELEASE") == RELEASE and os.environ.get("W1A_GATE_CHILD") == "1":
        return
    env = dict(os.environ, RX_RELEASE=RELEASE, W1A_GATE_CHILD="1", PYTHONUTF8="1")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    sys.exit(subprocess.call([sys.executable, str(Path(__file__).resolve())], env=env, cwd=str(ROOT)))


def boot_portal():
    sys.path.insert(0, str(ROOT))
    import sitecustomize  # noqa: F401
    import portal_api  # noqa: F401
    import portal_boot_guard_v26 as guard

    deadline = time.time() + 40
    while time.time() < deadline and not guard.STATE.get("ready") and not guard.STATE.get("error"):
        time.sleep(0.1)
    assert guard.STATE.get("ready") is True, ("portal boot failed", guard.STATE)
    import portal_v8

    return portal_v8.PORTAL_HTML


def check_module_contract() -> None:
    site = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
    assert "import portal_share_link_w1a" in site, "W1a module not loaded by sitecustomize"
    assert "w1a_share_link_not_loaded" in site, "sitecustomize must fail the boot when W1a is missing"
    order = [site.index(x) for x in ("import portal_identity_title_guard_v49", "import portal_share_link_w1a")]
    assert order == sorted(order), "W1a must load after the V49 identity guard (wraps the sanitized entry)"
    c2 = (ROOT / "portal_card_format_c2.py").read_text(encoding="utf-8")
    assert "window.rxCopyCarC2={copy,button,code:codeHtml,write};" in c2, "C2 must export the honest clipboard write"


def check_validators() -> None:
    import portal_share_link_w1a as w

    assert w.normalize_car(VALID) == VALID
    assert w.normalize_car("  " + VALID.lower() + "  ") == VALID
    bad = (
        "",
        None,
        "MG-3120904-XYZ",
        "XX-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
        "MG-312090-DFB380BECD7A4323AD8AA68FA14D011F",
        "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011",
        "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011FA",
        "MG-3120904-GFB380BECD7A4323AD8AA68FA14D011F",
        VALID + '"><img src=x onerror=alert(1)>',
        '<img src=x onerror="alert(1)">',
        "javascript:alert(1)",
        VALID + "\nX",
        "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F&car=1",
    )
    for value in bad:
        assert w.normalize_car(value) == "", ("invalid CAR accepted", value)
    assert w.public_base("https://raioxterritorial.com.br/") == "https://raioxterritorial.com.br"
    assert w.public_base("https://raio-x-territorial-app.onrender.com") == "https://raio-x-territorial-app.onrender.com"
    for value in ("", None, "http://raioxterritorial.com.br", "javascript:alert(1)", "https://raioxterritorial.com.br/x",
                  'https://evil.com/"><script>', "https://evil.com?x=1", "https://-bad.com", "https://localhost"):
        assert w.public_base(value) == "", ("unsafe public base accepted", value)


def check_final_html(html: str) -> None:
    import portal_share_link_w1a as w

    assert "RX_SHARE_LINK_W1A" in html, "W1a marker missing from final portal HTML"
    assert html.count('<script id="rxShareLinkScriptW1a">') == 1, "W1a script must be injected exactly once"
    start = html.index('<script id="rxShareLinkScriptW1a">')
    block = html[start: html.index("</script>", start)]

    # Wrap order: V46 entry -> V49 sanitize -> W1a (the link never bypasses sanitize).
    assert html.index("window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();") < html.index(
        "window.rxV46SelectProperty=selectWrapped") < start, "W1a must wrap the final, sanitized selection entry"

    # The card renders the share row under the CTA; the title logic is untouched.
    card_hook = "VER ANÁLISE COMPLETA</button>${window.rxShareW1a?window.rxShareW1a.html(car,id.place):''}</div>`}"
    assert html.count(card_hook) == 1, "V46 card must render the W1a share row right after the CTA"
    assert "const id=identity(p),car=id.code" in html and "window.rxCardIdentityC2=cardIdentity" in html, "card title contract changed"

    # Strict validation: the browser regex is the gate regex, uppercase-only and anchored.
    m = re.search(r"const CAR=new RegExp\((\"[^\"]+\")\);", block)
    assert m, "W1a CAR regex literal missing"
    assert json.loads(m.group(1)) == w.CAR_PATTERN, "browser CAR regex diverged from the gate pattern"
    assert "s.length===43&&CAR.test(s)" in block, "browser must check the fixed CAR length before use"
    base = re.search(r"const BASE=(\"[^\"]*\");", block)
    assert base and json.loads(base.group(1)) == w.public_base(os.getenv("RX_PUBLIC_BASE_URL")), "BASE literal must be the validated env origin or empty"

    # The URL code never reaches fetch/DOM before norm(); messages never echo it.
    assert "const all=params.getAll('car'),asked=all.length>0,code=all.length===1?norm(all[0]):''" in block
    assert "if(!code){setCar('');say('O link não traz um código CAR válido.','info');return}" in block
    assert "norm(p.cod_imovel)===code" in block, "an answer for another CAR must never open the card"
    assert "${all" not in block and "textContent=text" in block, "notice text must be set as text, never HTML"

    # Address bar: replaceState only, never pushState, anywhere in the portal.
    assert "history.replaceState(history.state,'',next)" in block
    assert "pushState" not in html, "portal must not push history entries"
    assert "u.hash=`#${Math.round(m.getZoom())}/${c.lat.toFixed(5)}/${c.lng.toFixed(5)}`" in block
    assert "const HASH=/^#(\\d{1,2})\\/(-?\\d{1,2}(?:\\.\\d{1,8})?)\\/(-?\\d{1,3}(?:\\.\\d{1,8})?)$/;" in block

    # Honest copy: "Link copiado" only on the success branch of a real clipboard write.
    assert "(await C.write(l))===true" in block and "copied.set(c,Date.now()+2800)" in block
    ok_branch = block.index("if(ok){const s=document.getElementById('rxShareSheetW1a')")
    fail_branch = block.index("else{copied.delete(c);paint(c);sheet(l)}")
    assert ok_branch < fail_branch
    assert "copiado" not in block[fail_branch: fail_branch + 60].lower()
    assert "Não foi possível copiar automaticamente" in block
    # Regression (found in W1a smoke): repainting the button with innerHTML detached the click
    # target mid-dispatch and a later map handler closed the card. Paint toggles state only.
    assert "b.innerHTML" not in block and ".rx-share-btn *{pointer-events:none}" in html

    # WhatsApp: a real link, short pt-BR text, opener isolated, no name in the text.
    assert "'https://wa.me/?text='+encodeURIComponent(t)" in block
    assert 'target="_blank" rel="noopener noreferrer"' in block
    assert "Veja este imóvel rural no Raio-X Territorial" in block
    wa_fn = block[block.index("function waText("): block.index("function waHref(")]
    assert "name" not in wa_fn.lower(), "WhatsApp text must not carry a property name"

    # The base loadCar mapping W1a mirrors (search and link must build the same property).
    assert "showProperty({car_code:p.cod_imovel,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal},c.geometry)" in html, \
        "base loadCar mapping changed: update openFromLink in portal_share_link_w1a.py"
    assert "{car_code:code,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal}" in block

    # 44 px targets on every screen; no polling loops.
    css = html[html.index('<style id="rxShareLinkW1a">'): html.index("</style>", html.index('<style id="rxShareLinkW1a">'))]
    assert re.search(r"\.rx-share-row \.rx-share-btn(?:,[^{]*)?\{[^}]*min-height:44px", css), "share buttons must be >= 44 px tall"
    assert re.search(r"\.rx-share-state button\{[^}]*min-width:44px;min-height:44px", css)
    assert re.search(r"\.rx-share-sheet-close\{[^}]*min-height:44px", css)
    assert "setInterval(" not in block and "MutationObserver" not in block
    assert "pwned" not in html.lower()


def main() -> None:
    reexec_with_portal_env()
    check_module_contract()
    html = boot_portal()
    check_validators()
    check_final_html(html)
    print("W1A_LINK_IMOVEL_GATE=PASS")


if __name__ == "__main__":
    main()
