"""W1a gate: shareable property link (?car=, #z/lat/lon), card share menu and offline shell.

Boots the real portal composition in-process (same path as production: sitecustomize ->
deferred modules -> boot guard ready), takes the FINAL portal HTML and the served /sw.js, and
runs the owner rules on that code in node (scripts/w1a_link_imovel_harness.js):

  - a link code that is not a valid CAR never reaches fetch, the DOM or the address bar;
  - an answer for another CAR is never shown; "não encontramos" only when SICAR answered;
    5xx, a 404 without a SICAR answer and the deadline say "consulta pendente";
  - the link notice has a visible close on every state and closing cancels the lookup;
  - selection rewrites ?car= with replaceState only (never a history entry);
  - "Link copiado" only after the clipboard really took the exact link;
  - the service worker keeps ONE shell entry whatever the query, so /?car= works offline.

Positive control of each rule: the gate mutates the served code (drops the validation, swaps
replaceState for pushState, claims "copiado" without copying, hides the busy close, treats any
404 as not found, drops the mismatch guard and the deadline, keys the shell by query) and
requires the rule's check to FAIL on each mutant. A check that passes on its mutant fails the
gate. Without the W1a module the gate fails at the first assertion (module not loaded).

Run: RX_RELEASE=V8_OPERATIONAL_ZERO_COST PYTHONPATH=. python scripts/w1a_link_imovel_gate.py
(needs node on PATH; it re-runs itself with the portal env if missing). The real screens
(menu inside the card, 44 px targets, polygon click, coverage) are in
.github/scripts/w1a_link_imovel_smoke.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "w1a_link_imovel_harness.js"
RELEASE = "V8_OPERATIONAL_ZERO_COST"
VALID = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OTHER = "MG-3120904-0123456789ABCDEF0123456789ABCDEF"
NOTFOUND = "MG-3120904-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"


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
    from fastapi.testclient import TestClient

    sw = TestClient(portal_api.app).get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers.get("content-type", ""), ("sw.js not served", sw.status_code)
    return portal_v8.PORTAL_HTML, sw.text


def check_module_contract() -> None:
    site = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
    assert "import portal_share_link_w1a" in site, "W1a module not loaded by sitecustomize"
    assert "w1a_share_link_not_loaded" in site, "sitecustomize must fail the boot when W1a is missing"
    order = [site.index(x) for x in ("import portal_identity_title_guard_v49", "import portal_share_link_w1a")]
    assert order == sorted(order), "W1a must load after the V49 identity guard (wraps the sanitized entry)"
    c2 = (ROOT / "portal_card_format_c2.py").read_text(encoding="utf-8")
    assert "window.rxCopyCarC2={copy,button,code:codeHtml,write};" in c2, "C2 must export the honest clipboard write"


def check_resolver_contract() -> None:
    # "Não encontramos" trusts only the strategies that query the exact code: they must still exist,
    # in this order, before the municipality scan in the resolver the CAR route uses.
    # (Imported only after boot: importing the W1a module earlier would inject it before V46.)
    import portal_share_link_w1a as w

    site = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
    src = (ROOT / "car_resilient.py").read_text(encoding="utf-8")
    names = re.findall(r"\('(wfs[\w]+)',\{", src)
    assert tuple(names) == w.EXACT_STRATEGIES, ("exact CAR strategies changed in car_resilient.py", names)
    assert src.index("wfs1_ogc_filter") < src.index("municipality_codes_"), "exact strategies must run before the scan"
    assert "import portal_car_resilient" in site, "the CAR route is no longer the resilient resolver"


def check_validators() -> None:
    import portal_share_link_w1a as w

    assert w.normalize_car(VALID) == VALID
    assert w.normalize_car("  " + VALID.lower() + "  ") == VALID
    bad = ("", None, "MG-3120904-XYZ", "XX-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
           "MG-312090-DFB380BECD7A4323AD8AA68FA14D011F", VALID[:-1], VALID + "A",
           "MG-3120904-GFB380BECD7A4323AD8AA68FA14D011F", VALID + '"><img src=x onerror=alert(1)>',
           '<img src=x onerror="alert(1)">', "javascript:alert(1)", VALID + "\nX", VALID + "&car=1")
    for value in bad:
        assert w.normalize_car(value) == "", ("invalid CAR accepted", value)
    assert w.public_base("https://raioxterritorial.com.br/") == "https://raioxterritorial.com.br"
    assert w.public_base("https://raio-x-territorial-app.onrender.com") == "https://raio-x-territorial-app.onrender.com"
    for value in ("", None, "http://raioxterritorial.com.br", "javascript:alert(1)", "https://raioxterritorial.com.br/x",
                  'https://evil.com/"><script>', "https://evil.com?x=1", "https://-bad.com", "https://localhost"):
        assert w.public_base(value) == "", ("unsafe public base accepted", value)


def check_final_html(html: str) -> str:
    import portal_share_link_w1a as w

    assert "RX_SHARE_LINK_W1A" in html, "W1a marker missing from final portal HTML"
    assert html.count('<script id="rxShareLinkScriptW1a">') == 1, "W1a script must be injected exactly once"
    start = html.index('<script id="rxShareLinkScriptW1a">')
    block = html[start + len('<script id="rxShareLinkScriptW1a">'): html.index("</script>", start)]

    # Wrap order: V46 entry -> V49 sanitize -> W1a (the link never bypasses sanitize).
    assert html.index("window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();") < html.index(
        "window.rxV46SelectProperty=selectWrapped") < start, "W1a must wrap the final, sanitized selection entry"

    # The share control lives in the card head (no extra card height); nothing after the CTA.
    head_hook = ('<span class="rx46-spacer"></span>${window.rxShareW1a?window.rxShareW1a.html(car,id.place):\'\'}'
                 '<button type="button" class="rx46-linkbtn" data-rx46-action="kml">Mapa KML</button>')
    assert html.count(head_hook) == 1, "V46 card head must render the W1a share control before Mapa KML"
    assert "VER ANÁLISE COMPLETA</button></div>`}" in html, "nothing may be added under the CTA (card height)"
    assert "const id=identity(p),car=id.code" in html and "window.rxCardIdentityC2=cardIdentity" in html, "card title contract changed"

    # One validation for browser and gate.
    m = re.search(r"const CAR=new RegExp\((\"[^\"]+\")\);", block)
    assert m and json.loads(m.group(1)) == w.CAR_PATTERN, "browser CAR regex diverged from the gate pattern"
    base = re.search(r"const BASE=(\"[^\"]*\");", block)
    assert base and json.loads(base.group(1)) == w.public_base(os.getenv("RX_PUBLIC_BASE_URL")), "BASE literal must be the validated env origin or empty"
    assert "pushState" not in html, "portal must not push history entries"
    assert "textContent=text" in block and "innerHTML" not in block, "notices are text, and paint never rewrites nodes"

    # The base loadCar mapping W1a mirrors (search and link must build the same property).
    assert "showProperty({car_code:p.cod_imovel,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal},c.geometry)" in html, \
        "base loadCar mapping changed: update openFromLink in portal_share_link_w1a.py"
    assert "{car_code:code,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal}" in block

    # Touch targets: every W1a control is >= 44 px on every screen, without growing the card.
    css = html[html.index('<style id="rxShareLinkW1a">'): html.index("</style>", html.index('<style id="rxShareLinkW1a">'))]
    assert re.search(r"(?m)^\.rx-share-toggle\{[^}]*min-width:44px;min-height:44px;margin:-10px -8px;", css), "share toggle below 44 px (or growing the head row)"
    assert re.search(r"\.rx-share-menu \.rx-share-item(?:,[^{]*)?\{[^}]*min-height:44px", css), "menu items below 44 px"
    assert re.search(r"\.rx-share-menu\{position:absolute;", css), "the menu must overlay the card, never add height"
    assert re.search(r"\.rx-share-state button\{[^}]*min-width:44px;min-height:44px", css)
    assert re.search(r"\.rx-share-sheet-close\{[^}]*min-height:44px", css)
    assert "setInterval(" not in block and "MutationObserver" not in block
    assert "pwned" not in html.lower()
    return block


# (name, file, anchor, replacement, checks that must FAIL on the mutant)
MUTANTS = (
    ("sem_validacao_no_norm", "w1a", "return s.length===43&&CAR.test(s)?s:''", "return s",
     ("link_invalid_code_never_used",)),
    ("sem_validacao_do_parametro", "w1a", "code=all.length===1?norm(all[0]):''", "code=all.length===1?String(all[0]).trim():''",
     ("link_invalid_code_never_used",)),
    ("pushState_no_lugar_de_replaceState", "w1a", "history.replaceState(history.state,'',next)", "history.pushState(history.state,'',next)",
     ("selection_uses_replace_state_only",)),
    ("copiado_sem_copiar", "w1a", "(await C.write(l))===true", "(await C.write(l),true)",
     ("copy_failure_never_says_copiado",)),
    ("aviso_ocupado_sem_fechar", "w1a", "el.querySelector('.rx-share-state-x').hidden=false", "el.querySelector('.rx-share-state-x').hidden=kind==='busy'",
     ("link_busy_notice_close_cancels", "link_deadline_stops_waiting_honestly")),
    ("todo_404_vira_nao_encontrado", "w1a", "if(sicarSaidNotFound(r,d))", "if(r&&r.status===404)",
     ("link_404_without_sicar_answer_is_pending_not_not_found",)),
    ("sem_guarda_de_outro_car", "w1a", "p&&norm(p.cod_imovel)===code", "p",
     ("link_answer_for_other_car_never_shown",)),
    ("sem_tempo_limite", "w1a", "const timer=setTimeout(()=>{try{ctl.abort()}catch(e){}},Math.max(0,ms))", "const timer=0",
     ("link_deadline_stops_waiting_honestly",)),
    ("fechar_aviso_nao_cancela", "w1a", "const stop=()=>{if(my!==linkSeq)return;cancelLink();setCar('')};", "const stop=()=>{};",
     ("link_busy_notice_close_cancels",)),
    ("sw_casca_pela_url_inteira", "sw", "networkFirst(req,SHELL_CACHE,2400,4,'/')", "networkFirst(req,SHELL_CACHE,2400,4)",
     ("sw_offline_shell_survives_shared_links",)),
    ("sw_sem_limpeza_de_chaves_antigas", "sw", "if(new URL(r.url).search)await shell.delete(r)", "if(false)await shell.delete(r)",
     ("sw_activate_drops_old_query_keys",)),
)

EXPECTED_CHECKS = {
    "link_valid_opens_that_car", "link_invalid_code_never_used", "link_answer_for_other_car_never_shown",
    "link_404_answered_by_sicar_says_not_found", "link_404_without_sicar_answer_is_pending_not_not_found",
    "link_5xx_pending_then_retry_button_opens", "link_deadline_stops_waiting_honestly", "link_busy_notice_close_cancels",
    "selection_uses_replace_state_only", "copy_success_says_copiado_with_the_exact_link", "copy_failure_never_says_copiado",
    "whatsapp_link_contract", "sw_offline_shell_survives_shared_links", "sw_activate_drops_old_query_keys",
}


def run_harness(node: str, w1a: str, sw: str) -> dict:
    import portal_share_link_w1a as w

    consts = {"valid": VALID, "other": OTHER, "notfound": NOTFOUND, "exact": list(w.EXACT_STRATEGIES),
              "slowMs": w.SLOW_MS, "deadlineMs": w.DEADLINE_MS, "retryGapMs": w.RETRY_GAP_MS,
              "msg": {"busy": w.MSG_BUSY, "slow": w.MSG_SLOW, "notFound": w.MSG_NOT_FOUND, "pending": w.MSG_PENDING, "invalid": w.MSG_INVALID}}
    res = subprocess.run([node, str(HARNESS)], input=json.dumps({"w1a": w1a, "sw": sw, "consts": consts}, ensure_ascii=False),
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, ("harness exited", res.returncode, res.stderr[-800:])
    return json.loads(res.stdout)


def check_rules(block: str, sw: str) -> None:
    node = shutil.which("node")
    assert node, "node not found: the W1a rules must run on the served code (setup-node before this gate)"
    base = run_harness(node, block, sw)
    assert set(base) == EXPECTED_CHECKS, ("harness checks changed", sorted(set(base) ^ EXPECTED_CHECKS))
    failed = {k: v["detail"] for k, v in base.items() if not v["ok"]}
    assert not failed, ("W1a rules fail on the served code", failed)
    for name, check in sorted(base.items()):
        print(f"W1A_RULE {name}=PASS")
    covered = set()
    for name, target, anchor, replacement, must_fail in MUTANTS:
        src = block if target == "w1a" else sw
        assert src.count(anchor) == 1, ("mutation anchor must exist exactly once", name, anchor, src.count(anchor))
        mutated = src.replace(anchor, replacement)
        out = run_harness(node, mutated if target == "w1a" else block, mutated if target == "sw" else sw)
        for check in must_fail:
            assert check in out, ("mutant crashed the harness instead of failing the rule", name, out)
            assert out[check]["ok"] is False, f"positive control failed: check {check} still passes on mutant {name}"
            covered.add(check)
        print(f"W1A_MUTANT {name}=CAUGHT by {','.join(must_fail)} ({out[must_fail[0]]['detail'][:90]})")
    required = {"link_invalid_code_never_used", "selection_uses_replace_state_only", "copy_failure_never_says_copiado",
                "link_busy_notice_close_cancels", "link_404_without_sicar_answer_is_pending_not_not_found",
                "link_answer_for_other_car_never_shown", "link_deadline_stops_waiting_honestly",
                "sw_offline_shell_survives_shared_links", "sw_activate_drops_old_query_keys"}
    assert required <= covered, ("rule without a positive control", sorted(required - covered))


def check_sw_static(sw: str) -> None:
    assert "rx-field-v43" in sw and "staleWhileRevalidate" in sw, "quality-gate V46 inline step expects these"
    assert "networkFirst(req,SHELL_CACHE,2400,4,'/')" in sw, "navigation shell must be keyed by '/' (query ignored)"


def main() -> None:
    reexec_with_portal_env()
    check_module_contract()
    html, sw = boot_portal()
    check_resolver_contract()
    check_validators()
    block = check_final_html(html)
    check_sw_static(sw)
    check_rules(block, sw)
    print("W1A_LINK_IMOVEL_GATE=PASS")


if __name__ == "__main__":
    main()
