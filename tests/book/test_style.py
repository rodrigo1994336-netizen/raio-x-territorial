"""Testes da P3 (estilo e ativos do livro v10).

Rodam com pytest ou direto: ``PYTHONPATH=. python tests/book/test_style.py``.
O teste de tempo do registro de fontes só reprova com ``RX_BOOK_PERF=1`` (máquina
parada ou CI); fora disso ele mede e imprime, porque tempo em máquina ocupada não
prova nada sobre o código.
"""
from __future__ import annotations

import io
import os
import statistics
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import report_book_v10_style as st  # noqa: E402
from report_book_v10_contracts import BookAssetsMissing  # noqa: E402

MILHAR = ("1.500 ha", "cerca de 1.100 mm", "19.200 t", "2.400 ha", "12.000 cabeças")
ESTRAGADO = ("1,50 ha", "cerca de 1,10 mm", "19,20 t", "2,40 ha", "12 cabeças")

# Tabela 1.3 da especificação: token → (fonte, pt, entrelinha, cor)
ESPEC_1_3 = {
    "kicker": ("Sans-Sb", 7.8, 10, "FOREST"), "codigo_titulo": ("Sans-Sb", 20, 23, "INK"),
    "nome_titulo": ("Disp-Sb", 26, 30, "INK"), "codigo_sub": ("Sans", 11, 14, "INK2"),
    "h1": ("Disp-Sb", 24, 28, "INK"), "dek": ("Disp-It", 12.5, 17, "INK2"),
    "resposta": ("Serif", 12.5, 18, "INK"), "h2": ("Sans-Sb", 12.5, 16, "INK"),
    "corpo": ("Serif", 10, 15, "INK"), "corpo_sb": ("Serif-Sb", 10, 15, "INK"),
    "caixa": ("Serif", 10.5, 15.5, "INK"), "painel_pergunta": ("Sans-Sb", 10.5, 13, "INK"),
    "painel_resposta": ("Serif", 9.8, 13.5, "INK"), "fato_valor": ("Disp", 18, 20, "INK"),
    "destaque_valor": ("Disp", 30, 32, "INK"), "legenda": ("Sans", 8.6, 12, "INK2"),
    "tab_cab": ("Sans-Sb", 7.8, 10, "MUTED"), "tab_cel": ("Sans", 9.6, 13, "INK"),
    "tab_num": ("Sans", 9.6, 13, "INK"), "selo_rotulo": ("Sans-Sb", 7.6, 9, "INK"),
    "corrente": ("Sans", 8, 10, "INK2"), "folio": ("Sans-Sb", 8.5, 10, "INK"),
    "rodape_id": ("Sans", 7.5, 9, "MUTED"), "glos_termo": ("Sans-Sb", 9.6, 13, "FOREST"),
    "glos_def": ("Serif", 9.6, 13.5, "INK"), "apend_cel": ("Sans", 8.8, 12, "INK"),
}

# Tabela 1.4 da especificação
ESPEC_1_4 = {
    "PAPER": "#FFFFFF", "INK": "#1E2320", "INK2": "#4A504B", "MUTED": "#767B74", "RULE": "#D8D2C4",
    "CREAM": "#F5F1E7", "CREAM_LINE": "#F6F1E4", "FOREST": "#1F4A36", "TERRA": "#9A5530",
    "TERRA_LIGHT": "#EAD9CB", "PENDING": "#8C9089", "PENDING_TEXT": "#6F736D", "WATER": "#4F7A8C",
    "WATER_LIGHT": "#E6EEF1", "PRODES_ATE_2008": "#D8B48F", "PRODES_ATE_2019": "#B5764A",
    "PRODES_DEPOIS_2019": "#7A3A1C", "SOLO_1": "#E9E1CF", "SOLO_2": "#D9CBB0", "SOLO_3": "#C4B08C",
    "USO_PASTO": "#D9C47E", "USO_LAVOURA": "#B8894F", "USO_AGUA": "#5E8FA3", "USO_NATIVA": "#6B8A5A",
    "USO_OUTROS": "#BFB8A8", "TEX_ARGILA": "#9C6B4E", "TEX_SILTE": "#C9A77F", "TEX_AREIA": "#E6D3AE",
}


