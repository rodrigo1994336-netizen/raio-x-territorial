"""Liga o relatório-livro v10 no serviço: o livro vira o PDF padrão, com volta por variável.

Onde entra: no fim da cadeia real do relatório, no ponto em que o V20 chama o motor que desenha o
PDF (``live_report_adapter_v20.render_v20`` → ``build_premium_property_report_v10``). Tudo o que vem
antes — consulta às fontes, payload, camadas F2 e T1 — continua igual, e o payload gravado ao lado do
PDF é o mesmo. O livro é só outra forma de desenhar o que já foi apurado.

**Como voltar atrás** (sem mexer em código):

| `RX_RELATORIO_LIVRO` | o que acontece |
|---|---|
| vazio ou `on` (padrão) | o PDF sai no formato livro; se o livro falhar, sai o relatório antigo |
| `off` | o PDF sai no formato antigo, como antes desta mudança |
| `so_livro` | só o livro; se ele falhar, a emissão falha (serve para o portão do CI) |

Duas travas de verdade, nesta ordem:

1. **O livro nunca derruba a emissão.** Qualquer erro ao desenhar o livro (fonte faltando, bloco que
   não cabe, imagem ausente) cai para o gerador antigo e grava `RX_LIVRO_FALLBACK=` no log. O cliente
   recebe um PDF.
2. **O livro nunca inventa.** Ele desenha a partir dos Fatos (``report_book_v10_fatos``), que só
   afirmam o que a fonte respondeu. O que faltar vira "consulta pendente" ou sai da folha.

A imagem do livro é opcional e tem prazo curto (``RX_LIVRO_IMAGEM_TIMEOUT_S``, padrão 9 s, com uma
segunda tentativa menor). Sem imagem, a p. 1 desenha a planta do imóvel e o livro segue inteiro.
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import Any

import live_report_adapter_v20 as v20
import report_book_v10
import report_book_v10_dados as book_dados
import report_book_v10_fatos as book_fatos

MODO = (os.getenv("RX_RELATORIO_LIVRO", "on") or "on").strip().lower()
LIVRO_LIGADO = MODO in ("on", "1", "sim", "so_livro", "somente_livro")
SO_LIVRO = MODO in ("so_livro", "somente_livro")
COM_IMAGEM = (os.getenv("RX_LIVRO_IMAGEM", "on") or "on").strip().lower() not in ("off", "0", "nao")

_render_anterior = v20.render_v20          # o gerador atual, guardado para a volta e para o portão


def montar_payload_do_livro(payload: dict, result: dict | None, pasta: Path) -> dict:
    """Acrescenta ao payload o que o livro precisa, sem tocar em nada que já esteja lá.

    - os envelopes `_v51` que ainda não existem, pela regra de transição (§2.0.2);
    - ``book_v10``, só com o que é de desenho (geometria em metros, imagem limpa, manchas).

    O gerador antigo e o portal continuam lendo os mesmos campos de antes.
    """
    fatos = book_fatos.ler_fatos(payload, result)
    book_fatos.gravar_fatos(payload, result)
    if not isinstance(payload.get("book_v10"), dict):
        payload["book_v10"] = book_dados.montar_book_v10(payload, result, pasta, fatos=fatos,
                                                         com_imagem=COM_IMAGEM)
    return payload


def render_livro(path: Any, payload: dict) -> str:
    """Desenha o livro. Falhou, desenha o relatório antigo — e diz no log que caiu."""
    destino = Path(path)
    ctx = getattr(v20._CTX, "value", None) or {}
    result = ctx.get("result") if isinstance(ctx, dict) else None
    inicio = time.monotonic()
    try:
        montar_payload_do_livro(payload, result, destino.parent)
        digest = report_book_v10.gerar_livro(destino, payload, result, pasta_imagens=destino.parent)
        print(f"RX_LIVRO_OK={round((time.monotonic() - inicio) * 1000)}ms", flush=True)
        return digest
    except Exception as erro:
        print(f"RX_LIVRO_FALLBACK={type(erro).__name__}:{str(erro)[:300]}", flush=True)
        traceback.print_exc()
        if SO_LIVRO:
            raise
        return _render_anterior(path, payload)


_start_queries_anterior = v20.start_queries


def start_queries(result: dict, car_code: str):
    """O mesmo começo de emissão do V20, com a imagem do livro buscada em paralelo.

    A imagem só depende do limite do CAR, que já veio da análise. Buscá-la agora, em segundo plano,
    tira do caminho do cliente os segundos que ela custava no fim — e nada no relatório espera por ela.

    O gancho é aqui, e não em ``generate_live_report``, de propósito: dois portões do projeto afirmam
    que ``report_api.generate_live_report`` é a função do módulo ``live_report_adapter_v20``, e
    trocar aquele nome quebrava essa ligação sem nenhum ganho — este ponto é chamado no mesmo
    instante, com os mesmos dados.
    """
    if COM_IMAGEM:
        try:
            book_dados.adiantar_imagem({"property": ((result or {}).get("car") or {}).get("properties") or {}},
                                       result)
        except Exception as erro:
            print(f"RX_LIVRO_IMAGEM_ADIANTADA=falhou:{type(erro).__name__}", flush=True)
    return _start_queries_anterior(result, car_code)


if LIVRO_LIGADO:
    v20.render_v20 = render_livro
    # O V20 instala o próprio render no V18 no momento do import; refaz a ligação com o livro.
    import live_report_adapter_v18 as v18

    v18.build_premium_property_report_v8 = render_livro
    # Só o começo da emissão é embrulhado, para a imagem sair na frente. Nenhum nome que os portões
    # usam para provar a cadeia (report_api.generate_live_report) é trocado.
    v20.start_queries = start_queries
    print(f"RX_RELATORIO_LIVRO=ligado:{MODO}", flush=True)
else:
    print("RX_RELATORIO_LIVRO=desligado", flush=True)
