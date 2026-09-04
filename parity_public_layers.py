from __future__ import annotations

import territorial_constraints as tc

EXTRA_SERVICES={
    'floresta_publica':(
        'Floresta Pública',
        'Serviço Florestal Brasileiro / IBAMA-PAMGIA',
        'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_floresta_publica_a/FeatureServer',
    ),
    'sitio_arqueologico':(
        'Sítio Arqueológico',
        'IPHAN / IBAMA-PAMGIA',
        'https://pamgia.ibama.gov.br/server/rest/services/BasesSincronizadas/lim_sitios_arqueologicos_iphan_a/FeatureServer',
    ),
}

for key,meta in EXTRA_SERVICES.items():
    tc.SERVICES[key]=meta

print('RX_PARITY_PUBLIC_LAYERS=SFB_PUBLIC_FORESTS,IPHAN_ARCHAEOLOGY',flush=True)