def _texto_pdf(dados: bytes) -> list[str]:
    from pypdf import PdfReader

    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(dados)).pages]


# --- G0: ativos -------------------------------------------------------------
def test_fontes_batem_com_sha256sums():
    assert st.conferir_fontes() == []
    assert sum((st.DIR_FONTES / f).stat().st_size for f in st.FONTES.values()) == 2_375_204
    for licenca in ("OFL-SourceSerif4-Adobe-LICENSE.md", "OFL-SourceSans3.txt"):
        texto = (st.DIR_FONTES / licenca).read_text(encoding="utf-8")
        assert "SIL Open Font License, Version 1.1" in texto and "Reserved Font Name" in texto
        assert "\r" not in texto


def test_conferir_fontes_acusa_arquivo_trocado(tmp_path=None):
    import shutil
    import tempfile

    pasta = Path(tmp_path or tempfile.mkdtemp())
    for arq in list(st.FONTES.values()) + ["SHA256SUMS"]:
        shutil.copy(st.DIR_FONTES / arq, pasta / arq)
    (pasta / "SourceSans3-Regular.ttf").write_bytes(b"\x00" * 10)
    original = st.DIR_FONTES
    try:
        st.DIR_FONTES = pasta
        assert st.conferir_fontes() == ["SourceSans3-Regular.ttf: sha256 diferente"]
    finally:
        st.DIR_FONTES = original


def test_registro_uma_vez_e_nomes():
    ms = st.registrar_fontes()
    assert ms > 0 and st.registrar_fontes() == ms
    from reportlab.pdfbase import pdfmetrics

    registrados = set(pdfmetrics.getRegisteredFontNames())
    assert set(st.FONTES) <= registrados
    assert set(st.FONTES) == {"Serif", "Serif-Sb", "Serif-It", "Disp", "Disp-Sb", "Disp-It", "Sans", "Sans-Sb"}


def test_fonte_ausente_levanta_sem_cair_para_helvetica():
    import tempfile

    original_dir, original_ms = st.DIR_FONTES, st._FONTES_MS
    try:
        st.DIR_FONTES, st._FONTES_MS = Path(tempfile.mkdtemp()), None
        try:
            st.registrar_fontes()
        except BookAssetsMissing as exc:
            assert "SourceSans3-Regular.ttf" in str(exc)
        else:
            raise AssertionError("registrar_fontes() sem TTF precisa levantar BookAssetsMissing")
    finally:
        st.DIR_FONTES, st._FONTES_MS = original_dir, original_ms


def test_glifos_do_livro():
    from reportlab.pdfbase import pdfmetrics

    comuns = "→↑≥≤°²₂–—“”•…×·áàâãéêíóôõúçÁÂÃÉÍÓÔÕÚÇ"
    for nome in st.FONTES:
        cmap = pdfmetrics.getFont(nome).face.charToGlyph
        faltam = [ch for ch in comuns if ord(ch) not in cmap]
        assert not faltam, f"{nome} sem {faltam}"
    for nome in ("Sans", "Sans-Sb"):
        cmap = pdfmetrics.getFont(nome).face.charToGlyph
        assert all(ord(ch) in cmap for ch in "●○◌☐✓")


def test_tempo_do_registro_em_processo_novo():
    codigo = "import report_book_v10_style as s; print('MS', s.registrar_fontes())"
    ambiente = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    medidas = []
    for _ in range(3):
        saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, env=ambiente,
                               cwd=str(ROOT), check=True).stdout
        linhas = [linha for linha in saida.splitlines() if linha.startswith("RX_BOOK_FONTS_MS=")]
        assert len(linhas) == 1, saida  # o log sai uma vez por processo
        medidas.append(float(next(linha for linha in saida.splitlines() if linha.startswith("MS ")).split()[1]))
    mediana = statistics.median(medidas)
    print(f"registro de fontes, mediana de 3 processos: {int(mediana)} ms")
    if os.environ.get("RX_BOOK_PERF") == "1":
        assert mediana <= 200, f"registro de fontes {int(mediana)} ms > 200 ms"


