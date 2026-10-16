"""W1 gate: WhatsApp and monitoring are safe to switch on when there are users.

Everything runs offline: Meta, Google Maps and the analysis are replaced by fakes.
PYTHONPATH=. python scripts/w1_whatsapp_ready_gate.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "gate-app-secret"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"


def _signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode("utf-8")
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _message(msg_id: str, text: str, phone: str = "5538999990000") -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [{"from": phone, "id": msg_id, "text": {"body": text}}]}}]}]}


def _app(sent: list):
    import whatsapp_gateway as wa

    async def fake_send(to, body):
        sent.append((to, body))
        return {"ok": True}

    async def fake_analyze(car, **_kw):
        return {"car": {"properties": {"cod_imovel": car, "municipio": "Curvelo", "uf": "MG", "area": 14.795}},
                "embargos_ibama": {"exact": {"occurrence_count": 0}}, "prodes": {}, "anm": {}, "fire_live": {}}

    wa._send_text = fake_send
    getattr(wa, "_SEEN_IDS", {}).clear()
    getattr(wa, "_RATE", {}).clear()
    app = FastAPI()
    wa.register_routes(app, fake_analyze)
    return wa, app


def _drain(wa, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    tasks = getattr(wa, "_TASKS", set())
    while tasks and time.monotonic() < end:
        time.sleep(0.02)
    assert not tasks, "background replies did not finish"


def test_disabled_sends_nothing() -> None:
    os.environ["RX_WHATSAPP_ENABLED"] = "off"
    sent: list = []
    wa, app = _app(sent)
    with TestClient(app) as client:
        raw, headers = _signed(_message("wamid.off", "menu"))
        r = client.post("/webhooks/whatsapp", content=raw, headers=headers)
        assert r.status_code == 200 and r.json()["enabled"] is False, r.text
        _drain(wa)
    assert sent == [], sent


def test_signature_is_required() -> None:
    os.environ["RX_WHATSAPP_ENABLED"] = "on"
    os.environ.pop("WHATSAPP_APP_SECRET", None)
    sent: list = []
    wa, app = _app(sent)
    with TestClient(app) as client:
        raw, headers = _signed(_message("wamid.nosecret", "menu"))
        r = client.post("/webhooks/whatsapp", content=raw, headers=headers)
        assert r.status_code == 503, ("enabled without app secret must fail closed", r.status_code)
        os.environ["WHATSAPP_APP_SECRET"] = SECRET
        forged = dict(headers, **{"X-Hub-Signature-256": "sha256=" + "0" * 64})
        assert client.post("/webhooks/whatsapp", content=raw, headers=forged).status_code == 403
        missing = {"Content-Type": "application/json"}
        assert client.post("/webhooks/whatsapp", content=raw, headers=missing).status_code == 403
        tampered = raw.replace(b"menu", b"relatorio")
        assert client.post("/webhooks/whatsapp", content=tampered, headers=headers).status_code == 403
        _drain(wa)
    assert sent == [], sent


def test_valid_message_answers_once_and_is_rate_limited() -> None:
    os.environ["RX_WHATSAPP_ENABLED"] = "on"
    os.environ["WHATSAPP_APP_SECRET"] = SECRET
    sent: list = []
    wa, app = _app(sent)
    with TestClient(app) as client:
        raw, headers = _signed(_message("wamid.1", CAR))
        first = client.post("/webhooks/whatsapp", content=raw, headers=headers)
        assert first.status_code == 200 and first.json()["accepted"] == 1, first.text
        retry = client.post("/webhooks/whatsapp", content=raw, headers=headers)
        assert retry.json()["accepted"] == 0, "Meta retry of the same message must not be answered again"
        _drain(wa)
        texts = [body for _to, body in sent]
        assert any("14,80 ha" in t for t in texts), texts
        assert not any("14.795" in t or "fonte indisponível" in t for t in texts), texts
        accepted = 0
        for i in range(wa.RATE_MAX_MESSAGES + 5):
            raw_i, headers_i = _signed(_message(f"wamid.rate.{i}", "menu", phone="5538911112222"))
            accepted += client.post("/webhooks/whatsapp", content=raw_i, headers=headers_i).json()["accepted"]
        _drain(wa)
    assert accepted == wa.RATE_MAX_MESSAGES, accepted


def test_maps_links_never_leave_google_maps() -> None:
    import asyncio

    import whatsapp_gateway as wa

    for url in ("https://maps.app.goo.gl/AbC123", "https://www.google.com/maps/@-18.44,-44.21,15z", "https://maps.google.com.br/?q=-18.4,-44.2"):
        assert wa.maps_url_allowed(url), url
    for url in ("http://maps.app.goo.gl/AbC123", "https://169.254.169.254/latest/meta-data", "https://evil.example/?u=maps.google.com",
                "https://maps.google.com.evil.example/", "https://user@maps.google.com/", "https://maps.google.com:8443/", "file:///etc/passwd"):
        assert not wa.maps_url_allowed(url), url

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "maps.app.goo.gl" and request.url.path == "/internal":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data?q=-18.1,-44.1"})
        if request.url.host == "maps.app.goo.gl":
            return httpx.Response(302, headers={"location": "https://www.google.com/maps/@-18.4448863,-44.2181254,15z"})
        return httpx.Response(200, text="ok")

    original = wa.httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    wa.httpx.AsyncClient = client_factory
    try:
        coords = asyncio.run(wa._coords_from_maps_url("olha https://maps.app.goo.gl/AbC123"))
        assert coords == (-18.4448863, -44.2181254), coords
        requested.clear()
        asyncio.run(wa._coords_from_maps_url("https://maps.app.goo.gl/internal"))
        assert not any("169.254" in u for u in requested), requested
        requested.clear()
        assert asyncio.run(wa._coords_from_maps_url("https://169.254.169.254/latest?q=-18.1,-44.1")) is None
        assert requested == [], requested
    finally:
        wa.httpx.AsyncClient = original


def test_monitoring_never_alerts_on_a_source_that_did_not_answer() -> None:
    import monitoring_routes
    import monitoring_store as store

    before = {"car_code": CAR, "ibama_embargo_count": 0, "prodes_count": 4, "fire_latest_file": "focos_10min_20260913_1820.csv"}
    rolled = dict(before, fire_latest_file="focos_10min_20260913_1830.csv")
    assert store._diff(before, store.merge_answered(before, rolled)) == {}, "INPE file name rolling is not a change"
    assert store.snapshot_signature(before) == store.snapshot_signature(rolled)
    failed = dict(before, prodes_count=None)
    merged = store.merge_answered(before, failed)
    assert merged["prodes_count"] == 4 and store._diff(before, merged) == {}, "trying is not answering"
    embargo = dict(before, ibama_embargo_count=1)
    diff = store._diff(before, store.merge_answered(before, embargo))
    assert list(diff) == ["ibama_embargo_count"], diff
    severity, summary = store.classify_alert(diff)
    assert severity == "critical" and "embargo" in summary, (severity, summary)
    text = monitoring_routes._alert_text(CAR, diff)
    assert "ibama_embargo_count" not in text and "novo embargo IBAMA" in text, text


def test_alert_delivery_and_phone_privacy() -> None:
    import asyncio

    import monitoring_routes
    import monitoring_store as store
    import whatsapp_gateway as wa

    calls: list = []

    async def fake_template(to, template, language, params):
        calls.append(("template", to, template, language, params))
        return {"ok": True}

    async def fake_text(to, body):
        calls.append(("text", to, body))
        return {"ok": True}

    wa._send_template, wa._send_text = fake_template, fake_text
    mon = {"car_code": CAR, "channel": "whatsapp", "destination": "5538999990000"}
    saved = {"changed": True, "diff": {"ibama_embargo_count": {"before": 0, "after": 1}}}
    os.environ["WHATSAPP_ALERT_TEMPLATE"] = "alerta_imovel"
    out = asyncio.run(monitoring_routes._deliver_if_configured(mon, saved))
    assert out["mode"] == "template" and calls[-1][0] == "template", (out, calls)
    assert calls[-1][4][0] == CAR and "embargo" in calls[-1][4][1], calls[-1]
    os.environ.pop("WHATSAPP_ALERT_TEMPLATE")
    out = asyncio.run(monitoring_routes._deliver_if_configured(mon, saved))
    assert out["mode"] == "text_24h_window" and calls[-1][0] == "text", (out, calls)
    assert asyncio.run(monitoring_routes._deliver_if_configured(mon, {"changed": False})) == {"attempted": False}

    assert "destination" not in monitoring_routes._public_monitor(mon)

    original_ready, original_add = store.readiness, store.add_monitor
    added: list = []
    store.readiness = lambda: {"ready": True, "backend": "gate"}
    store.add_monitor = lambda code, channel="in_app", destination=None: added.append((code, channel, destination)) or {"id": 1, "car_code": code, "channel": channel, "destination": destination}
    try:
        app = FastAPI()

        async def fake_analyze(car, **_kw):
            raise RuntimeError("offline gate")

        monitoring_routes.register_monitoring_routes(app, fake_analyze)
        with TestClient(app) as client:
            r = client.post(f"/v1/monitoring/properties/{CAR}", json={"channel": "whatsapp", "destination": "5511988887777"})
            assert r.status_code == 403, ("a phone number cannot be subscribed by a third party over HTTP", r.status_code)
            assert added == [], added
            r = client.post(f"/v1/monitoring/properties/{CAR}", json={"channel": "in_app"})
            assert r.status_code == 200 and "destination" not in r.json()["monitor"], r.text
    finally:
        store.readiness, store.add_monitor = original_ready, original_add


def test_client_texts_have_no_internals() -> None:
    source = open("whatsapp_gateway.py", encoding="utf-8").read()
    for forbidden in ("fonte indisponível", "DATABASE_URL", "({type(e).__name__})", "Programa Queimadas indisponível"):
        assert forbidden not in source, forbidden


def main() -> None:
    test_disabled_sends_nothing()
    test_signature_is_required()
    test_valid_message_answers_once_and_is_rate_limited()
    test_maps_links_never_leave_google_maps()
    test_monitoring_never_alerts_on_a_source_that_did_not_answer()
    test_alert_delivery_and_phone_privacy()
    test_client_texts_have_no_internals()
    print("RX_W1_WHATSAPP_READY_GATE=PASS")


if __name__ == "__main__":
    main()
