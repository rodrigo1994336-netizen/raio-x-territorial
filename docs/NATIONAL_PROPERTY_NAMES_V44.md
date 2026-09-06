# V44 P0 — Base nacional de nomes de imóveis rurais

## Objetivo

O Raio-X Territorial deve resolver a denominação pública do imóvel rural em todo o Brasil sem inventar nome e sem depender de uma única API externa em tempo real. A base local contém apenas campos cadastrais não pessoais necessários à resolução do nome.

## Fontes públicas oficiais

1. **SNCR/INCRA — Consulta Pública de Imóveis Rurais**
   - denominação, código do imóvel rural, município/IBGE, UF e área;
   - não contornar CAPTCHA ou proteção de acesso.

2. **CAFIR/RFB — Dados Abertos**
   - cobertura nacional;
   - CIB/NIRF, código INCRA/SNCR quando presente, nome do imóvel, área, situação, UF, município;
   - campos de localidade do imóvel como `id_municipio`, `distrito` e `endereco` podem ser usados somente para desambiguação cadastral.

## Minimização de dados

A base local **não armazena titular, detentor, CPF, CNPJ, telefone, e-mail nem endereço de pessoa**. Endereço e distrito do próprio imóvel rural podem ser armazenados apenas como contexto de desambiguação do imóvel.

Persistir somente:

- identificador SNCR/INCRA;
- CIB/NIRF;
- denominação do imóvel;
- UF, município, `id_municipio`/IBGE quando disponíveis;
- área;
- distrito e endereço do imóvel, quando públicos no CAFIR;
- situação cadastral;
- fonte, licença, data e URL de origem.

## Prioridade do resolvedor

1. nome explícito do SICAR;
2. SNCR/CAFIR por código SNCR obtido de sobreposição SIGEF forte e inequívoca;
3. nome explícito do SIGEF em sobreposição forte;
4. SNCR/CAFIR por município + área, com unicidade rigorosa;
5. desambiguação adicional por `id_municipio`, `distrito` e `endereco` quando houver contexto equivalente confiável;
6. evidência OSM auditada;
7. OSM ao vivo fail-soft;
8. sem nome quando houver conflito ou ausência de evidência segura.

Nenhuma regra comprova titularidade.

## Match por município + área

A área do CAR é declarada e pode divergir da área cadastrada no CAFIR. Portanto:

```text
tolerancia_ha = max(0,5% da área CAR, 0,01 ha)
```

Regras:

- aplicar a janela inteira de tolerância; não exigir igualdade exata;
- usar primeiro IBGE quando a fonte de nomes o fornecer; caso contrário UF + município;
- se todos os candidatos da janela tiverem a mesma denominação, o nome pode ser promovido;
- se houver duas ou mais denominações, tentar desambiguar por `id_municipio`, `distrito` e `endereco` somente quando o lado CAR/territorial fornecer contexto equivalente confiável;
- persistindo duas ou mais denominações, **não promover nome**;
- registrar em cada resolução a tolerância efetivamente usada e o critério de match.

## Métricas obrigatórias

Toda medição de cobertura deve separar:

1. `matched`: CARs que receberam nome com evidência segura;
2. `ambiguous`: havia candidato(s) no CAFIR, mas mais de uma denominação permaneceu possível;
3. `absent`: nenhum candidato CAFIR caiu na janela de município + área.

Reportar quantidade e percentual dos três grupos. Ambiguidade é problema potencialmente tratável por desambiguação; ausência é falta de cobertura da fonte e não deve ser mascarada como ambiguidade.

## Produção

O arquivo nacional não deve ser commitado no Git. Deve residir em armazenamento persistente do serviço ou ser construído por pipeline autorizado. O runtime abre a base em modo somente leitura.

```text
RX_PROPERTY_NAMES_DB=/var/data/rx_property_names.sqlite3
```

Endpoint de diagnóstico:

```text
GET /v1/live/property-name-registry
```

## Atualização

- CAFIR: atualizar quando a Receita publicar nova competência;
- SNCR: atualizar quando o arquivo público permitido estiver disponível;
- carga idempotente por fingerprint;
- registrar data e origem da competência;
- nunca automatizar bypass de CAPTCHA.
