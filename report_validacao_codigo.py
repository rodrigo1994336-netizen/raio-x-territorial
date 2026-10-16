"""Código de autenticidade do relatório: assinado, autoverificável, sem banco de dados.

O concorrente SpectraX publica um validador de relatório por código. Aqui o código é
*autocontido*: ele carrega os campos que a página mostra e uma assinatura HMAC sobre
esses mesmos campos. Não existe banco, não existe consulta, não existe custo — quem
confere recalcula a assinatura com o segredo do ambiente e compara.

Consequência aceita de propósito: o código é longo (54 letras e números, em 9 grupos de
6), porque ele carrega o código do CAR inteiro. Guardar só um pedaço do CAR deixaria a
página mostrar um CAR truncado, e a primeira regra do dono é nunca transmitir informação
errada. Quem lê usa o QR; quem digita tem grupos curtos e um alfabeto sem letra ambígua.

Sem segredo configurado (``RX_VALIDACAO_SEGREDO``), nada é prometido: o relatório não
imprime código e a página diz que a conferência não está disponível nesta instalação.
Jamais o contrário — um código ausente nunca vira "inválido", e um código que não pode
ser conferido nunca vira "autêntico".

Formato 1, contrato congelado (qualquer mudança nos campos, na ordem das fontes ou na
lista de UFs exige formato 2, senão código antigo passa a ser lido com significado novo):

    bits  3  reservado (sempre zero)
    bits  4  formato (=1)
    bits  5  UF (índice em UFS, 1..27)
    bits 24  município IBGE (7 dígitos)
    bits 128 parte final do código do CAR (32 dígitos hexadecimais)
    bits 26  minuto da emissão, contado de 2020-01-01T00:00Z
    bits 16  versão do relatório (maior 4 | menor 8 | correção 4; 0xFFFF = não informada)
    bits  1  a lista de consultas pendentes está registrada neste código
    bits 15  consultas sem resposta (bit i = fonte i de FONTES; bit 14 = outras)
    bits 48  assinatura HMAC-SHA256 truncada
    ----
    bits 270 = 54 letras do alfabeto de 32 símbolos
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone

# Horário de Brasília e a nota de fuso vêm de quem já é dono deles no relatório: duas
# cópias da mesma conversão é como o PDF e a tela passam a discordar.
from report_ptbr_v50 import BRT, BRT_NOTE

FORMATO = 1
TAMANHO = 54
GRUPO = 6
# Alfabeto de Crockford: sem I, L, O nem U, para ninguém confundir 1/I/L, 0/O e não formar palavra.
ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALOR = {ch: i for i, ch in enumerate(ALFABETO)}
# Quem digita "I" quis dizer 1, "L" quis dizer 1 e "O" quis dizer 0.
_CONFUSAO = {"I": "1", "L": "1", "O": "0"}

SEGREDO_ENV = "RX_VALIDACAO_SEGREDO"
SEGREDO_MINIMO = 24
BASE_URL_ENV = "RX_VALIDACAO_BASE_URL"
# O domínio no ar. RX_PUBLIC_BASE_URL não entra aqui de propósito: ela ainda guarda o
# endereço antigo do Render e isso ficaria impresso em documento que o cliente arquiva.
BASE_URL_PADRAO = "https://raioxterritorial.com.br"
CAMINHO = "/validar"

EPOCA = datetime(2020, 1, 1, tzinfo=timezone.utc)
_MINUTO_MAX = (1 << 26) - 1
VERSAO_NAO_INFORMADA = 0xFFFF

UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT",
    "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)

# Catálogo congelado das consultas que o relatório pode deixar pendentes. A posição de
# cada uma é o número do bit: acrescentar fonte no meio muda o significado de código já
# emitido, por isso o portão compara o resumo do contrato com um valor fixo.
FONTES: tuple[tuple[str, str], ...] = (
    ("car", "Cadastro Ambiental Rural (CAR)"),
    ("embargo", "Áreas embargadas do IBAMA"),
    ("prodes", "Desmatamento mapeado pelo INPE"),
    ("anm", "Processos minerários da Agência Nacional de Mineração"),
    ("registro", "Matrícula e titularidade no cartório de registro de imóveis"),
    ("terra_indigena", "Terra indígena"),
    ("reserva_legal", "Reserva legal"),
    ("unidade_conservacao", "Unidade de conservação"),
    ("floresta_publica", "Floresta pública"),
    ("snci", "Certificação do imóvel no INCRA"),
    ("mte_trabalho_escravo", "Lista de trabalho análogo ao escravo do Ministério do Trabalho"),
    ("sinaflor", "Autorização para cortar vegetação, no IBAMA"),
    ("outorga_agua", "Outorgas de água"),
    ("clima", "Clima e chuva"),
)
FONTES_IDS = tuple(fid for fid, _ in FONTES)
BIT_OUTRAS = 14
BITS_PENDENCIA = 15

# Como o relatório de hoje nomeia cada fonte na tabela de situação. A tradução é
# declarada aqui, num lugar só; rótulo que não estiver nesta tabela e estiver pendente
# acende o bit "outras" — nunca desaparece em silêncio.
ROTULOS_DO_RELATORIO: tuple[tuple[str, str], ...] = (
    ("SICAR", "car"),
    ("CAR", "car"),
    ("IBAMA — áreas embargadas", "embargo"),
    ("INPE / TerraBrasilis / PRODES", "prodes"),
    ("ANM / SIGMINE", "anm"),
    ("Registro de imóveis", "registro"),
    ("Terra indígena", "terra_indigena"),
    ("Reserva legal", "reserva_legal"),
    ("Unidade de conservação", "unidade_conservacao"),
    ("Floresta pública", "floresta_publica"),
    ("INCRA", "snci"),
    ("Ministério do Trabalho", "mte_trabalho_escravo"),
    ("SINAFLOR", "sinaflor"),
    ("Outorga", "outorga_agua"),
    ("Clima", "clima"),
)

_CAR_RE = re.compile(r"^([A-Z]{2})-([0-9]{7})-([0-9A-F]{32})$")
_VERSAO_RE = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})")
_PREFIXO_RE = re.compile(r"^\s*RX\s*[-:]\s*", re.IGNORECASE)
_LIMPA_RE = re.compile(r"[^0-9A-Za-z]+")
# Teto de entrada: a página não gasta tempo com texto grande mandado de propósito.
ENTRADA_MAXIMA = 200


def digest_do_contrato() -> str:
    """Resumo do que o formato 1 promete. Mudou aqui, muda o significado de código já emitido."""
    partes = [
        f"formato={FORMATO}",
        f"tamanho={TAMANHO}",
        f"alfabeto={ALFABETO}",
        "ufs=" + ",".join(UFS),
        "fontes=" + ",".join(FONTES_IDS),
        f"bit_outras={BIT_OUTRAS}",
        f"bits_pendencia={BITS_PENDENCIA}",
        "campos=reservado3,formato4,uf5,municipio24,car128,minuto26,versao16,pendflag1,pend15,mac48",
        "epoca=2020-01-01T00:00:00Z",
    ]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()


def segredo() -> bytes | None:
    """O segredo do ambiente, ou None. Nunca é impresso, registrado nem devolvido."""
    bruto = (os.environ.get(SEGREDO_ENV) or "").strip()
    if len(bruto) < SEGREDO_MINIMO:
        return None
    return bruto.encode("utf-8")


def validacao_disponivel() -> bool:
    return segredo() is not None


def base_url() -> str:
    return (os.environ.get(BASE_URL_ENV) or BASE_URL_PADRAO).rstrip("/")


def url_de_validacao(codigo: str) -> str:
    return f"{base_url()}{CAMINHO}/{codigo}"


def rotulo_da_fonte(fonte_id: str) -> str:
    for fid, rotulo in FONTES:
        if fid == fonte_id:
            return rotulo
    return fonte_id


def formatar(codigo: str) -> str:
    return "-".join(codigo[i:i + GRUPO] for i in range(0, len(codigo), GRUPO))


def _mac(dados: int) -> int:
    chave = segredo()
    if chave is None:
        raise RuntimeError("validacao_sem_segredo")
    corpo = b"RX-VALIDACAO-V1\x00" + dados.to_bytes(28, "big")
    return int.from_bytes(hmac.new(chave, corpo, hashlib.sha256).digest()[:6], "big")


def _codificar(valor: int) -> str:
    saida = []
    for posicao in range(TAMANHO - 1, -1, -1):
        saida.append(ALFABETO[(valor >> (5 * posicao)) & 31])
    return "".join(saida)


def normalizar(bruto: str) -> str | None:
    """Aceita o código com ou sem prefixo, espaço, traço, minúscula ou letra confundida."""
    if not isinstance(bruto, str) or len(bruto) > ENTRADA_MAXIMA:
        return None
    texto = _PREFIXO_RE.sub("", bruto)
    texto = _LIMPA_RE.sub("", texto).upper()
    texto = "".join(_CONFUSAO.get(ch, ch) for ch in texto)
    if len(texto) != TAMANHO or any(ch not in _VALOR for ch in texto):
        return None
    return texto


def _versao_em_bits(versao) -> int:
    achado = _VERSAO_RE.search(str(versao or ""))
    if not achado:
        return VERSAO_NAO_INFORMADA
    maior, menor, correcao = (int(x) for x in achado.groups())
    if maior > 15 or menor > 255 or correcao > 15:
        return VERSAO_NAO_INFORMADA
    bits = (maior << 12) | (menor << 4) | correcao
    return VERSAO_NAO_INFORMADA if bits == VERSAO_NAO_INFORMADA else bits


def _versao_de_bits(bits: int) -> str | None:
    if bits == VERSAO_NAO_INFORMADA:
        return None
    return f"{(bits >> 12) & 15}.{(bits >> 4) & 255}.{bits & 15}"


def _minuto(emitido_em) -> int | None:
    momento = emitido_em
    if isinstance(momento, str):
        texto = momento.strip().replace("Z", "+00:00")
        try:
            momento = datetime.fromisoformat(texto)
        except ValueError:
            return None
    if not isinstance(momento, datetime):
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    minutos = int((momento.astimezone(timezone.utc) - EPOCA).total_seconds() // 60)
    if minutos < 0 or minutos > _MINUTO_MAX:
        return None
    return minutos


def _bits_de_pendencia(fontes_sem_resposta) -> int:
    bits = 0
    for item in fontes_sem_resposta or ():
        texto = str(item)
        if texto in FONTES_IDS:
            bits |= 1 << FONTES_IDS.index(texto)
        else:
            bits |= 1 << BIT_OUTRAS
    return bits


def pendencias_do_relatorio(payload) -> tuple[list[str], bool]:
    """Quais fontes não responderam, lidas da tabela de situação do próprio relatório.

    Devolve (ids conhecidos, houve pendência fora do catálogo). A tabela do relatório
    nomeia as fontes em português, então a tradução é por rótulo — e rótulo que ninguém
    traduz vira "outras consultas pendentes", nunca silêncio.
    """
    conhecidas: list[str] = []
    outras = False
    for linha in (payload or {}).get("sources") or ():
        if not isinstance(linha, dict):
            continue
        estado = str(linha.get("status") or "").upper()
        respondeu = "CONSULTAD" in estado and "NÃO" not in estado and "NAO" not in estado
        if respondeu or "PARCIAL" in estado:
            continue
        nome = str(linha.get("name") or "")
        achado = None
        for rotulo, fonte_id in ROTULOS_DO_RELATORIO:
            if rotulo.casefold() in nome.casefold():
                achado = fonte_id
                break
        if achado is None:
            outras = True
        elif achado not in conhecidas:
            conhecidas.append(achado)
    return conhecidas, outras


def emitir(car_code, emitido_em, versao_relatorio=None, *,
           fontes_sem_resposta=None, registrar_pendencias=False) -> dict:
    """Emite o código do documento. Sem segredo, sem código — e o motivo fica dito."""
    if not validacao_disponivel():
        return {"ok": False, "motivo": "sem_segredo"}
    achado = _CAR_RE.match(str(car_code or "").strip().upper())
    if not achado:
        return {"ok": False, "motivo": "car_fora_do_formato"}
    uf, municipio, parte_final = achado.groups()
    if uf not in UFS:
        return {"ok": False, "motivo": "uf_desconhecida"}
    minuto = _minuto(emitido_em)
    if minuto is None:
        return {"ok": False, "motivo": "emissao_fora_do_alcance"}
    bits_pendencia = _bits_de_pendencia(fontes_sem_resposta) if registrar_pendencias else 0
    if bits_pendencia >> BITS_PENDENCIA:
        return {"ok": False, "motivo": "pendencias_fora_do_formato"}

    dados = 0
    dados = (dados << 3) | 0
    dados = (dados << 4) | FORMATO
    dados = (dados << 5) | (UFS.index(uf) + 1)
    dados = (dados << 24) | int(municipio)
    dados = (dados << 128) | int(parte_final, 16)
    dados = (dados << 26) | minuto
    dados = (dados << 16) | _versao_em_bits(versao_relatorio)
    dados = (dados << 1) | (1 if registrar_pendencias else 0)
    dados = (dados << 15) | bits_pendencia
    codigo = _codificar((dados << 48) | _mac(dados))
    return {
        "ok": True,
        "codigo": codigo,
        "codigo_formatado": formatar(codigo),
        "url": url_de_validacao(codigo),
        "formato": FORMATO,
    }


def _invalido(motivo: str) -> dict:
    return {"ok": False, "estado": "nao_confere", "motivo": motivo}


def conferir(bruto: str) -> dict:
    """Confere um código. Três estados, nunca confundidos entre si.

    ``indisponivel``  não há segredo nesta instalação: não se afirma nada sobre o código.
    ``nao_confere``   o código não foi emitido por nós, ou foi alterado.
    ``autentico``     a assinatura fecha, e os campos devolvidos são os assinados.
    """
    if not validacao_disponivel():
        return {"ok": False, "estado": "indisponivel", "motivo": "sem_segredo"}
    codigo = normalizar(bruto)
    if codigo is None:
        return _invalido("formato_do_codigo")
    valor = 0
    for ch in codigo:
        valor = (valor << 5) | _VALOR[ch]
    dados = valor >> 48
    assinatura = valor & ((1 << 48) - 1)
    if dados >> 219:  # os 3 bits reservados têm de estar em zero
        return _invalido("reservado")
    if not hmac.compare_digest(_mac(dados).to_bytes(6, "big"), assinatura.to_bytes(6, "big")):
        return _invalido("assinatura")

    bits_pendencia = dados & ((1 << 15) - 1)
    registrou = bool((dados >> 15) & 1)
    versao_bits = (dados >> 16) & 0xFFFF
    minuto = (dados >> 32) & _MINUTO_MAX
    parte_final = (dados >> 58) & ((1 << 128) - 1)
    municipio = (dados >> 186) & ((1 << 24) - 1)
    uf_indice = (dados >> 210) & 31
    formato = (dados >> 215) & 15
    if formato != FORMATO:
        return _invalido("formato_desconhecido")
    if not 1 <= uf_indice <= len(UFS) or not 1_000_000 <= municipio <= 9_999_999:
        return _invalido("campos")

    momento = (EPOCA + timedelta(minutes=minuto)).astimezone(BRT)
    pendentes = [FONTES_IDS[i] for i in range(len(FONTES_IDS)) if bits_pendencia >> i & 1]
    saida = {
        "ok": True,
        "estado": "autentico",
        "formato": formato,
        "car_code": f"{UFS[uf_indice - 1]}-{municipio:07d}-{parte_final:032X}",
        "uf": UFS[uf_indice - 1],
        "municipio_ibge": f"{municipio:07d}",
        "emitido_em": momento.strftime("%d/%m/%Y às %H:%M") + BRT_NOTE,
        "emitido_em_iso": momento.isoformat(timespec="minutes"),
        "pendencias_registradas": registrou,
        "pendencias": [rotulo_da_fonte(x) for x in pendentes] if registrou else [],
        "pendencias_outras": bool(registrou and bits_pendencia >> BIT_OUTRAS & 1),
    }
    versao = _versao_de_bits(versao_bits)
    if versao:  # campo vazio não aparece
        saida["versao_relatorio"] = versao
    return saida


print("RX_VALIDACAO_CODIGO=formato1_hmac_autocontido_sem_banco", flush=True)
