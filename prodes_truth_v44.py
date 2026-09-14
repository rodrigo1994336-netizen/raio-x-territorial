from __future__ import annotations

from prodes_reading_f2 import apply_reading_to_report_payload

# Apresentação antiga do PRODES (V44): "Histórico PRODES completo" / "Recorte pós-31/07/2019"
# com "ha únicos", "% do CAR" em 4 casas, anos soltos e "Interseção geométrica exata" somando
# faixas de divisa. Nenhum destes rótulos ou trechos pode sobreviver nas linhas do PRODES do
# relatório; o gate F2 (scripts/f2_prodes_leitura_gate.py) confere isso no payload gerado.
LEGACY_ROW_MARKERS = (
    'Histórico PRODES completo',
    'Recorte pós-31/07/2019',
    'ha únicos',
    '% do CAR •',
    'Interseção geométrica exata com o CAR + união das geometrias por lente',
    'Ocorrências exatas',
    'Área intersectada',
    'Imagem',
)


def patch_prodes_lens(payload:dict,result:dict):
    """Leitura única do PRODES no relatório (F2).

    A contagem, a área e o risco usam só o desmatamento dentro do imóvel; faixas de divisa
    mais estreitas que um pixel de 30 m aparecem numa linha à parte; máscara acumulada nunca
    vira ocorrência anual; o recorte pós-31/07/2019 continua separado. A mesma função
    (prodes_reading_f2) alimenta o resumo que o portal recebe.
    """
    return apply_reading_to_report_payload(payload,result)


def install():
    import live_report_adapter_v9 as v9
    import live_report_adapter_v11 as v11
    v9._patch_prodes_lens=patch_prodes_lens
    v11._patch_prodes_lens=patch_prodes_lens
    print('RX_PRODES_TRUTH_V44=exact_union_two_lenses f2_inside_only_30m_pixel',flush=True)


install()
