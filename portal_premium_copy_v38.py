from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML
replacements = {
    "['agro','Agropecuária']": "['agro','Produção rural']",
    '<h3>Agropecuária</h3>': '<h3>Produção rural</h3>',
    'Consultando pecuária e agropecuária…': 'Consultando produção rural e contexto territorial…',
    'Rebanho regional, cadeia SIF e triagem do imóvel. Município e fazenda nunca são misturados.': 'Produção, rebanhos e contexto territorial. Dados municipais nunca são apresentados como se fossem do imóvel.',
    'CONTEXTO AGROPECUÁRIO': 'CONTEXTO DE PRODUÇÃO RURAL',
    'A aba busca dados regionais de produção e cadeia agropecuária.': 'A aba reúne dados regionais de produção rural, rebanhos e cadeia produtiva.',
}
for old,new in replacements.items():
    html=html.replace(old,new)
portal_v8.PORTAL_HTML=html

print('RX_PREMIUM_COPY_V38=rural_production_terminology_aligned',flush=True)
