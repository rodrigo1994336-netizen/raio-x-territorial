"""R3 · portão da foto de satélite limpa e do validador público do relatório.

O que ele confere, sempre no comportamento e nunca na intenção escrita no código:

  FOTO LIMPA (sentinel_limpo / foto_limpa_no_relatorio)
    R14  a fonte não escreve NADA sobre a imagem — nem texto, nem limite, nem crédito;
         provado por pixel, com as imagens montadas sem rede a partir de bandas sintéticas;
    R15  o contraste da cor natural sai do MESMO corte nas três bandas (esticar banda a
         banda troca a cor da terra, e cor errada é informação errada);
    R16  a cena escolhida é a mais recente entre as que prestam, e a tolerância usada volta
         dita no resultado;
    R17  cena sem data não entra — foto sem data é o defeito que esta entrega existe para
         não repetir;
    R18  nenhuma cena boa não vira imagem vazia nem data inventada: vira consulta pendente;
    R19  cena antiga ou nublada é dita, com a data e a nuvem, nunca escondida;
    R20  a legenda do verdor é dado, com faixa declarada, e a nota diz o que o verdor NÃO
         mede;
    R21  quem desenha o limite por cima é o relatório, e o que ele desenha é linha: a
         página com a foto não tem um caractere sequer;
    R22  a foto limpa é uma lente do caminho real de imagens, cronometrada como as outras.

  VALIDADOR (report_validacao_codigo / validacao_publica / report_validacao_pdf)
    R01  o contrato do formato 1 está congelado (mudou o campo, a ordem das fontes ou a
         lista de UFs, muda o significado de código já emitido);
    R02  ida e volta exata: CAR, data e hora de Brasília, versão e pendências;
    R03  uma letra trocada em QUALQUER posição derruba a conferência;
    R04  código assinado com outro segredo não confere aqui;
    R05  sem segredo não se promete nada: não imprime código e a página diz "indisponível",
         jamais "não confere" nem "autêntico";
    R06  o segredo não aparece no código, na página, no endereço, na resposta nem no que
         for impresso no terminal;
    R07  limite de tentativas por origem, e encaminhamento inventado não troca a origem;
    R08  a página mostra o que está assinado e diz que confere o documento, não as fontes;
    R09  a página não roda script nem mostra dado pessoal;
    R10  os textos da página passam no mesmo lint de cliente do portão T2;
    R11  o código impresso no PDF, lido de volta do PDF, confere;
    R12  sem segredo o PDF não imprime código nem promete conferência;
    R13  o código impresso cabe na linha — célula que estoura corta o código em silêncio,
         e código cortado é informação errada.

Controle positivo por mutação: scripts/r3_foto_validador_mutation_control.py.

Rodar:
  RX_RELEASE=OFF PYTHONPATH=. python scripts/r3_foto_validador_gate.py
"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A raiz entra no FIM do caminho de importação, nunca no começo: o controle positivo por
# mutação injeta a cópia mutada pelo PYTHONPATH, e uma raiz na frente faria o portão medir
# sempre o arquivo original — controle que não alcança o próprio portão não prova nada.
for _pasta in (str(ROOT), str(ROOT / "scripts")):
    if _pasta not in sys.path:
        sys.path.append(_pasta)

SEGREDO = "segredo-do-portao-r3-nao-vai-para-producao"
OUTRO_SEGREDO = "segredo-de-quem-tenta-forjar-um-codigo"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
EMISSAO = "2026-10-16T17:42:10+00:00"
EMISSAO_BR = "16/10/2026 às 14:42 (horário de Brasília)"
VERSAO = "0.47.0"
# Contrato congelado do formato 1. Se este valor mudar, código já emitido passa a ser lido
# com outro significado: a saída é criar o formato 2, nunca atualizar o número aqui.
CONTRATO = "71eb39499433996daf6191d9eea35a7d99007d956e4f85ae32da6a7895d5dd29"

FALHAS: list[str] = []


def falhar(regra: str, detalhe: str) -> None:
    FALHAS.append(f"{regra}: {detalhe}")
    print(f"FALHA {regra}: {detalhe}", flush=True)


def conferir(regra: str, condicao, detalhe: str = "") -> None:
    if condicao:
        print(f"ok {regra}", flush=True)
    else:
        falhar(regra, detalhe or "condição falsa")


def com_segredo(valor: str | None) -> None:
    if valor is None:
        os.environ.pop("RX_VALIDACAO_SEGREDO", None)
    else:
        os.environ["RX_VALIDACAO_SEGREDO"] = valor


# ------------------------------------------------------------------ bandas sintéticas

def bandas(altura: int = 90, largura: int = 140):
    import numpy as np

    linha, coluna = np.mgrid[0:altura, 0:largura]
    vermelho = (900 + 600 * np.sin(coluna / 11.0) + 240 * np.cos(linha / 7.0)).astype("float32")
    verde = (1100 + 500 * np.cos(coluna / 13.0)).astype("float32")
    azul = (800 + 400 * np.sin(linha / 9.0)).astype("float32")
    infra = (2600 + 1800 * np.sin(coluna / 17.0) * np.cos(linha / 10.0)).astype("float32")
    return vermelho, verde, azul, infra


def cena(data: str, nuvem, identificador: str = "x") -> dict:
    propriedades = {"datetime": data, "eo:cloud_cover": nuvem} if data else {"eo:cloud_cover": nuvem}
    return {"id": identificador, "properties": propriedades}


# ------------------------------------------------------------------ foto limpa

def regras_da_foto() -> dict:
    import numpy as np
    from PIL import Image

    import sentinel_limpo as foto

    fonte = Path(foto.__file__).read_text(encoding="utf-8")
    proibido = [x for x in ("ImageDraw", "ImageFont", "drawString", ".text(", "truetype")
                if x in fonte]
    conferir("R14_FONTE_NAO_DESENHA_TEXTO", not proibido and not hasattr(foto, "ImageDraw"),
             f"a fonte da imagem usa {proibido}")

    pasta = Path(tempfile.mkdtemp(prefix="r3_foto_"))
    vermelho, verde, azul, infra = bandas()
    cor = pasta / "cor.jpg"
    verdor = pasta / "verdor.png"
    calculado = foto.montar_imagens(vermelho, verde, azul, infra, cor, verdor)

    valido = (vermelho > 0) & (verde > 0) & (azul > 0)
    esperado_cor = foto.esticar_cor_natural(vermelho, verde, azul, valido)
    lido_cor = np.asarray(Image.open(cor).convert("RGB")).astype("int16")
    desvio = int(np.max(np.abs(lido_cor - esperado_cor.astype("int16"))))
    # JPEG mexe no pixel; texto ou linha desenhada mexe MUITO mais do que isso.
    conferir("R14_COR_NATURAL_SEM_NADA_POR_CIMA", desvio <= 26, f"desvio máximo {desvio}")

    amarelo = np.all(lido_cor == np.array([255, 238, 70]), axis=-1)
    conferir("R14_SEM_CONTORNO_GRAVADO", not bool(amarelo.any()),
             f"{int(amarelo.sum())} pixels da cor do contorno dentro da foto")

    soma = infra + vermelho
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = np.where(soma > 0, (infra - vermelho) / soma, np.nan)
    esperado_verdor = np.zeros(ndvi.shape + (3,), dtype="uint8")
    esperado_verdor[:] = foto.SEM_LEITURA
    legivel = np.isfinite(ndvi) & valido
    for de, ate, cor_classe, _ in foto.CLASSES_DE_VERDOR:
        faixa = legivel & (ndvi >= de) & (ndvi < ate if ate < 1.0 else ndvi <= ate)
        esperado_verdor[faixa] = cor_classe
    lido_verdor = np.asarray(Image.open(verdor).convert("RGB"))
    conferir("R14_VERDOR_PIXEL_A_PIXEL", bool(np.array_equal(lido_verdor, esperado_verdor)),
             "a imagem de verdor não é exatamente a classificação do NDVI")

    # R15: o corte é um só. Com corte por banda, uma banda escura e outra clara saem com o
    # mesmo brilho — a cor da terra muda e ninguém percebe olhando o código.
    escura = (vermelho * 0.25).astype("float32")
    junto = foto.esticar_cor_natural(escura, verde, azul, valido)
    media_vermelho = float(np.mean(junto[..., 0]))
    media_verde = float(np.mean(junto[..., 1]))
    conferir("R15_CONTRASTE_DAS_TRES_BANDAS_JUNTAS", media_vermelho < media_verde * 0.8,
             f"banda escurecida saiu com brilho {media_vermelho:.1f} contra {media_verde:.1f}")

    # R16 e R17: escolha da cena, sem rede.
    itens = [
        cena("2026-01-10T13:00:00Z", 2.0, "antiga_limpa"),
        cena("2026-09-30T13:00:00Z", 8.0, "recente_limpa"),
        cena("2026-10-12T13:00:00Z", 30.0, "recentissima_nublada"),
        cena("", 0.1, "sem_data"),
    ]
    escolhidas, politica = foto.escolher_cenas(itens)
    conferir("R16_MAIS_RECENTE_ENTRE_AS_LIMPAS",
             bool(escolhidas) and escolhidas[0]["id"] == "recente_limpa" and politica == "nuvem_ate_12",
             f"escolheu {[x['id'] for x in escolhidas]} com política {politica}")
    so_sem_data, politica_sem_data = foto.escolher_cenas([cena("", 0.1, "sem_data")])
    conferir("R17_CENA_SEM_DATA_NAO_ENTRA",
             all(x["id"] != "sem_data" for x in escolhidas)
             and not so_sem_data and politica_sem_data == "nenhuma_cena",
             "cena sem data entrou na escolha — foto sem data é o defeito que isto evita")

    so_nublada, politica_nublada = foto.escolher_cenas([cena("2026-10-12T13:00:00Z", 30.0, "nublada")])
    conferir("R16_AFROUXA_SO_QUANDO_PRECISA",
             bool(so_nublada) and politica_nublada == "nuvem_ate_45",
             f"política {politica_nublada}")

    vazio, politica_vazia = foto.escolher_cenas([])
    conferir("R18_SEM_CENA_NAO_INVENTA", not vazio and politica_vazia == "nenhuma_cena",
             f"devolveu {vazio} / {politica_vazia}")
    pendencia = foto.pendente("catalogo:TimeoutException")
    conferir("R18_PENDENTE_E_PENDENTE",
             pendencia.get("pendente") is True and pendencia.get("ok") is False
             and "pendente" in pendencia.get("mensagem", "").lower()
             and "data_cena" not in pendencia,
             f"pendência mal formada: {pendencia}")

    # R19: velha ou nublada, o texto avisa — e a data e a nuvem continuam lá.
    meta_velha = {"ok": True, "data_cena_br": "02/03/2025", "nuvem_pct": 22.0, "idade_dias": 410,
                  "cena_antiga": True, "cena_nublada": True,
                  "aviso": foto._aviso(410, 22.0, True, True)}
    texto_velha = foto.texto_da_cena(meta_velha)
    conferir("R19_CENA_VELHA_E_NUBLADA_AVISA",
             "02/03/2025" in texto_velha and "22" in texto_velha and "410 dias" in texto_velha,
             f"texto sem aviso completo: {texto_velha!r}")
    conferir("R19_CENA_BOA_NAO_INVENTA_AVISO", foto._aviso(4, 0.1, False, False) is None,
             "cena recente e limpa ganhou aviso")

    # R20: legenda é dado, com faixa declarada e a ressalva do que o verdor não mede.
    legenda = foto.legenda_de_verdor("12/10/2026")
    faixas = legenda.get("faixas") or []
    nota = (legenda.get("nota") or "").lower()
    conferir("R20_LEGENDA_COM_FAIXA_DECLARADA",
             len(faixas) == len(foto.CLASSES_DE_VERDOR)
             and all({"de", "ate", "cor", "rotulo"} <= set(x) for x in faixas)
             and legenda.get("sem_leitura", {}).get("rotulo"),
             f"legenda incompleta: {legenda}")
    conferir("R20_LEGENDA_DIZ_O_QUE_NAO_MEDE",
             "12/10/2026" in (legenda.get("nota") or "") and "não mede produtividade" in nota
             and "não confirma safra" in nota,
             f"nota fraca: {legenda.get('nota')!r}")
    conferir("R20_LEGENDA_SEM_DATA_NAO_INVENTA_DATA", "nota" not in foto.legenda_de_verdor(),
             "legenda sem data trouxe nota com data")
    return {"pasta": pasta, "cor": cor, "verdor": verdor}


def regras_do_desenho(artefatos: dict) -> None:
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    import foto_limpa_no_relatorio as desenho
    import sentinel_limpo as foto

    fonte = Path(desenho.__file__).read_text(encoding="utf-8")
    escreve = [x for x in ("drawString", "drawCentredString", "drawRightString", "setFont")
               if x in fonte]
    conferir("R21_RELATORIO_DESENHA_LINHA_NAO_TEXTO", not escreve,
             f"o desenho sobre a foto usa {escreve}")

    pasta = artefatos["pasta"]
    anel = [[(10, 10), (120, 14), (118, 70), (14, 66), (10, 10)]]
    altura_px, largura_px = 90, 140

    def paginar(nome: str, limite):
        caminho = pasta / nome
        documento = SimpleDocTemplate(str(caminho), pagesize=A4)
        documento.build([desenho.FotoComLimite(str(artefatos["cor"]), limite, largura_px, altura_px)])
        return caminho

    com_limite = paginar("com_limite.pdf", anel)
    sem_limite = paginar("sem_limite.pdf", [])
    texto = "".join((p.extract_text() or "") for p in PdfReader(str(com_limite)).pages).strip()
    conferir("R21_PAGINA_DA_FOTO_SEM_UM_CARACTERE", texto == "",
             f"a página com a foto trouxe texto: {texto[:120]!r}")
    conferir("R21_O_LIMITE_E_MESMO_DESENHADO",
             com_limite.stat().st_size > sem_limite.stat().st_size,
             "desenhar com e sem limite deu o mesmo arquivo")

    meta = {"ok": True, "cor_natural_path": str(artefatos["cor"]), "verdor_path": str(artefatos["verdor"]),
            "data_cena_br": "12/10/2026", "nuvem_pct": 0.1, "largura_px": largura_px,
            "altura_px": altura_px, "limite_px": anel,
            "legenda_verdor": foto.legenda_de_verdor("12/10/2026")}
    blocos = desenho.blocos_da_foto_limpa({"satellite_imagery": {"foto_limpa": meta}})
    tem_foto = [x for x in blocos if isinstance(x, desenho.FotoComLimite)]
    conferir("R21_LEGENDA_FICA_FORA_DO_RECORTE",
             bool(tem_foto) and all(not getattr(x, "limite_px", None) or True for x in tem_foto)
             and any(type(x).__name__ == "Table" for x in blocos),
             "a legenda não veio como bloco próprio ao lado da foto")
    conferir("R18_FOTO_PENDENTE_NAO_SOME",
             len(desenho.blocos_da_foto_limpa(
                 {"satellite_imagery": {"foto_limpa": foto.pendente("x")}})) == 1,
             "foto pendente não deixou linha nenhuma no relatório")
    conferir("R18_SEM_FOTO_NAO_ABRE_ESPACO",
             desenho.blocos_da_foto_limpa({"satellite_imagery": {}}) == [],
             "emissão sem foto abriu espaço no relatório")


def regra_do_caminho_das_imagens() -> None:
    """R22: a foto limpa é lente do caminho real, cronometrada como as outras."""
    import asyncio

    import visual_hybrid

    async def falsa(nome):
        return {"ok": True, "path": f"/tmp/{nome}.jpg", "source": nome}

    originais = (visual_hybrid.build_highres_reference_image,
                 visual_hybrid.build_sentinel_cog_property_image,
                 visual_hybrid.build_clean_sentinel_photo,
                 visual_hybrid.build_property_visual_plate)
    try:
        visual_hybrid.build_highres_reference_image = lambda *a, **k: falsa("esri")
        visual_hybrid.build_sentinel_cog_property_image = lambda *a, **k: falsa("sentinel")
        visual_hybrid.build_clean_sentinel_photo = lambda *a, **k: falsa("foto_limpa")
        visual_hybrid.build_property_visual_plate = lambda *a, **k: {"ok": True, "path": "/tmp/p.jpg"}
        saida = io.StringIO()
        with redirect_stdout(saida):
            meta = asyncio.run(visual_hybrid.build_hybrid_property_imagery(
                {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
                Path(tempfile.mkdtemp(prefix="r3_pipe_")) / "visual.jpg"))
        registro = saida.getvalue()
    finally:
        (visual_hybrid.build_highres_reference_image, visual_hybrid.build_sentinel_cog_property_image,
         visual_hybrid.build_clean_sentinel_photo, visual_hybrid.build_property_visual_plate) = originais
    conferir("R22_FOTO_LIMPA_E_LENTE_DO_CAMINHO_REAL",
             (meta.get("foto_limpa") or {}).get("ok") is True, f"meta sem foto limpa: {list(meta)}")
    conferir("R22_LENTE_CRONOMETRADA", "RX_VISUAL_STAGE=foto_limpa:" in registro,
             "a lente da foto limpa não foi cronometrada")


# ------------------------------------------------------------------ validador

def foto_sintetica(pasta: Path) -> dict:
    """Meta da foto limpa com imagens montadas na hora, sem rede."""
    import sentinel_limpo as foto

    vermelho, verde, azul, infra = bandas()
    cor = pasta / "foto_cor.jpg"
    verdor = pasta / "foto_verdor.png"
    foto.montar_imagens(vermelho, verde, azul, infra, cor, verdor)
    return {"ok": True, "cor_natural_path": str(cor), "verdor_path": str(verdor),
            "data_cena": "2026-10-12", "data_cena_br": "12/10/2026", "nuvem_pct": 0.1,
            "idade_dias": 4, "cena_antiga": False, "cena_nublada": False,
            "largura_px": 140, "altura_px": 90, "resolucao_m": 10,
            "limite_px": [[(10, 10), (120, 14), (118, 70), (14, 66), (10, 10)]],
            "legenda_verdor": foto.legenda_de_verdor("12/10/2026")}


def payload_do_relatorio(pasta: Path) -> dict:
    from PIL import Image

    capa = pasta / "capa.jpg"
    Image.new("RGB", (32, 32), (238, 240, 238)).save(capa, format="JPEG")
    return {
        "report_id": "RX-R3-GATE", "generated_at": EMISSAO, "source_version": "R3",
        "property": {"name": "", "area_ha": 1981.2, "municipality": "Curvelo", "uf": "MG",
                     "car_code": CAR},
        "car": {"status": "AT", "fields": [["Status do imóvel", "AT"]]},
        "land": {}, "enforcement": {}, "mining": {}, "productive": {}, "monitoring": {},
        "conclusion": {}, "water": {}, "narrative": {}, "environment": {}, "agropecuaria": {},
        "satellite_imagery": {"foto_limpa": foto_sintetica(pasta)},
        "satellite_image_path": str(capa),
        "sources": [
            {"name": "SICAR", "description": "Cadastro consultado.", "status": "CONSULTADA"},
            {"name": "Registro de imóveis", "description": "Não consultada nesta emissão.",
             "status": "NÃO CONSULTADA"},
            {"name": "Fonte que ninguém traduziu", "description": "Sem resposta.",
             "status": "INDISPONÍVEL"},
        ],
    }


def regras_do_codigo() -> None:
    import report_validacao_codigo as validacao

    com_segredo(SEGREDO)
    conferir("R01_CONTRATO_CONGELADO", validacao.digest_do_contrato() == CONTRATO,
             f"contrato mudou para {validacao.digest_do_contrato()} — crie o formato 2")
    conferir("R01_BITMAP_COBRE_O_CATALOGO",
             len(validacao.FONTES) < validacao.BITS_PENDENCIA
             and validacao.BIT_OUTRAS == validacao.BITS_PENDENCIA - 1
             and len(set(validacao.FONTES_IDS)) == len(validacao.FONTES_IDS),
             "o catálogo de fontes não cabe no espaço reservado no código")
    conferir("R01_ALFABETO_SEM_LETRA_AMBIGUA",
             not ({"I", "L", "O", "U"} & set(validacao.ALFABETO)) and len(validacao.ALFABETO) == 32,
             "alfabeto com letra que se confunde na leitura")

    saida = io.StringIO()
    with redirect_stdout(saida):
        emitido = validacao.emitir(CAR, EMISSAO, VERSAO,
                                   fontes_sem_resposta=["registro", "sinaflor", "nao_catalogada"],
                                   registrar_pendencias=True)
        conferido = validacao.conferir(emitido["codigo"])
    impresso = saida.getvalue()

    conferir("R02_IDA_E_VOLTA_DO_CAR", conferido.get("car_code") == CAR, str(conferido)[:200])
    conferir("R02_IDA_E_VOLTA_DA_HORA", conferido.get("emitido_em") == EMISSAO_BR,
             f"{conferido.get('emitido_em')!r} em vez de {EMISSAO_BR!r}")
    conferir("R02_IDA_E_VOLTA_DA_VERSAO", conferido.get("versao_relatorio") == VERSAO,
             str(conferido.get("versao_relatorio")))
    conferir("R02_IDA_E_VOLTA_DAS_PENDENCIAS",
             conferido.get("pendencias_registradas") is True
             and conferido.get("pendencias_outras") is True
             and validacao.rotulo_da_fonte("sinaflor") in conferido.get("pendencias", [])
             and validacao.rotulo_da_fonte("registro") in conferido.get("pendencias", []),
             str(conferido)[:260])

    sem_registro = validacao.conferir(validacao.emitir(CAR, EMISSAO, VERSAO)["codigo"])
    conferir("R02_PENDENCIA_NAO_REGISTRADA_NAO_VIRA_NENHUMA",
             sem_registro.get("pendencias_registradas") is False
             and sem_registro.get("pendencias") == [],
             "código sem lista de pendências afirmou alguma coisa sobre elas")

    conferir("R13_TAMANHO_E_GRUPOS",
             len(emitido["codigo"]) == validacao.TAMANHO
             and emitido["codigo_formatado"].count("-") == validacao.TAMANHO // validacao.GRUPO - 1
             and set(emitido["codigo"]) <= set(validacao.ALFABETO),
             f"código fora do formato: {emitido['codigo_formatado']}")
    conferir("R02_ACEITA_COMO_A_PESSOA_DIGITA",
             validacao.conferir(" rx-" + emitido["codigo_formatado"].lower().replace("1", "l", 1)
                                + " ").get("estado") == "autentico",
             "o código digitado com minúscula, prefixo e letra confundida não foi aceito")

    trocados = 0
    for posicao in range(validacao.TAMANHO):
        original = emitido["codigo"][posicao]
        outro = validacao.ALFABETO[(validacao.ALFABETO.index(original) + 7) % 32]
        alterado = emitido["codigo"][:posicao] + outro + emitido["codigo"][posicao + 1:]
        if validacao.conferir(alterado).get("estado") == "autentico":
            trocados += 1
    conferir("R03_UMA_LETRA_TROCADA_DERRUBA", trocados == 0,
             f"{trocados} posições aceitaram letra trocada")

    com_segredo(OUTRO_SEGREDO)
    forjado = validacao.emitir(CAR, EMISSAO, VERSAO)["codigo"]
    com_segredo(SEGREDO)
    conferir("R04_FORJADO_COM_OUTRO_SEGREDO_NAO_CONFERE",
             validacao.conferir(forjado).get("estado") == "nao_confere", "código forjado passou")
    conferir("R04_CHUTE_NAO_CONFERE",
             validacao.conferir("0" * validacao.TAMANHO).get("estado") == "nao_confere",
             "código chutado passou")

    conferir("R06_SEGREDO_NAO_VAI_PARA_O_TERMINAL", SEGREDO not in impresso, "segredo impresso")
    conferir("R06_SEGREDO_NAO_VAI_NO_CODIGO_NEM_NO_ENDERECO",
             SEGREDO not in emitido["codigo"] and SEGREDO not in emitido["url"],
             "segredo no código ou no endereço")

    com_segredo(None)
    conferir("R05_SEM_SEGREDO_NAO_EMITE",
             validacao.emitir(CAR, EMISSAO, VERSAO).get("ok") is False, "emitiu código sem segredo")
    conferir("R05_SEM_SEGREDO_NAO_ACUSA",
             validacao.conferir(emitido["codigo"]).get("estado") == "indisponivel",
             "sem segredo o código virou veredito")
    com_segredo("curto")
    conferir("R05_SEGREDO_CURTO_E_O_MESMO_QUE_NENHUM",
             validacao.validacao_disponivel() is False, "segredo curto foi aceito")
    com_segredo(SEGREDO)

    pendentes, outras = validacao.pendencias_do_relatorio(
        payload_do_relatorio(Path(tempfile.mkdtemp(prefix="r3_pay_"))))
    conferir("R02_PENDENCIA_LIDA_DA_TABELA_DO_RELATORIO",
             pendentes == ["registro"] and outras is True,
             f"leu {pendentes} / outras={outras}")


def regras_do_pdf() -> None:
    import report_ptbr_v50
    from pypdf import PdfReader
    from reportlab.pdfbase.pdfmetrics import stringWidth

    report_ptbr_v50.install()
    import report_blocos_r3  # noqa: F401  (instala os dois blocos no motor de hoje)
    import report_validacao_codigo as validacao
    import report_validacao_pdf as bloco
    from report_engine_v9 import build_premium_property_report_v9

    pasta = Path(tempfile.mkdtemp(prefix="r3_pdf_"))
    com_segredo(SEGREDO)
    # A versão que o relatório imprime é a do serviço. Aqui ela é fixada para o portão
    # medir a ida e a volta de um valor conhecido, e devolvida no fim.
    import report_api
    versao_do_servico = report_api.APP_VERSION
    report_api.APP_VERSION = VERSAO
    payload = payload_do_relatorio(pasta)
    esperado = bloco.codigo_do_relatorio(payload)
    caminho = pasta / "com_codigo.pdf"
    build_premium_property_report_v9(caminho, payload)
    texto = "\n".join((p.extract_text() or "") for p in PdfReader(str(caminho)).pages)
    plano = " ".join(texto.split())

    conferir("R21_A_FOTO_ENTRA_NO_RELATORIO",
             "Imagem de satélite de 12/10/2026" in plano
             and "nuvem de 0,1% na cena" in plano
             and "mede produtividade" in plano.casefold()
             and "vegetação densa" in plano,
             "a foto limpa, a data da cena ou a legenda não chegaram ao PDF do cliente")
    conferir("R11_O_PDF_TRAZ_O_CONVITE",
             bloco.TITULO in plano and validacao.CAMINHO.lstrip("/") in plano,
             "o PDF não diz onde conferir")

    # O código é lido DE VOLTA do PDF, nunca comparado com um que o portão calculou à parte:
    # o relatório limpa o payload antes de desenhar (fonte fora de escopo sai da tabela), e
    # um código calculado sobre o payload cru não é o que o cliente recebe impresso.
    grupo = r"[0-9A-HJKMNP-TV-Z]{6}"
    impresso = re.search(grupo + f"(?:-{grupo})" + "{%d}" % (bloco.LINHA_1 - 1)
                         + r"\s+" + grupo + f"(?:-{grupo})"
                         + "{%d}" % (validacao.TAMANHO // validacao.GRUPO - bloco.LINHA_1 - 1), plano)
    recuperado = validacao.normalizar(impresso.group(0)) if impresso else None
    lido = validacao.conferir(recuperado) if recuperado else {}
    conferir("R11_CODIGO_IMPRESSO_CONFERE_DE_VOLTA",
             lido.get("estado") == "autentico" and lido.get("car_code") == CAR
             and lido.get("emitido_em") == EMISSAO_BR and lido.get("versao_relatorio") == VERSAO,
             f"o código lido do PDF não confere: {str(lido)[:200]}")
    primeira, segunda = (impresso.group(0).split() if impresso else ("", ""))
    conferir("R11_NORMALIZACAO_PTBR_NAO_REESCREVE_O_CODIGO",
             report_ptbr_v50.normalize_text(esperado["codigo_formatado"]) == esperado["codigo_formatado"],
             "a normalização pt-BR mexeu no código")

    for linha in (primeira, segunda):
        largura = stringWidth(linha, bloco.FONTE_DO_CODIGO, bloco.CORPO_DO_CODIGO)
        conferir(f"R13_LINHA_CABE_{len(linha)}", largura <= bloco.LARGURA_DO_CODIGO,
                 f"{largura:.0f}pt em célula de {bloco.LARGURA_DO_CODIGO:.0f}pt — código cortado")

    conferir("R06_SEGREDO_NAO_VAI_PARA_O_PDF", SEGREDO not in texto, "segredo impresso no PDF")

    com_segredo(None)
    sem = pasta / "sem_codigo.pdf"
    build_premium_property_report_v9(sem, payload_do_relatorio(pasta))
    texto_sem = " ".join("\n".join(
        (p.extract_text() or "") for p in PdfReader(str(sem)).pages).split())
    conferir("R12_SEM_SEGREDO_O_PDF_NAO_PROMETE",
             bloco.TITULO not in texto_sem and esperado["codigo"][:12] not in texto_sem,
             "sem segredo o PDF ainda prometeu conferência")
    com_segredo(SEGREDO)
    report_api.APP_VERSION = versao_do_servico


def regras_da_pagina() -> None:
    import report_validacao_codigo as validacao
    import validacao_publica
    from fastapi.testclient import TestClient
    from report_api import app

    com_segredo(SEGREDO)
    cliente = TestClient(app)
    emitido = validacao.emitir(CAR, EMISSAO, VERSAO, fontes_sem_resposta=["registro"],
                               registrar_pendencias=True)

    def limpar_limite():
        validacao_publica._TENTATIVAS.clear()

    limpar_limite()
    pagina = cliente.get(f"/validar/{emitido['codigo_formatado']}")
    html = pagina.text
    conferir("R08_PAGINA_MOSTRA_O_QUE_ESTA_ASSINADO",
             pagina.status_code == 200 and CAR in html and "16/10/2026" in html
             and "14:42" in html and VERSAO in html
             and validacao.rotulo_da_fonte("registro") in html,
             "a página não mostrou os campos assinados")
    conferir("R08_PAGINA_DIZ_O_QUE_CONFERE",
             "não repete as consultas" in html and "autenticidade do documento" in html,
             "a página não separa autenticidade de conteúdo das fontes")
    conferir("R09_PAGINA_SEM_SCRIPT",
             "<script" not in html.lower() and "onclick" not in html.lower()
             and "script-src 'none'" not in html
             and "default-src 'none'" in pagina.headers.get("content-security-policy", ""),
             "a página roda script ou não declara política de conteúdo")
    # Dado pessoal é identificador de gente, não assunto: "titularidade" é o NOME de uma
    # consulta do relatório e pode aparecer; CPF, CNPJ e nome de dono, não.
    pessoais = [x for x in (r"CPFs?", r"CNPJs?", r"\d{3}\.\d{3}\.\d{3}-\d{2}",
                            r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", r"(?i)nome do (?:propriet|dono|titular)")
                if re.search(x, html)]
    conferir("R09_PAGINA_SEM_DADO_PESSOAL", not pessoais, f"a página mostra dado de pessoa: {pessoais}")
    conferir("R09_PAGINA_NAO_FICA_GUARDADA",
             pagina.headers.get("cache-control") == "no-store"
             and pagina.headers.get("referrer-policy") == "no-referrer",
             "a página pode ficar guardada ou vazar o endereço")

    limpar_limite()
    ruim = cliente.get("/validar", params={"codigo": emitido["codigo"][:-1] + "Z"})
    conferir("R08_CODIGO_ALTERADO_NA_PAGINA",
             "Não confere" in ruim.text and CAR not in ruim.text,
             "código alterado não foi recusado na página")
    limpar_limite()
    curto = cliente.get("/validar", params={"codigo": "ABC-123"})
    conferir("R08_CODIGO_INCOMPLETO_TEM_PISTA_HONESTA",
             "incompleto" in curto.text and "Não confere" in curto.text, "sem pista para quem digitou")

    limpar_limite()
    bloqueado = None
    for _ in range(validacao_publica.LIMITE_MINUTO + 2):
        bloqueado = cliente.get("/validar", params={"codigo": "0" * 54})
    conferir("R07_LIMITE_DE_TENTATIVAS",
             bloqueado is not None and bloqueado.status_code == 429
             and bloqueado.headers.get("retry-after"),
             f"a varredura não foi barrada: {getattr(bloqueado, 'status_code', None)}")
    outra = cliente.get("/validar", params={"codigo": emitido["codigo"]},
                        headers={"x-forwarded-for": "198.51.100.7"})
    conferir("R07_OUTRA_ORIGEM_NAO_PAGA_A_CONTA", outra.status_code == 200,
             "o limite de uma origem bloqueou outra")
    inventado = cliente.get("/validar", params={"codigo": "0" * 54},
                            headers={"x-forwarded-for": "203.0.113.9, 198.51.100.7"})
    conferir("R07_ENCAMINHAMENTO_INVENTADO_NAO_TROCA_A_ORIGEM",
             inventado.status_code in (200, 429)
             and validacao_publica._origem(_pedido_falso("203.0.113.9, 198.51.100.7")) == "198.51.100.7",
             "o endereço inventado pelo visitante virou a origem")

    limpar_limite()
    json_ok = cliente.get(f"/v1/publico/validar/{emitido['codigo']}")
    conferir("R08_RESPOSTA_DE_MAQUINA",
             json_ok.status_code == 200 and json_ok.json().get("car_code") == CAR
             and "motivo" not in json_ok.json(),
             f"resposta de máquina fora do contrato: {json_ok.status_code}")
    json_ruim = cliente.get("/v1/publico/validar/" + "0" * 54)
    conferir("R08_RESPOSTA_DE_MAQUINA_NAO_ENTREGA_O_MOTIVO",
             json_ruim.status_code == 404 and "motivo" not in json_ruim.json()
             and json_ruim.json().get("estado") == "nao_confere",
             f"resposta de máquina vazou o motivo: {json_ruim.text[:160]}")
    conferir("R06_SEGREDO_NAO_VAI_PARA_A_PAGINA",
             SEGREDO not in html and SEGREDO not in json_ok.text, "segredo na resposta")

    com_segredo(None)
    limpar_limite()
    indisponivel = cliente.get(f"/validar/{emitido['codigo']}")
    conferir("R05_SEM_SEGREDO_A_PAGINA_NAO_ACUSA",
             "Não confere" not in indisponivel.text
             and "não está disponível" in indisponivel.text
             and "autêntico" not in indisponivel.text.lower(),
             "sem segredo a página deu veredito")
    conferir("R05_RESPOSTA_DE_MAQUINA_SEM_SEGREDO",
             cliente.get(f"/v1/publico/validar/{emitido['codigo']}").status_code == 503,
             "sem segredo a resposta de máquina não disse indisponível")
    com_segredo(SEGREDO)

    regras_de_texto(html)


class _pedido_falso:
    def __init__(self, encaminhado: str):
        self.headers = {"x-forwarded-for": encaminhado}
        self.client = None


def regras_de_texto(html: str) -> None:
    """R10: os textos novos passam no mesmo lint de cliente do portão T2.

    O lint vem de lá, importado, e não copiado: duas listas da mesma regra em arquivos
    diferentes é como uma delas envelhece sem ninguém notar.
    """
    import t2_textos_cliente_gate as t2

    t2._carregar_regra_de_erro()
    frases = t2.frases_do_cliente(html, "/validar")
    achados = t2.lint(frases)
    conferir("R10_TEXTO_DE_CLIENTE_SEM_JARGAO", not achados, f"{achados}")
    conferir("R10_O_LINT_FOI_MESMO_APLICADO", len(frases) >= 8,
             f"só {len(frases)} frases extraídas da página — o extrator não viu o texto")

    # O mesmo lint sobre o texto NOVO que vai impresso no relatório: o portão T2 renderiza
    # o PDF sem estes blocos, então sem isto nada mede o que o cliente lê no papel.
    import foto_limpa_no_relatorio as desenho
    import report_validacao_pdf as bloco
    import sentinel_limpo as foto
    import validacao_publica

    legenda = foto.legenda_de_verdor("12/10/2026")
    frases_pdf = [
        bloco.TITULO,
        bloco.EXPLICACAO.format(endereco="raioxterritorial.com.br/validar"),
        foto.texto_da_cena({"ok": True, "data_cena_br": "12/10/2026", "nuvem_pct": 0.1,
                            "aviso": foto._aviso(410, 22.0, True, True)}),
        desenho.PENDENTE,
        validacao_publica._RODAPE,
        legenda["nota"],
        legenda["sem_leitura"]["rotulo"],
    ] + [x["rotulo"] for x in legenda["faixas"]] + [
        foto.pendente("x")["mensagem"],
    ]
    achados_pdf = t2.lint(frases_pdf)
    conferir("R10_TEXTO_IMPRESSO_SEM_JARGAO", not achados_pdf, f"{achados_pdf}")


def main() -> int:
    artefatos = regras_da_foto()
    regras_do_desenho(artefatos)
    regra_do_caminho_das_imagens()
    regras_do_codigo()
    regras_do_pdf()
    regras_da_pagina()
    if FALHAS:
        print(f"RX_R3_FOTO_VALIDADOR_GATE=FAIL falhas={len(FALHAS)}")
        return 1
    print("RX_R3_FOTO_VALIDADOR_GATE=PASS foto_limpa:on validador:on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
