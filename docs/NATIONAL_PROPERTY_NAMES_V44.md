# V44 P0 — Base nacional de nomes de imóveis rurais

## Objetivo

O Raio-X Territorial deve resolver a denominação pública do imóvel rural em todo o Brasil sem inventar nome e sem depender de uma única API externa em tempo real. A base local contém apenas campos cadastrais não pessoais necessários à resolução do nome.

## Fontes públicas oficiais

1. **SNCR/INCRA — Consulta Pública de Imóveis Rurais**
   - licença: ODbL;
   - atualização declarada: mensal;
   - campos úteis: código do imóvel rural, denominação, código IBGE do município, município, UF e área total;
   - a consulta pública pode exigir hCaptcha para download. O Raio-X não contorna CAPTCHA nem proteção de acesso.

2. **CAFIR/RFB — Dados Abertos**
   - licença: Creative Commons Attribution;
   - atualização declarada: trimestral;
   - cobertura nacional;
   - campos úteis: CIB/NIRF, código do imóvel no INCRA/SNCR, nome do imóvel rural, área, situação, UF e município;
   - desde 2024 os arquivos públicos podem ser disponibilizados por UF, o que permite carga nacional incremental.

## Minimização de dados

A base local **não deve armazenar titular, detentor, CPF, CNPJ, endereço de pessoa, telefone, e-mail ou qualquer outro dado pessoal**. Mesmo que um arquivo de origem contenha essas colunas, o importador descarta esses campos e persiste apenas:

- identificador SNCR/INCRA;
- CIB/NIRF quando público no arquivo de imóvel;
- denominação do imóvel;
- UF e município;
- código IBGE quando disponível;
- área;
- situação cadastral;
- fonte, licença, data da fonte e URL de origem.

## Prioridade do resolvedor

1. nome explícito do SICAR, quando o serviço realmente o fornecer;
2. base oficial local SNCR/CAFIR por código SNCR obtido de sobreposição SIGEF forte e inequívoca;
3. nome explícito do SIGEF em sobreposição forte;
4. base oficial local por município + área, somente quando houver um único nome compatível;
5. evidência geográfica pública já auditada do OpenStreetMap;
6. OpenStreetMap ao vivo como fallback fail-soft;
7. sem nome quando houver conflito ou ausência de evidência segura.

Nenhuma dessas regras comprova propriedade ou titularidade.

## Match por município e área

- SNCR com código IBGE: janela estrita compatível com área de quatro casas decimais.
- CAFIR fixed-width sem IBGE: fallback para UF + município, com tolerância máxima de `0,051 ha` para acomodar área pública armazenada com uma casa decimal.
- dois ou mais nomes distintos na mesma janela: **não promover nome**.

## Construção da base

Arquivo por arquivo:

```bash
python scripts/build_national_property_name_db.py \
  --db /var/data/rx_property_names.sqlite3 \
  --cafir-file /dados/CAFIR/K34313UF.D40701.MG01 \
  --source-date 2026-09-01
```

Carga de todos os arquivos de uma pasta nacional:

```bash
python scripts/build_national_property_name_db.py \
  --db /var/data/rx_property_names.sqlite3 \
  --cafir-dir /dados/CAFIR \
  --sncr-dir /dados/SNCR \
  --source-date 2026-09-01
```

Também são aceitos `--cafir-csv`, `--cafir-csv-dir` e múltiplos argumentos repetidos.

## Produção

Definir:

```text
RX_PROPERTY_NAMES_DB=/var/data/rx_property_names.sqlite3
```

O arquivo nacional não deve ser commitado no Git. Deve residir em armazenamento persistente do serviço ou ser construído por pipeline de ingestão autorizado. O runtime abre a base em modo somente leitura para resolver nomes.

Endpoint de diagnóstico:

```text
GET /v1/live/property-name-registry
```

A resposta informa total de registros, fontes, UFs presentes e `national_ready`. A carga só é considerada nacionalmente completa quando as 27 UFs estiverem representadas.

## Atualização

- CAFIR: sincronização a cada publicação trimestral, ou quando a Receita publicar nova competência.
- SNCR: atualização mensal quando o arquivo for obtido por meio público permitido.
- toda carga é idempotente por fingerprint;
- registrar data e origem da competência;
- nunca automatizar bypass de hCaptcha.
