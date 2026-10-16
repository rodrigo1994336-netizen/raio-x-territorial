# Fixtures do relatório-livro v10

Fixtures JSON **sem dado pessoal** do motor `report_book_v10` (SPEC_RELATORIO_LIVRO §8.1). Cada uma traz
os Fatos `_v51` (envelope do PLANO_PARIDADE §3.1) e o `book_v10` (só desenho) do contrato §9.3.
O contrato em código é `report_book_v10_contracts.py`, na raiz do repositório.

## Conferir

```bash
python scripts/book_v10_schema_check.py              # F1 contra o contrato + hash congelado
python scripts/book_v10_schema_check.py --autoteste  # prova que o portão reprova defeitos injetados
cd tests/fixtures/book && sha256sum -c curvelo_corrigido.sha256
```

O portão só usa a biblioteca padrão e roda no Windows e no CI.

## F1 · `curvelo_corrigido.json`

Imóvel `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F` (Curvelo/MG, 14,795 ha, 0,3699 módulo fiscal).

**Dona: P0. Congelada por hash** em `curvelo_corrigido.sha256` (o JSON e as quatro imagens). Ninguém
edita a F1. A P6 gera F2–F8 a partir dela e confere o hash antes. A troca da F1 escrita à mão pela F1
gerada (P4 + faixas A, B e E) é **um PR só**: hash novo, F2–F8 regeneradas e o diff dos textos de ouro
explicado.

### Como foi montada

- **Base:** o payload real do relatório `RX-20260913T215201Z-A14D011F` (13/09/2026, 21:52 UTC).
  Os campos antigos ficam como vieram (o gerador atual e o portal ainda os leem; o livro não).
- **Dado pessoal removido:** `agropecuaria.sif_chain.establishments[].fields` (CPF/CNPJ, razão social,
  endereço, telefone, e-mail). Fica só a categoria (D3).
- **Fatos `_v51` acrescentados à mão**, um por fato do livro. `_fixture.fatos` diz onde cada um mora.
  Cada envelope traz `estado`, `valor`, `unidade`, `fonte` (nome para o apêndice, licença e citação),
  `data_base`, `consultado_em`, `limites` e `texto_base` (a frase-base conferida).
- **`book_v10`:** geometria do CAR em EPSG:31983 (simplificada a 1 m), recortes das manchas do PRODES
  (0,5 m, recortados à caixa da imagem, chave = `id` da ocorrência), módulo fiscal pela conta do CAR
  (40 ha) e quatro imagens **sem texto gravado** em `imagens/curvelo/`:

  | arquivo | enquadramento | caixa | pixels |
  |---|---|---|---|
  | `book_base.jpg` | vizinhança (p. 1 e p. 3) | 2.520 × 2.000 m | 1008 × 800 |
  | `book_base_muted.jpg` | a mesma, esmaecida (fundo do mapa da p. 3) | 2.520 × 2.000 m | 1008 × 800 |
  | `book_s2.jpg` | recorte médio, cor natural (p. 7) | 1.000 × 1.000 m | 800 × 800 |
  | `book_ndvi.jpg` | recorte médio, verdor (p. 7) | 1.000 × 1.000 m | 800 × 800 |

  Todas vêm da cena Sentinel-2 L2A `S2B_23KNV_20260828_0_L2A` (28/08/2026), lida por faixa nas
  bandas B02, B03, B04 e B08, reprojetada para EPSG:31983 com reamostragem bilinear. A escala de cor
  do verdor vai de NDVI −0,15 (bege) a 0,80 (verde). Crédito: "Contém dados modificados do Copernicus
  Sentinel-2 (2026)".

### Decisões do dono aplicadas

| # | como está na F1 |
|---|---|
| D1 | desligado: p. 1 e mapa da p. 3 com Sentinel-2 e vizinhança |
| D2 | ligado: comparação de área pequena abaixo de 5 ha, sem m² |
| D3 | desligado: SIF só pela categoria ("laticínios") |
| D4 | contato só pelo site https://raioxterritorial.com.br |
| D5 | frases legais na versão factual |
| D6 | ligado: `car.composition_v51` encontrado, base de 04/08/2026 |
| D8 | ligado: IPHAN oficial, 2 sítios na vizinhança (6,7 e 8,6 km da divisa), nenhum dentro |
| D9 | ligado: DETER + alerta validado, nenhum; `fonte.somente_apendice = true` (o nome da fonte não entra no corpo) |
| D10 | ligado: SIGEF e SNCI pelo INCRA oficial, nenhum; SIGEF com base de 11/09/2026 |
| D11 | só "Inscrito no CAR em 28/11/2018"; `car.last_change_v51` = `fora_desta_edicao` |
| D12 | a F1 é a base da amostra aprovável |

### Leituras corrigidas (valem sobre o texto antigo da SPEC)

- **PRODES:** 1 ocorrência **dentro** (2006, percebida entre 31/07/2004 e 21/07/2006, 14,28 ha = 97% da área)
  e 3 **toques na divisa** (2004, 2014, 2021), faixas de 8, 9 e 4 m, menores que um ponto de 30 m do
  satélite, com 100% da parte interna na faixa de 30 m do limite. Nada dentro depois de 31/07/2019.
  As três condições foram medidas pela P0 sobre as amostras gravadas (UTM 23S); as áreas são geodésicas
  GRS80, as mesmas do payload. Satélite das imagens de 2004 e 2006: Landsat 5 (cena única no catálogo).
