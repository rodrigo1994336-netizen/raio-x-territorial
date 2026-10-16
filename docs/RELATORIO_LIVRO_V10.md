# Relatório-livro v10 — como ligar, como voltar atrás e o que ele mede

O PDF do Raio-X passa a sair no formato **livro**: 13 a 18 folhas, uma pergunta por capítulo,
resposta primeiro e a situação de cada consulta num selo. Ele desenha o MESMO dado que o relatório
anterior apurava — nada de fonte nova, nada de custo novo.

## Como voltar atrás, sem mexer em código

Variável de ambiente do serviço do relatório:

| `RX_RELATORIO_LIVRO` | efeito |
|---|---|
| vazia ou `on` (**padrão**) | o PDF sai no formato livro; se o livro falhar, sai o relatório antigo |
| `off` | o PDF sai no formato antigo, exatamente como antes |
| `so_livro` | só o livro; se ele falhar, a emissão falha (serve para portão de CI) |

Outras variáveis, todas opcionais:

| variável | padrão | para quê |
|---|---|---|
| `RX_LIVRO_IMAGEM` | `on` | `off` desliga a foto de satélite; o livro desenha a planta do imóvel |
| `RX_LIVRO_IMAGEM_TIMEOUT_S` | `9` | prazo de cada tentativa da imagem (há uma segunda, menor) |
| `RX_LIVRO_SITE` | `raioxterritorial.com.br` | endereço impresso no rodapé |

## Onde cada coisa mora

| arquivo | o que faz |
|---|---|
| `report_book_v10_contracts.py` | contrato: os cinco estados, os nomes dos fatos, os campos proibidos |
| `report_book_v10_fatos.py` | **o que o livro afirma**: envelope `_v51` quando existe, senão a regra de transição sobre o payload de hoje |
| `report_book_v10_dados.py` | **o que o livro desenha**: geometria em metros, foto limpa, recorte das manchas |
| `report_book_v10_style.py` | letra, grade, cor e selo |
| `report_book_v10.py` | o motor: as 13 folhas e o compositor |
| `report_livro_patch.py` | a ligação ao serviço e a volta pelo formato antigo |
| `scripts/livro_v10_gate.py` | o portão, com controle positivo por mutação em cada regra |

## As regras do dono, em código

1. **Zero não é ausência.** Um fato só vira "nenhum registro" quando a fonte respondeu inteira; o
   alcance da consulta vai escrito junto ("só embargos com mapa na base oficial").
2. **Tentar não é responder.** Fonte que não respondeu vira "consulta pendente", nunca "nenhum".
3. **O livro não inventa.** Falta o campo, some a linha — nada de "—", "N/D" ou zero no lugar.
4. **Pessoas não aparecem.** Nome, CPF, CNPJ e razão social são retirados antes de o livro ver o
   dado; o armazém da CONAB entra pelo tipo, pela distância e pela capacidade, nunca pela empresa.
5. **Nada escrito sobre o mapa.** A foto não leva rótulo e o nome do imóvel não é desenhado nela.

## O que custa a mais

Medido na mesma máquina, com o mesmo payload, mediana de três desenhos:

| imóvel | desenho antigo | desenho livro | diferença | PDF antigo | PDF livro |
|---|---|---|---|---|---|
| Curvelo (MG), 14,8 ha | 1,08 s | 1,41 s | **+0,32 s** | 681 KB | 1,3 MB |
| Altamira (PA), 432 ha | 1,18 s | 1,52 s | **+0,33 s** | 115 KB | 872 KB |

A foto de satélite custa cerca de 3 s à parte, mas é buscada **em paralelo** com as consultas, desde
o começo da emissão: quando o livro vai desenhar, ela já está pronta (medido: 4 ms). Se não estiver,
o livro espera no máximo um prazo curto e, sem ela, desenha a planta do imóvel.

O livro custa 363 ms no arranque do serviço (registro das fontes e import do motor), uma vez só.

O PDF fica maior porque passa a ter fotos. Em emissão com imagem indisponível ele volta ao tamanho
de antes.

## Fontes da imagem

A foto do livro vem do mosaico público de alta resolução da Esri (World Imagery), pedido já na
projeção métrica do imóvel e **sem camada de rótulo**. É a mesma fonte que o projeto já usa no mapa,
sem chave de acesso e sem custo. O mosaico não publica a data da cena nesta chamada, e por isso a
legenda **não traz data** — em vez de trazer uma data errada. A data que aparece no apêndice é a da
cena Sentinel-2, que tem data de verdade.

## O que ainda não entra nesta edição

Sai como "não faz parte desta edição", com a linha no Apêndice B:

- composição declarada no CAR (APP e Reserva Legal) — o SICAR não devolve essa consulta hoje;
- embargos do ICMBio — a camada não é consultada nesta edição;
- matrícula, CCIR e embargos estaduais — contrato comercial, fora do produto;
- aeródromos — a distância medida hoje sai do centro do imóvel, e a linha do livro pede a
  distância da divisa;
- safras do município — só entram com o ano da safra; número sem ano não diz de quando é.
