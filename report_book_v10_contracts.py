"""Contrato central do relatório-livro v10 (SPEC_RELATORIO_LIVRO §9.2 e §9.3).

Escrito primeiro (Onda 0, parte P0). Só tipos e constantes: nenhum import de reportlab, nenhuma
rede, nenhuma leitura de payload. Quem implementa lê daqui; quem muda um tipo muda aqui e avisa as
partes que consomem.

Duas camadas de dado:
- Fatos `_v51` no payload (envelope do PLANO_PARIDADE §3.1), um contrato só para o que o livro afirma;
- `payload["book_v10"]`, só desenho (geometria projetada, imagens limpas, recortes das manchas).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, get_args

# --------------------------------------------------------------------------------------------------
# Estados, perfis e tipos de figura
# --------------------------------------------------------------------------------------------------

# Os cinco estados do envelope Fato do PLANO_PARIDADE §3.1 — um contrato só.
Estado = Literal["encontrado", "nao_encontrado", "pendente", "fora_desta_edicao", "nao_se_aplica"]
Perfil = Literal["qualquer", "comprar", "financiar", "produzir"]
FiguraTipo = Literal["foto", "lista", "mapa", "icones", "diagrama", "corte", "imagens",
                     "rampas", "grafico", "numeros", "tabela", "tipografico"]
Significado = tuple[tuple[Optional[str], str], ...]   # (perfil em texto ou None, frase)

ESTADOS: tuple[str, ...] = get_args(Estado)
PERFIS: tuple[str, ...] = get_args(Perfil)
FIGURA_TIPOS: tuple[str, ...] = get_args(FiguraTipo)


class BookAssetsMissing(RuntimeError): ...
class BookPayloadMissing(RuntimeError): ...          # payload sem os Fatos _v51 do núcleo nem book_v10.schema >= 1
class LayoutError(RuntimeError): ...
class RelatorioIndisponivel(RuntimeError): ...       # render_or_fallback sem v10 e sem ONDA0_APLICADA: a rota pede "tente de novo"


# --------------------------------------------------------------------------------------------------
# Modelo em memória (lido por report_book_model.construir_modelo)
# --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fato:
    """Leitura, na memória, do envelope `_v51` (ou da regra de transição, seção 2.0.2)."""
    id: str                      # "prodes", "embargo_ibama", "ti", "outorga"... (FATO_CHAVES abaixo)
    estado: Estado
    atencao: bool
    valor: Optional[str]         # já formatado por report_book_fmt; sempre None em "pendente"
    frase: str                   # frase curta do quadro de situação, já com esc() nos pedaços vindos de dado
    fonte_publica: Optional[str] # fonte.nome_publico do envelope (ou chave de report_book_texts.FONTES)
    data_base: Optional[str]     # data da base da fonte, dd/mm/aaaa
    consultado_em: Optional[str] # "13/09/2026 18h52" (Brasília)
    limites: tuple[str, ...]     # alcance escrito da consulta ("só embargos desenhados no mapa")
    origem: Literal["v51", "transicao"]
    capitulo: Optional[str]      # chave de FolhaSpec (para RefPagina)
    # "motivo" do envelope fica fora de propósito: é interno e nunca é impresso.


@dataclass(frozen=True)
class Imagem:
    ok: bool
    kind: Literal["esri_tiles", "cbers4a_wpm", "sentinel2", "ndvi", "planta"]
    enquadramento: Literal["vizinhanca", "medio", "planta"]   # seção 1.7
    path: Optional[str]; path_muted: Optional[str]
    crs: Optional[str]; bounds_m: Optional[tuple[float, float, float, float]]
    px: Optional[tuple[int, int]]; data: Optional[str]; resolucao_m: Optional[float]
    legenda: str; credito: Optional[str]


@dataclass(frozen=True)
class Geometria:
    crs: str                                  # "EPSG:31983"
    car_m: dict                               # GeoJSON projetado e simplificado (tolerância 1 m)
    bbox_m: tuple[float, float, float, float]
    centroide_lonlat: tuple[float, float]
    lado_menor_m: float
    uf: str


@dataclass(frozen=True)
class Identidade:
    titulo: str; titulo_e_nome: bool; codigo_car: str; explicacao_titulo: Optional[str]
    localizacao: str; localizacao_legenda: str
    area: str; area_legenda: Optional[str]
    tamanho: Optional[str]; tamanho_legenda: Optional[str]
    cadastro: Optional[str]; cadastro_legenda: Optional[str]
    centro: Optional[str]
    emitido_data: str; emitido_hora: str; report_id: str
    retrato: Imagem                           # kind "planta" quando for a planta


@dataclass(frozen=True)
class Resposta:
    ordem: int; icone: str; pergunta: str; fatos: tuple[str, ...]
    estado: Optional[Estado]; atencao: bool
    texto_longo: str; texto_curto: str; capitulo: str


@dataclass(frozen=True)
class ItemConferencia:
    perfil: Perfil; o_que: str; por_que: str; onde: str; gatilho: str; ordem: int


@dataclass(frozen=True)
class Cabeca:
    n: int; rotulo: str; icone: str; pergunta: str; resposta: str
    estado: Optional[Estado]; atencao: bool; nota_fonte: str
    destaques: tuple[tuple[str, str, str], ...] = ()     # (valor, unidade, legenda); nunca zero


@dataclass(frozen=True)
class OcorrenciaProdes:
    ano: int; data_imagem: str; periodo: str
    classe: Literal["dentro", "divisa"]; largura_m: Optional[int]
    area: str; pct: str; area_mancha: Optional[str]
    periodo_legal: Literal["ate_2008_07_22", "ate_2019_07_31", "depois_2019_07_31"]
    parte_da_mancha: Optional[str]; quase_inteira_fora: bool   # razão mancha ÷ parte interna ≥ 10
    geom_dentro_m: Optional[dict]; geom_mancha_m: Optional[dict]


@dataclass(frozen=True)
class CapDesmatamento:
    cabeca: Cabeca; ocorrencias: tuple[OcorrenciaProdes, ...]; frase_mancha: Optional[str]
    significado: Significado; limite: str


@dataclass(frozen=True)
class Achado:
    tipo: Literal["embargo", "anm", "sobreposicao"]; colunas: tuple[str, ...]
    linha: tuple[str, ...]; geom_m: Optional[dict]


@dataclass(frozen=True)
class CapRestricoes:
    cabeca: Cabeca; icones: tuple[Fato, ...]; linhas_fiscalizacao: tuple[Fato, ...]
    achados: tuple[Achado, ...]; nota_margem: str; significado: Significado; limite: str


@dataclass(frozen=True)
class CapDocumentos:
    cabeca: Cabeca; documentos: tuple[tuple[str, str, str, Optional[Fato]], ...]  # nome, responde, neste relatório, fato
    ficha: tuple[tuple[str, str, Optional[str]], ...]                             # rótulo, valor, explicação
    referencia: Optional[str]; composicao: Optional[tuple[tuple[str, str, str], ...]]
    qr_url: Optional[str]; significado: Significado; limite: str


@dataclass(frozen=True)
class CapAgua:
    cabeca: Cabeca; outorgas: tuple[tuple[tuple[str, str], ...], ...]; outorgas_total: int
    pivo: Optional[Fato]; poco: Optional[dict]           # {"profundidade": (m, n), "estatico": (m, n), "dinamico": (m, n)}
    texto_pocos: Optional[str]; usos: tuple[tuple[str, int], ...]; aquifero: Optional[str]
    vizinhos: Optional[str]; significado: Significado


@dataclass(frozen=True)
class CapTerraUso:
    cabeca: Cabeca; imagem_a: Optional[Imagem]; imagem_b: Optional[Imagem]
    verdor: Optional[tuple[int, int, int]]; texto_verdor: Optional[str]
    uso: tuple[tuple[str, int, str, str], ...]           # (classe, pct, ha, token de cor)
    frase_nativa: Optional[str]; lotacao: str; significado: Significado


@dataclass(frozen=True)
class CapTerraChao:
    pergunta: str; resposta: str; rampas: tuple[tuple[str, int, float], ...]  # (rótulo, pct, grau limite)
    texto_inclinacao: Optional[str]; textura: Optional[tuple[int, int, int]]; texto_solo: Optional[str]
    texto_aptidao: Optional[str]; erosao: Optional[str]; limite_aptidao: Optional[str]; significado: Significado


@dataclass(frozen=True)
class CapClima:
    cabeca: Cabeca; meses_mm: tuple[int, ...]; meses_temp: tuple[int, ...]
    faixa_chuva: tuple[int, ...]; faixa_seca: tuple[int, ...]; janela_meses: tuple[int, ...]
    observado: Optional[str]; normal: Optional[str]; texto_janela: Optional[str]
    texto_temperatura: Optional[str]; significado: Significado


@dataclass(frozen=True)
class CapEntorno:
    cabeca: Cabeca; numeros: tuple[tuple[str, str, str], ...]
    lavouras_ano: Optional[int]; lavouras: tuple[tuple[str, str, str, float], ...]  # cultura, ha, t, fração
    linhas: tuple[tuple[str, str], ...]; significado: Significado


@dataclass(frozen=True)
class Apendice:
    tabelas: tuple[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], Optional[str], Optional[str]], ...]
    # (título, cabeçalho, linhas, nota, capítulo de origem)


@dataclass(frozen=True)
class LinhaFonte:
    grupo: str; nome: str; respondeu: str; data_ref: Optional[str]; consultada: Optional[str]; estado: Estado


@dataclass(frozen=True)
class LivroModelo:
    identidade: Identidade; geometria: Optional[Geometria]
    resumo_longo: str; resumo_curto: str; painel: tuple[Resposta, ...]
    conferencia: tuple[ItemConferencia, ...]; nao_responde: str
    desmatamento: CapDesmatamento; restricoes: CapRestricoes; documentos: CapDocumentos; agua: CapAgua
    terra_uso: Optional[CapTerraUso]; terra_chao: Optional[CapTerraChao]
    clima: Optional[CapClima]; entorno: Optional[CapEntorno]
    apendice: Apendice; fontes: tuple[LinhaFonte, ...]; fora_desta_edicao: tuple[str, ...]
    fatos: dict[str, Fato]; valores_unicos: dict[str, str]
    creditos: tuple[str, ...]; contato: Optional[str]


# --------------------------------------------------------------------------------------------------
# Composição (compositor e motor)
# --------------------------------------------------------------------------------------------------

@dataclass
class Bloco:
    flowable: object                       # reportlab Flowable
    papel: Literal["cabeca", "figura", "texto", "caixa", "nota", "tabela", "lista", "rodape"]
    prioridade: int = 0                    # 0 obrigatório; 1..9 opcional (9 sai primeiro)
    largura_mm: float = 170.0
    mola: Optional[tuple[float, float]] = None   # (min_mm, max_mm) da figura elástica
    destino_apendice: Optional[str] = None       # chave do bloco no apêndice, se sair
    titulo: bool = False                   # para a regra "nenhum título nos últimos 45 mm"
    selos: tuple[tuple[str, Estado], ...] = ()   # (fato_id, estado) desenhados, para AC-08


@dataclass
class FolhaSpec:
    chave: str; capitulo: Optional[str]; titulo_corrente: str; figura_tipo: FiguraTipo
    blocos: list[Bloco]; pode_dividir: bool = False; corpo: bool = True
    ancorar_rodape: Optional[Bloco] = None


@dataclass
class Plano:
    folhas: list                            # Folha (flowable), 1 por página
    pagina_de: dict[str, int]; total: int
    ocupacao: dict[int, float]; figura_tipo: dict[int, str]
    blocos_movidos: list[tuple[str, str]]; selos: list[tuple[int, str, Estado]]
    textos_por_pagina: dict[int, list[str]]   # para glossário e portões
    frases: list[tuple[int, str, tuple[str, ...], bool]]   # (página, texto, fato_ids, fixo) para AC-07


# --------------------------------------------------------------------------------------------------
# Contrato do payload (§9.3): envelope `_v51` e `book_v10`
# --------------------------------------------------------------------------------------------------

ENVELOPE_V51_CAMPOS: tuple[str, ...] = ("estado", "valor", "fonte", "data_base", "consultado_em", "limites")
# Opcionais do envelope. `motivo` é interno (nunca impresso); `unidade` e `texto_base` descrevem o valor
# e a frase-base conferida (a F1 os traz sempre; as faixas podem omitir).
ENVELOPE_V51_OPCIONAIS: tuple[str, ...] = ("motivo", "unidade", "texto_base")
ENVELOPE_V51_EXTRAS: dict[str, tuple[str, ...]] = {"water.grants_v51": ("vizinhas_5km",)}
FONTE_CAMPOS: tuple[str, ...] = ("id", "nome_publico")
FONTE_OPCIONAIS: tuple[str, ...] = ("licenca", "citacao", "somente_apendice")   # somente_apendice: D9
MOTIVOS: tuple[str, ...] = ("timeout", "http", "excecao_ogc", "corpo_vazio", "truncado", "sem_snapshot",
                            "fora_cobertura", "licenca_off")

# fato_id -> caminho do envelope no payload (pontos separam chaves). Tabela 2.0.2 + §9.3, com as
# leituras corrigidas da F1: `relevo` (Copernicus DEM, classes Embrapa em %) substitui `srtm`;
# `textura_solo` (MapBiomas Solo) é novo; `soilgrids` fica como segunda opinião.
FATO_CHAVES: dict[str, str] = {
    "car": "car.registration_date_v51",
    "car_ultima_alteracao": "car.last_change_v51",
    "composicao_car": "car.composition_v51",
    "sigef": "land.sigef_v51",
    "snci": "land.snci_v51",
    "prodes": "environment.prodes.occurrences_v51",
    "bioma": "environment.biome_v51",
    "embargo_ibama": "environment.embargos_v51.ibama",
    "embargo_icmbio": "environment.embargos_v51.icmbio",
    "autos": "enforcement.autos_v51",
    "ti": "environment.territorial_v51.ti",
    "uc": "environment.territorial_v51.uc",
    "quilombola": "environment.territorial_v51.quilombola",
    "assentamento": "environment.territorial_v51.assentamento",
    "floresta_publica": "environment.territorial_v51.floresta_publica",
    "anm": "mining.processes_v51",
    "regra_credito": "rules.credit_prodes_cmn_v51",
    "alertas_mensais": "environment.deforestation_alerts_v51",
    "sitio_arqueologico": "infrastructure.iphan_v51",
    "outorga": "water.grants_v51",
    "pocos": "water.wells_v51",
    "pivo": "water.pivots_v51",
    "climatologia": "water.climatology_v51",
    "janela_chuva": "water.drought_screening_v51",
    "sentinel": "satellite_imagery.cover_v51",
    "mapbiomas": "agropecuaria.landcover_v51",
    "relevo": "productive.relief_v51",
    "solo_mapa": "productive.soil_map_v51",
    "textura_solo": "productive.soil_texture_v51",
    "soilgrids": "productive.soilgrids_v51",
    "aptidao": "productive.aptitude_v51",
    "erosao": "productive.erosion_v51",
    "ppm": "agropecuaria.livestock_v51",
    "safras": "agropecuaria.safras_v51",
    "sif": "infrastructure.sif_v51",
    "armazens": "infrastructure.warehouses_v51",
    "aerodromo": "infrastructure.airports_v51",
}
# Chaves que ainda não estão na tabela §9.3 nem no PLANO §3.2: propostas pela P0 para carregar as
# leituras corrigidas sem reescrever os campos antigos (que o gerador atual e o portal ainda usam).
CHAVES_PROPOSTAS_P0: frozenset[str] = frozenset({
    "water.wells_v51", "water.pivots_v51", "water.climatology_v51", "agropecuaria.landcover_v51",
    "productive.relief_v51", "productive.soil_map_v51", "productive.soil_texture_v51", "productive.soilgrids_v51",
    "productive.aptitude_v51", "productive.erosion_v51", "agropecuaria.livestock_v51",
})
# Fatos sem envelope de propósito: o modelo decide sozinho (sempre fora_desta_edicao).
FATOS_SEMPRE_FORA: tuple[str, ...] = ("matricula", "ccir", "embargos_estaduais")

# Campos que nunca entram em Fato, book_v10, cache ou log (PLANO §3.1 + SPEC §9.3), comparados sem
# diferença de maiúsculas, como nome exato de chave.
CAMPOS_PROIBIDOS: frozenset[str] = frozenset({
    "cpf", "cnpj", "cpf_cnpj", "cd_cnpj_cpf", "cpfcnpj_4", "emp_nu_cpfcnpj", "emp_nm_responsavel",
    "emp_nm_empreendimento", "empto_4", "nome_armazenador", "email", "endereco", "rt", "art",
    "cod_profissional_credenciado", "num_processo", "no_logradouro", "dt_sync",
    "razao_social", "nome_fantasia", "telefone", "autuado", "titular", "responsavel",
})

PRODES_CLASSES: tuple[str, ...] = ("dentro", "divisa")
PRODES_PERIODOS_LEGAIS: tuple[str, ...] = ("ate_2008_07_22", "ate_2019_07_31", "depois_2019_07_31")
PRODES_MARCO_2008 = "2008-07-22"
PRODES_MARCO_2019 = "2019-07-31"
# Três condições juntas para "divisa" (SPEC §0.3 item 1); fora disso, "dentro".
PRODES_DIVISA_LARGURA_MAX_M = 30.0      # largura média da parte interna < 30 m
PRODES_DIVISA_PARTE_MAX_PCT = 10.0      # parte interna < 10% da mancha inteira
PRODES_DIVISA_FAIXA_MIN_PCT = 90.0      # ≥ 90% da parte interna na faixa de 30 m do limite
PRODES_QUASE_INTEIRA_FORA_RAZAO = 10.0  # feature_area_ha ÷ area_inside_ha ≥ 10

BOOK_V10_SCHEMA = 2
BOOK_V10_IMAGEM_MAX_BYTES = 120_000     # imagens das fixtures (SPEC §8.1)
BOOK_V10_CAIXA_VIZINHANCA_MIN_M = 2000.0
BOOK_V10_RECORTE_MEDIO_MIN_M = 1000.0
