from __future__ import annotations

import territorial_constraints as tc
import iphan_sicg

EXTRA_SERVICES={
    'floresta_publica':(
        'Floresta Pública',
        'Serviço Florestal Brasileiro / IBAMA-PAMGIA',
        'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_floresta_publica_a/FeatureServer',
    ),
    # F2: a checagem de sítio arqueológico usa o IPHAN oficial (pontos + polígonos).
    # O espelho PAMGIA só tinha polígonos, desatualizados; ponto dentro do imóvel dava "0".
    'sitio_arqueologico':(
        'Sítio Arqueológico',
        iphan_sicg.SOURCE_LABEL,
        iphan_sicg.WFS_URL,
    ),
}

for key,meta in EXTRA_SERVICES.items():
    tc.SERVICES[key]=meta


_ORIGINAL_QUERY_ONE=getattr(tc._query_one,'_rx_original',tc._query_one)


async def _query_one(client,key,meta,car,bbox):
    if key=='sitio_arqueologico':
        return key,await iphan_sicg.query_constraint(client,car,meta[0])
    return await _ORIGINAL_QUERY_ONE(client,key,meta,car,bbox)


_query_one._rx_original=_ORIGINAL_QUERY_ONE
tc._query_one=_query_one

print('RX_PARITY_PUBLIC_LAYERS=SFB_PUBLIC_FORESTS,IPHAN_SICG_OFFICIAL',flush=True)
