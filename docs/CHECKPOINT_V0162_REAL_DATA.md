# Raio-X Territorial - checkpoint V0.16.2

Data: 2026-09-04

## Regra do checkpoint

Este marco só registra integrações que já foram executadas contra dados reais no Render. Campos não sustentados por fonte acessível permanecem NÃO CONSULTADO/INDISPONÍVEL; ausência de resposta de uma fonte nunca é convertida em ausência de ocorrência.

## Benchmark real

CAR: `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`
Município: Curvelo/MG
Área SICAR: 14,795 ha
Condição: Aguardando análise

## Fontes validadas

- SICAR: polígono e metadados básicos do imóvel.
- SIGEF: consulta pelo espelho público disponível via IBAMA/PAMGIA.
- IBAMA/PAMGIA: embargos SISCOM.
- IBAMA/PAMGIA: autos de infração ambiental, com deduplicação e retry no V0.16.2.
- INPE/TerraBrasilis: PRODES com interseção geométrica exata.
- ANM/SIGMINE: processos minerários com fallback curl em instabilidade de transporte.
- INPE Programa Queimadas: feed oficial de focos em arquivos de 10 minutos.
- FUNAI/CNUC/INCRA/ICMBio pelas camadas públicas usadas no motor de restrições territoriais.
- IDE-Sisema/IGAM: outorgas estaduais de recursos hídricos.
- IDE-Sisema/ANA: outorgas federais disponíveis na IDE-Sisema.
- ANA/SNIRH: pivôs centrais de irrigação, referência 2022.
- NASA POWER: agroclimatologia diária recente no centróide do imóvel.

## Resultados do benchmark já comprovados

### PRODES

4 ocorrências exatas, área única de interseção 14,496704 ha:
- 2004: 0,138050 ha
- 2006: 14,278854 ha
- 2014: 0,051891 ha
- 2021: 0,027909 ha

### Restrições e fiscalização

No benchmark, nas execuções validadas:
- SIGEF: 0 interseções/candidatos no envelope consultado.
- Embargo IBAMA: 0.
- Processos ANM: 0.
- Terra Indígena: 0.
- Unidade de Conservação: 0.
- Território Quilombola: 0.
- Assentamento: 0.
- Embargo ICMBio: 0.
- Autos IBAMA: 0 nas execuções em que a fonte respondeu; V0.16.2 adiciona retry para reduzir falso 'indisponível' por falha transitória.

### Outorgas

As duas camadas foram descobertas e consultadas:
- IGAM estadual: `IDE:ide_2103_mg_outorgas_uso_recursos_hidricos_pto`
- ANA federal: `IDE:ide_2103_mg_federais_ana_outorgas_pto`

Benchmark:
- 1 outorga intersectante.
- 7 outorgas em até 5 km.
- Intersectante mais próxima: processo 17849/2019; portaria 1309443/2020; status Deferido; vencimento informado 15/12/2030; captação subterrânea por poço tubular; vazão informada de 10 m³/h nos meses retornados pela fonte.
- A camada federal foi consultada e retornou 0 ocorrências no envelope deste benchmark.

### Pivôs centrais

ANA/SNIRH 2022:
- serviço validado por ArcGIS REST JSON.
- 1 feição no envelope expandido usado na consulta do benchmark.
- 0 pivôs intersectando o CAR.
- 0 pivôs em até 5 km após cálculo de distância exata.

### Clima recente

NASA POWER, última execução validada:
- 30 dias válidos: 2026-08-02 a 2026-08-31.
- precipitação acumulada: 9,03 mm.
- temperatura média: 25,186 °C.
- máxima diária média: 32,911 °C.
- mínima diária média: 18,229 °C.
- umidade relativa média: 47,565%.
- radiação solar retornou indisponível nessa execução e não deve ser inventada.

## PDF real validado antes do hardening V0.16.2

Report ID: `RX-20260904T155808Z-A14D011F`
SHA-256: `50f2773962e6e447b2bfc517f4d267b40ce5cb28d7b62c215f0eb3bddbb4a708`
Tamanho: 133429 bytes
Payload SHA-256: `218ad0694b7a05de2c1cab3bc769a9dacbc96a5842b1f9dc715fce9f27af9526`
Resultado de startup: `RX_REAL_PDF_OK`.

## Hardening V0.16.2

- startup não bloqueia mais a abertura da porta HTTP durante o smoke test externo;
- smoke real roda em background;
- cache em memória por CAR por 300 s;
- lock por CAR para evitar múltiplas consultas concorrentes idênticas;
- retry específico para autos IBAMA;
- retry/fallback já existente para ANM e transportes curl nas novas fontes;
- endpoint continua gerando o PDF com o mesmo motor de produção.

## Limitações conhecidas

1. O WFS público do SICAR testado expõe as camadas de imóveis por UF, mas não expõe APP, Reserva Legal, vegetação nativa e área consolidada como camadas separadas. Esses valores não podem ser inventados.
2. SNCI direto ainda não foi validado.
3. Matrícula, CNS e titularidade registral atual ainda não estão integrados ao pipeline de produção.
4. O Postgres do Render existe, mas o serviço ainda não está conectado à persistência. Os PDFs atuais são gravados em armazenamento efêmero do serviço.
5. Solo, aptidão, relevo, NDVI, logística e demais blocos ainda precisam de fontes reais e validação.
6. NASA POWER é produto em grade no centróide do imóvel e não substitui estação meteorológica local.
7. Pivôs ANA têm referência 2022; não devem ser apresentados como fotografia atual de 2026.

## Próximo marco

Prioridade técnica:
1. persistência do histórico de consultas/relatórios;
2. relevo/altitude/declividade;
3. solo e aptidão;
4. uso/cobertura do solo e NDVI;
5. infraestrutura/logística;
6. ampliar cobertura estadual/nacional das fontes que hoje têm conector específico de MG;
7. inspeção visual automatizada/regressão do PDF real.