# --- estilos e cores --------------------------------------------------------
def test_estilos_da_tabela_1_3():
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT

    for token, (fonte, pt, entrelinha, cor) in ESPEC_1_3.items():
        s = st.ESTILO[token]
        assert (s.fontName, s.fontSize, s.leading) == (fonte, pt, entrelinha), token
        assert s.textColor.hexval() == st.COR[cor].hexval(), token
    for token, s in st.ESTILO.items():
        assert s.fontSize >= st.TAMANHO_MINIMO, token
        assert s.fontName in st.FONTES, token
        assert s.allowWidows == 0 and s.allowOrphans == 0, token
        assert s.alignment == (TA_RIGHT if token == "tab_num" else TA_LEFT), token
    assert (st.ESTILO["kicker"].charSpace, st.ESTILO["tab_cab"].charSpace, st.ESTILO["selo_rotulo"].charSpace) == (1.4, 0.8, 1.0)


def test_cores_da_tabela_1_4():
    assert set(st.HEX) == set(ESPEC_1_4)
    for nome, valor in ESPEC_1_4.items():
        assert st.HEX[nome] == valor and st.COR[nome].hexval().upper() == "0X" + valor[1:].upper(), nome


def test_rampa_do_verdor_igual_ao_sentinel():
    def rgb(c):
        return tuple(int(v * 255 + 0.5) for v in (c.red, c.green, c.blue))

    assert rgb(st.cor_verdor(-0.15)) == (180, 95, 55)
    assert rgb(st.cor_verdor(0.8)) == (45, 175, 75)
    assert rgb(st.cor_verdor(-1)) == (180, 95, 55) and rgb(st.cor_verdor(2)) == (45, 175, 75)


def test_grade_1_1():
    from reportlab.lib.units import mm

    g = st.GRADE
    assert abs(g.margem_esq + g.util_l + g.margem_dir - g.pagina_l) < 1e-6
    assert abs(g.margem_inf + g.util_a + g.margem_sup - g.pagina_a) < 1e-6
    assert abs(g.leitura + g.calha + g.nota - g.util_l) < 1e-6
    assert abs(2 * g.col2 + g.calha - g.util_l) < 1e-6
    assert abs(3 * g.col3 + 2 * g.calha - g.util_l) < 1e-6
    assert abs(4 * g.col4 + 3 * g.calha - g.util_l) < 1e-6
    assert abs(g.us(1) - 2.646 * mm) < 0.01


# --- selo -------------------------------------------------------------------
def test_selo_recusa_numero_e_estado_sem_selo():
    from reportlab.pdfgen.canvas import Canvas

    c = Canvas(io.BytesIO())
    for errado, excecao in ((0, TypeError), (3, TypeError), (None, TypeError),
                            ("fora_desta_edicao", ValueError), ("nao_se_aplica", ValueError), ("ok", ValueError)):
        try:
            st.desenhar_selo(c, 0, 0, errado)
        except excecao:
            pass
        else:
            raise AssertionError(f"selo aceitou {errado!r}")


def test_selo_nao_encontrado_nunca_usa_cor_de_atencao():
    assert st.SELO[st._selo_chave("nao_encontrado", True)][1] == "INK2"
    assert st.SELO[st._selo_chave("pendente", True)][3] == "PENDING_TEXT"
    assert st.SELO[("encontrado", True)][1] == "TERRA" and st.SELO[("encontrado", False)][1] == "INK"


def test_largura_dos_selos_longos():
    # 1.5: "NÃO ENCONTRADO" e "CONSULTA PENDENTE" com círculo e espaço ficam entre 90 e 105 pt.
    for estado in ("nao_encontrado", "pendente"):
        largura = st.largura_selo(estado)
        assert 88 <= largura <= 106, (estado, largura)


# --- Paragrafo e normalizador (AC-39) ---------------------------------------
def test_paragrafo_recusa_numero_cru():
    try:
        st.Paragrafo(1500.0, "corpo")
    except TypeError:
        pass
    else:
        raise AssertionError("Paragrafo aceitou float")