- **Crédito rural:** Res. CMN 5.303/2026. Para até 4 módulos fiscais a conferência começa em 03/01/2028;
  é condição com documento (MCR 2-9, item 18). A lista do MMA não foi consultada.
- **Chuva recente:** 31,46 mm de 13/08 a 11/09/2026 contra 16,52 mm do normal desses dias (climatologia
  2001–2020); em 31 dos 35 anos de 1991 a 2025 choveu menos nessa janela. Acima do normal, sem alerta de seca.
- **Relevo:** Copernicus DEM GLO-30, classes da Embrapa em %: plano 11%, suave ondulado 62%, ondulado 24,5%,
  forte ondulado 2,5%. Leitura legal em graus: nada acima de 25°. Substitui o Fato `srtm`.
- **Solo:** mapa nacional do IBGE (1:250.000): Argissolo Vermelho Eutrófico (~6 em 10 partes) e Cambissolo
  Háplico (~4 em 10). Textura pelo MapBiomas Solo: argila 47–49%, argilosa. SoilGrids fica como segunda
  opinião (pH 5,4), com a divergência de argila escrita.
- **Outorga:** poço tubular do IGAM, 10 m³/h, 8 h 22 min por dia, válida até 15/12/2030 (base de 17/10/2025).
- **CONAB:** 2 armazéns, a 31,1 e 47,1 km da divisa.
- **Embargo do IBAMA:** pela camada oficial `adm_embargos_ibama_a` (a `embargos_siscom_brasil` do código de
  hoje está vazia no país inteiro). Nenhum sobre Curvelo.

### Conferência ao vivo feita pela P0 (14/09/2026, 0h00–0h02 de Brasília)

Para cada camada de restrição: a camada tem registros, a caixa do CAR dá 0 e uma caixa larga em volta dá
mais que 0 (a busca espacial funciona). Resultado gravado em `_fixture.provas.reconferencia_camadas`:

| camada | registros | caixa do CAR | caixa larga |
|---|---|---|---|
| IBAMA embargos (`adm_embargos_ibama_a`) | 91.197 | 0 | 120 |
| ICMBio embargos (`adm_embargo_icmbio_a`, autos até 08/02/2022) | 8.025 | 0 | 131 |
| IBAMA autos de infração | 710.298 | 0 | 1.317 |
| terras indígenas | 627 | 0 | 2 |
| unidades de conservação | 3.471 | 0 | 64 |
| territórios quilombolas | 440 | 0 | 3 |
| assentamentos | 8.377 | 0 | 14 |
| florestas públicas | 4.763 | 0 | 32 |
| ANM / SIGMINE | 269.631 | 0 | 7.902 |

### Ausentes de propósito

- `infrastructure.airports_v51`: a distância do aeródromo **medida da divisa** não foi provada; sem o Fato,
  a linha não existe (SPEC 2.0.2).
- matrícula, CCIR e embargos estaduais: sempre `fora_desta_edicao` pelo modelo, sem envelope.

### Chaves novas propostas pela P0

Para carregar as leituras corrigidas sem reescrever os campos antigos: `water.wells_v51`,
`water.pivots_v51`, `water.climatology_v51`, `agropecuaria.landcover_v51`, `agropecuaria.livestock_v51`,
`productive.relief_v51`, `productive.soil_map_v51`, `productive.soil_texture_v51`,
`productive.soilgrids_v51`, `productive.aptitude_v51`, `productive.erosion_v51`. Estão em
`report_book_v10_contracts.CHAVES_PROPOSTAS_P0` e precisam entrar na tabela §9.3 com a faixa dona.

### Pontos que a SPEC ainda diz diferente (para P2 e P7 decidirem)

Lista completa em `_fixture.divergencias_com_a_spec`. Os que mudam texto de ouro:

1. relevo e solo (acima): as frases das p. 1 e 8 da SPEC usam graus do SRTM e o solo da IDE-Sisema;
2. janela de chuva: a SPEC 0.3 e o PLANO L14 mandam não usar adjetivo quando o normal é < 30 mm; a F1 grava
   `leitura = acima_do_normal` e `esperado_abaixo_30mm = true`;
3. PRODES 2006: 14,28 ha (geodésica, igual ao payload); o 14,27 citado nas provas é a área em UTM;
4. imagem anterior da mancha de 2006: pela regra literal da SPEC 2.3 seria `null` (a camada tem 1 feição
   em 2005); a F1 segue o exemplo de Curvelo da SPEC (31/07/2004);
5. embargo do ICMBio: a camada publicada é de 2022, e a frase de ausência precisa dessa data;
6. poços: a contagem de 27 vem do filtro de distância com o ×1,15 que a P4 corrige.

## Complemento da F1 · `curvelo_declividade_celulas.json`

As 163 células de 30 m do Copernicus DEM dentro do CAR de Curvelo, com a inclinação em % e a classe da Embrapa,
para o mapa "Onde inclina mais" da p. 7 da amostra. A F1 só tem as partes por classe; este arquivo diz onde cada
parte fica. Gerado por `scripts/book_v10_celulas_declividade.py` (mesma conta da F1, limite em graus do SICAR) e
congelado por `curvelo_declividade_celulas.sha256`. O script da amostra confere, antes de desenhar, que o total
de células e as partes por classe batem com `productive.relief_v51` da F1. A P0 leva para a F1 gerada
(`book_v10.relief_cells`, proposta).
