# Raio-X Territorial — Frentes Mestras V40

Este arquivo é a fila canônica de fechamento. Uma frente só pode sair daqui depois de passar por: implementação, gate técnico, teste em produção, inspeção visual mobile/desktop e evidência de não regressão.

## P0 — Base confiável
- [ ] Estabilidade: sem 502, boot previsível, memória do portal leve, worker pesado isolado.
- [ ] Mobile: uma única superfície ativa; mapa OU dossiê OU filtro. Zero painel/controle sobreposto.
- [ ] Desktop: mapa e dossiê em áreas próprias, sem sobreposição nem conteúdo estourando.
- [ ] Internet de campo: interface/mapa primeiro, cache, progressive loading, baixo volume em 2G/3G/Save-Data.
- [ ] Ações: nenhum botão silencioso; loading local, erro amigável e retry localizado.
- [ ] Abas: conteúdo preservado, cache por imóvel/aba, nenhuma tela vazia durante refresh.

## P0 — Descoberta e identificação
- [ ] Mapa nacional: CAR por viewport em todas as UFs, sem UF grudada do GPS.
- [ ] Nome das fazendas: SICAR quando explícito + SIGEF por sobreposição segura + labels em lote no mapa/lista.
- [ ] Busca simples: nome, CAR, município, coordenada e identificadores públicos.
- [ ] Busca avançada nacional: UF, município, área, nome/identificador.
- [ ] Busca produtiva: pecuária/pastagem, agricultura, soja, cana, café, citros, algodão, arroz, silvicultura, aquicultura, uso misto e demais perfis suportados por evidência espacial.
- [ ] Busca por matrícula: apenas onde SIGEF/SNCI ou integração autorizada expuserem o identificador; não prometer cobertura nacional inexistente.

## P0 — Dados territoriais
- [ ] CAR completo: APP, Reserva Legal, vegetação nativa, área consolidada, uso restrito, passivo/excedente quando a fonte permitir.
- [ ] Solo físico-químico + aptidão + erosão + relevo/declividade.
- [ ] MapBiomas por imóvel: cobertura, pastagem, vigor e perfis produtivos reais do polígono.
- [ ] Clima: 7d/30d/1a com cache e refresh não destrutivo.
- [ ] Água: SIAGAS/outorgas/pivôs quando disponíveis, sem inferência falsa.
- [ ] Mineração: ANM + SGB fail-soft, terras raras como sinal, nunca como jazida comprovada.
- [ ] Produção rural: bovinos e demais rebanhos, leite, aquicultura, silvicultura/eucalipto quando identificável, PEVS/PAM/PPM, SIF e contexto regional corretamente rotulado.
- [ ] Embargos/autos/UC/quilombola/TI/assentamentos/PRODES e demais restrições ambientais.

## P1 — Relatório premium
- [ ] Primeiro PDF com tempo aceitável; cache subsequente imediato.
- [ ] Prancha visual premium: imóvel protagonista, perímetro, contexto, norte, escala, Sentinel datado, NDVI e referência de alta resolução tecnicamente correta.
- [ ] Nome da fazenda, município/UF, área e CAR em identidade principal.
- [ ] Imagem de acesso/via pública com fonte/licença adequada e sem chamar de entrada quando não houver evidência.
- [ ] Leitura humana: conclusão simples → sinal → números → detalhes técnicos/fontes.
- [ ] Inspeção visual página por página antes de declarar concluído.

## P1 — Monitoramento e integrações
- [ ] Alertas persistentes 24/7 com banco durável, histórico, snapshots, diffs e estado de leitura.
- [ ] WhatsApp Meta Cloud API operacional após credenciais.
- [ ] CCIR/SNCR, CIB/NIRF, CNDIR, matrícula/ônus/titularidade, SICOR e buscas documentais apenas por integrações legítimas/autorizadas.

## P1 — Benchmark final
- [ ] Mesma propriedade analisada no Raio-X e no Dados Fazenda.
- [ ] Comparação objetiva: cobertura de fontes, clareza, velocidade, mapa, nomes, busca, relatório, monitoramento e experiência mobile.
- [ ] Só declarar “mais completo/melhor” após vencer o benchmark de forma demonstrável.

## Fora de execução nesta fase
- Modo URBANO: somente planejamento até aprovação explícita do desenho funcional.
