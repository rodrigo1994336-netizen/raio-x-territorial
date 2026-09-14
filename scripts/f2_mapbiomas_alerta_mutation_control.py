"""Controle positivo do gate F2 (alertas validados): cada mutação quebra uma regra e o gate precisa reprovar.

Uso manual, fora do CI (roda o gate uma vez por mutação):

    python scripts/f2_mapbiomas_alerta_mutation_control.py

Imprime uma linha por mutação e termina com RX_F2_MAPBIOMAS_ALERTA_MUTATIONS=ALL_CAUGHT
(saída 0) ou com a lista das que passaram despercebidas (saída 1).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = "scripts/f2_mapbiomas_alerta_gate.py"
MODULE = ROOT / "mapbiomas_alerta.py"

MUTATIONS: dict[str, list[tuple[str, str]]] = {
    # --- regras anteriores
    "M01_car_fora_da_base_vira_nenhum": [(
        '        return _pending(code, "graphql_error" if errors else "car_not_in_source")',
        '        prop = {"propertyCode": code, "alerts": []}')],
    "M02_area_somada_em_vez_de_unida": [(
        '"area_in_car_ha": round(_area_ha(union), 4),',
        '"area_in_car_ha": round(sum(_area_ha(x["geom"]) for x in members), 4),')],
    "M03_nao_agrupa_mesma_abertura": [(
        'if recent[i]["geom"].intersects(recent[j]["geom"]):', 'if False:')],
    "M04_repete_depois_de_timeout": [(
        'reason = "timeout"  # já gastou o prazo da tentativa: não repete\n                break',
        'reason = "timeout"\n                continue')],
    "M05_falha_de_rede_vira_not_found": [(
        '        result = _pending(code, reason or "no_response", **meta)',
        '        result = parse_response({"data": {"ruralProperty": {"propertyCode": code, "alerts": []}}}, code)\n'
        '        result.update(meta)')],
    "M06_nota_no_ano_prodes_errado": [(
        'prodes_overlap(w["geom"], {n, n + 1})', 'prodes_overlap(w["geom"], {n - 1})')],
    "M07_lista_do_deter_vale_resposta": [(
        '    if not isinstance(deter, dict):\n        return "pending"',
        '    if isinstance(deter, list):\n        return "answered"\n    if not isinstance(deter, dict):\n        return "pending"')],
    "M08_credito_sem_declarar_adaptacao": [(
        '    "material adaptado sob a mesma licença"\n)\nDETER_LICENSE_URL',
        '    "material"\n)\nDETER_LICENSE_URL')],
    "M09_credito_some_da_combinacao": [(
        '        credits.append(_credit(SOURCES_PAGE_CREDIT, LICENSE_URL))', '        pass')],
    "M10_campo_interno_vaza": [('if not str(k).startswith("_")}', 'if True}')],
    "M11_insiste_depois_de_429": [(
        '                _start_cooldown(retry_after)  # respeita o limite da fonte: não repete\n'
        '                reason = "rate_limited"\n                break',
        '                reason = "rate_limited"\n                continue')],
    "M12_conta_alerta_cancelado": [(
        '        if status in _KNOWN_NOT_VALIDATED_STATUS:\n            ignored["not_validated"] += 1\n            continue\n'
        '        if status not in _VALIDATED_STATUS:',
        '        if status not in _VALIDATED_STATUS | _KNOWN_NOT_VALIDATED_STATUS:')],
    "M13_alerta_ilegivel_vira_not_found": [('    if not alerts and incomplete:', '    if False:')],
    "M14_parcial_vira_not_found": [('    elif answered and not pending:', '    elif answered:')],
    "M15_discordancia_usa_corte_mais_novo": [('return min(dates), ', 'return max(dates), ')],
    "M16_alerta_sem_geometria_some_da_conta": [(
        '        # pôde ser medido nunca deixa a resposta virar "nenhum alerta".\n'
        '                status["validated_alerts"] = "pending"\n',
        '        # pôde ser medido nunca deixa a resposta virar "nenhum alerta".\n')],
    "M17_tentativas_sem_limite": [('while attempts < MAX_ATTEMPTS:', 'while attempts < 5:')],
    # --- 1) decisão do dono
    "N01_nome_da_fonte_no_pendente": [(
        'PENDING_TEXT = "Alertas validados: consulta pendente."', 'PENDING_TEXT = "MapBiomas Alerta: consulta pendente."')],
    "N02_nome_da_fonte_no_texto": [(
        "'alertas de desmatamento validados')} sobre o imóvel.\")",
        "'alertas de desmatamento validados')} sobre o imóvel no MapBiomas Alerta.\")")],
    "N03_laudo_publico": [(
        '            "_laudo_car_url": laudo_car_url(alert_code, code),',
        '            "laudo_car_url": laudo_car_url(alert_code, code),')],
    "N04_numero_do_laudo_publico": [(
        '            "_alert_code": alert_code,', '            "_alert_code": alert_code,\n            "alert_code": alert_code,')],
    "N05_nome_da_fonte_publico": [(
        '        "_source_label": _SOURCE_LABEL,\n        "state": state,',
        '        "source_label": _SOURCE_LABEL,\n        "state": state,')],
    # --- 2) resposta parcialmente ilegível
    "N06_incompleto_ignorado_na_combinacao": [(
        '            status["validated_alerts"] = "pending"\n    base = {', '            pass\n    base = {')],
    "N07_deter_parcial_vira_resposta": [(
        '        if malformed:\n            status["inpe_deter"] = "pending"',
        '        if malformed and False:\n            status["inpe_deter"] = "pending"')],
    "N08_contagem_fechada_com_ilegivel": [(
        '        "alert_count": None if incomplete else len(alerts),', '        "alert_count": len(alerts),')],
    # --- 3) nunca levanta exceção
    "N09_decoding_error_nao_capturado": [(
        '            except httpx.DecodingError:\n                reason = "decode_error"\n                break\n'
        '            except httpx.HTTPError:\n                reason = "http_error"\n                break\n', '')],
    "N10_parse_sem_protecao": [(
        '    except Exception as exc:\n        return _pending(normalize_car_code(car_code), "schema_unexpected"',
        '    except ZeroDivisionError as exc:\n        return _pending(normalize_car_code(car_code), "schema_unexpected"')],
    "N11_consulta_sem_protecao": [(
        '    except Exception as exc:\n        elapsed = round(', '    except ZeroDivisionError as exc:\n        elapsed = round(')],
    "N12_lista_com_tipo_trocado_aceita": [(
        '    if not isinstance(value, list):\n        return None', '    if not isinstance(value, list):\n        return [str(value)]')],
    # --- 4) status desconhecido e geometria sem área
    "N13_status_desconhecido_vira_nao_validado": [(
        '            ignored["malformed"] += 1  # estado desconhecido não é "não validado"',
        '            ignored["not_validated"] += 1')],
    "N14_geometria_sem_area_aceita": [(
        '    return geom if geom is not None and _area_ha(geom) >= MIN_AREA_HA else None', '    return geom')],
    # --- 5) alerta da fonte fora do desenho atual
    "N15_alerta_fora_do_desenho_descartado": [(
        '                outside = True\n                area_in_car, method = None, None', '                continue')],
    "N16_evento_fora_do_desenho_descartado": [(
        '    for w in outside:\n        if w["date"] <= cutoff:\n            continue\n        events.append({',
        '    for w in []:\n        if w["date"] <= cutoff:\n            continue\n        events.append({')],
    # --- 6) nota pré-corte
    "N17_nota_sem_limiar_de_sobreposicao": [('SIGNIFICANT_OVERLAP = 0.5 ', 'SIGNIFICANT_OVERLAP = 0.0 ')],
    "N18_nota_atribui_validacao": [(
        ': coincide com alerta de desmatamento validado (detectado em', ': validada pelo alerta de desmatamento (detectado em')],
    # --- 7) PRODES não consultado
    "N19_prodes_falhou_ainda_afirma": [(
        '    prodes_checked = _prodes_checked(prodes)', '    prodes_checked = isinstance(prodes, dict)')],
    "N20_mascara_acumulada_conta_como_anual": [(
        '            if _PRODES_ANNUAL_LAYER not in hit["layer"]:', '            if False:')],
    # --- 8) parcial sem zeros e auditoria fora da visão pública
    "N21_parcial_expoe_zero": [(
        '        event_count, area_union = None, None  # parcial ou pendente', '        event_count, area_union = 0, 0.0  # parcial ou pendente')],
    "N22_auditoria_publica": [('        "_audit_sum_of_sources_ha": round(', '        "audit_sum_of_sources_ha": round(')],
    # --- 9) prazo total e cancelamento
    "N23_sem_prazo_total_na_leitura": [(
        '            if time.monotonic() > deadline:\n                raise _Abort("deadline")\n', '')],
    "N24_tentativa_ignora_prazo_restante": [('timeout=_attempt_timeout(remaining)', 'timeout=TIMEOUT')],
    "N25_cancelamento_ignorado_na_leitura": [(
        '            if _is_set(cancel_event):\n                raise _Abort("cancelled")\n', '')],
    "N26_cancelamento_ignorado_entre_tentativas": [
        ('            if _is_set(cancel_event):\n                reason = "cancelled"\n                break\n            if attempts:',
         '            if attempts:'),
        ('                _pause(cancel_event, RETRY_PAUSE_SECONDS)\n                if _is_set(cancel_event):\n'
         '                    reason = "cancelled"\n                    break\n',
         '                _pause(cancel_event, RETRY_PAUSE_SECONDS)\n'),
    ],
    "N27_async_nao_avisa_a_thread": [('        cancel.set()\n        raise', '        raise')],
    # --- 10) caminho de produção
    "N28_consulta_com_token": [(
        '    "Accept": "application/json",\n}', '    "Accept": "application/json",\n    "Authorization": "Bearer conta-criada",\n}')],
    "N29_timeout_infinito": [(
        'TIMEOUT = httpx.Timeout(16.0, connect=6.0, read=16.0, write=10.0, pool=8.0)', 'TIMEOUT = httpx.Timeout(None)')],
    "N30_segue_redirecionamento": [('follow_redirects=False)', 'follow_redirects=True)')],
    # --- 11) 429, código do CAR e cobertura
    "N31_sem_intervalo_depois_de_429": [(
        '                _start_cooldown(retry_after)  # respeita o limite da fonte: não repete\n', '')],
    "N32_ignora_retry_after": [(
        '    if seconds is None or seconds <= 0:\n        return RATE_LIMIT_DEFAULT_SECONDS',
        '    if True:\n        return RATE_LIMIT_DEFAULT_SECONDS')],
    "N33_digito_unicode_no_car": [(
        r'r")-[0-9]{7}-[0-9A-F]{32}", re.ASCII)', r'r")-\d{7}-[0-9A-F]{32}")')],
    "N34_uf_invalida_aceita": [('re.compile(r"(?:" + "|".join(_UFS)', 're.compile(r"(?:[A-Z]{2}|" + "|".join(_UFS)')],
    "N35_cobertura_pela_data_de_publicacao": [(
        "        if res.get(\"max_detected_date\"):\n            summary += f\" (detecções até {_br_date(res['max_detected_date'])})\"",
        "        if res.get(\"last_publication_date\"):\n            summary += f\" (publicações até {_br_date(res['last_publication_date'])})\"")],
}


def run_gate(pythonpath: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(pythonpath))
    proc = subprocess.run([sys.executable, GATE], cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    lines = [x for x in (proc.stdout + proc.stderr).strip().splitlines() if not x.startswith("RX_MAPBIOMAS_ALERTA=")]
    return proc.returncode, (lines[-1] if lines else "")


def main() -> int:
    source = MODULE.read_text(encoding="utf-8")
    code, last = run_gate([str(ROOT)])
    print(f"SEM_MUTACAO exit={code} :: {last}")
    if code != 0:
        print("RX_F2_MAPBIOMAS_ALERTA_MUTATIONS=BASELINE_FAILS")
        return 1
    missed = []
    with tempfile.TemporaryDirectory(prefix="f2_mba_mut_") as tmp:
        for name, edits in MUTATIONS.items():
            mutated = source
            for old, new in edits:
                assert mutated.count(old) == 1, f"{name}: âncora encontrada {mutated.count(old)} vez(es)"
                mutated = mutated.replace(old, new)
            folder = Path(tmp) / name
            folder.mkdir()
            (folder / "mapbiomas_alerta.py").write_text(mutated, encoding="utf-8", newline="\n")
            code, last = run_gate([str(folder), str(ROOT)])
            caught = code != 0
            if not caught:
                missed.append(name)
            print(f"{name} exit={code} {'REPROVADA' if caught else 'PASSOU_DESPERCEBIDA'} :: {last[:150]}")
    if missed:
        print("RX_F2_MAPBIOMAS_ALERTA_MUTATIONS=MISSED:" + ",".join(missed))
        return 1
    print(f"RX_F2_MAPBIOMAS_ALERTA_MUTATIONS=ALL_CAUGHT count={len(MUTATIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