def test_milhar_intacto_sob_normalizador_com_controle_positivo():
    import report_ptbr_v50
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    report_ptbr_v50.install()
    report_ptbr_v50.install()  # idempotente
    livro = [st.Paragrafo(t, "corpo") for t in MILHAR]
    comum = [Paragraph(t, st.ESTILO["corpo"]) for t in MILHAR]
    assert [p.text for p in livro] == list(MILHAR)
    assert [p.text for p in comum] == list(ESTRAGADO), "controle positivo: o normalizador precisa estragar o Paragraph comum"

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build(livro)
    texto = _texto_pdf(buf.getvalue())[0]
    for certo, errado in zip(MILHAR, ESTRAGADO):
        assert certo in texto, certo
        assert errado not in texto, errado

    # A divisão de parágrafo longo mantém a classe, e com ela a marca.
    longo = st.Paragrafo(" ".join(["Área de 1.500 ha com pasto."] * 40), "corpo")
    partes = longo.split(st.GRADE.leitura, 60)
    assert len(partes) == 2 and all(type(p) is st.Paragrafo for p in partes)

    # Texto comum do v9 continua normalizado.
    assert Paragraph("0.3699", st.ESTILO["corpo"]).text == "0,3699"


# --- moldura e modelo de página ---------------------------------------------
class _CanvasGravador:
    def __init__(self):
        self.textos: list[tuple[str, float, float]] = []
        self.linhas = 0

    def __getattr__(self, nome):
        return lambda *a, **k: None

    def drawString(self, x, y, t, **k):
        self.textos.append((t, x, y))

    def drawRightString(self, x, y, t, **k):
        self.textos.append((t, x, y))

    def line(self, *a):
        self.linhas += 1


def test_moldura_p1_so_folio_e_demais_com_cabecalho():
    p1 = _CanvasGravador()
    st.desenhar_moldura(p1, 1, 13, "Ficha", "CAR MG-3120904-…A14D011F · emitido em 13/09/2026")
    assert [t for t, _, _ in p1.textos] == ["1 de 13"] and p1.linhas == 0

    p3 = _CanvasGravador()
    st.desenhar_moldura(p3, 3, 13, "Desmatamento", "CAR MG-3120904-…A14D011F · emitido em 13/09/2026")
    textos = [t for t, _, _ in p3.textos]
    assert textos == ["RAIO-X TERRITORIAL", "Desmatamento", "CAR MG-3120904-…A14D011F · emitido em 13/09/2026", "3 de 13"]
    assert p3.linhas == 1


def test_modelo_pagina_monta_pdf_com_frame_de_170_por_248():
    from reportlab.platypus import BaseDocTemplate, PageBreak

    g = st.GRADE
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=(g.pagina_l, g.pagina_a), leftMargin=g.margem_esq,
                          rightMargin=g.margem_dir, topMargin=g.margem_sup, bottomMargin=g.margem_inf)
    folhas = [SimpleNamespace(titulo_corrente=None), SimpleNamespace(spec=SimpleNamespace(titulo_corrente="A terra"))]
    modelo = st.modelo_pagina(doc, SimpleNamespace(total=2, folhas=folhas), rodape_id="CAR teste · emitido em 13/09/2026")
    frame = modelo.frames[0]
    assert (frame._x1, frame._y1, frame._width, frame._height) == (g.origem_x, g.origem_y, g.util_l, g.util_a)
    assert (frame._leftPadding, frame._rightPadding, frame._topPadding, frame._bottomPadding) == (0, 0, 0, 0)
    doc.build([st.Paragrafo("Primeira folha.", "corpo"), PageBreak(), st.Paragrafo("Segunda folha.", "corpo")])
    p1, p2 = _texto_pdf(buf.getvalue())
    from pypdf import PdfReader

    fontes = {str(f.get_object()["/BaseFont"]).split("+")[-1]
              for pg in PdfReader(io.BytesIO(buf.getvalue())).pages
              for f in pg["/Resources"]["/Font"].values()}
    assert fontes <= {arq[:-4] for arq in st.FONTES.values()}, fontes  # nada de Helvetica/Times
    assert "1 de 2" in p1 and "RAIO-X" not in p1
    assert "2 de 2" in p2 and "A terra" in p2 and "RAIO-X TERRITORIAL" in p2.replace("\n", " ")


if __name__ == "__main__":
    total = 0
    for nome, funcao in list(globals().items()):
        if nome.startswith("test_") and callable(funcao):
            funcao()
            total += 1
            print(f"ok {nome}")
    print(f"BOOK_V10_STYLE_TESTS=PASS count={total}")
