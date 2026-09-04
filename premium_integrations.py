from __future__ import annotations

import os
from typing import Any


def _flag(name: str, default: str='off') -> bool:
    return os.getenv(name, default).strip().lower() in {'1','true','yes','on'}

INTEGRATIONS: dict[str,dict[str,Any]] = {
    'whatsapp_cloud': {
        'label':'WhatsApp Cloud API',
        'enabled_flag':'RX_WHATSAPP_ENABLED',
        'credentials':['WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_ACCESS_TOKEN'],
        'cost_mode':'usage',
        'purpose':'Consulta por WhatsApp, entrega de relatório e alertas.',
    },
    'serpro_cnpj': {
        'label':'SERPRO CNPJ',
        'enabled_flag':'RX_SERPRO_CNPJ_ENABLED',
        'credentials':['SERPRO_CONSUMER_KEY','SERPRO_CONSUMER_SECRET'],
        'cost_mode':'usage',
        'purpose':'Dados cadastrais de pessoas jurídicas quando aplicável.',
    },
    'serpro_cnd': {
        'label':'SERPRO CND',
        'enabled_flag':'RX_SERPRO_CND_ENABLED',
        'credentials':['SERPRO_CONSUMER_KEY','SERPRO_CONSUMER_SECRET'],
        'cost_mode':'usage',
        'purpose':'Consulta/emissão de certidões fiscais quando contratada.',
    },
    'sncr_ccir': {
        'label':'SNCR / CCIR autorizado',
        'enabled_flag':'RX_SNCR_ENABLED',
        'credentials':['SNCR_CLIENT_ID','SNCR_CLIENT_SECRET'],
        'cost_mode':'authorized',
        'purpose':'Consulta cadastral/CCIR mediante habilitação oficial.',
    },
    'onr_matricula': {
        'label':'ONR / RI Digital — Matrícula',
        'enabled_flag':'RX_ONR_ENABLED',
        'credentials':['ONR_CLIENT_ID','ONR_CLIENT_SECRET'],
        'cost_mode':'pass_through',
        'purpose':'Matrícula atualizada e serviços registrais, com custo repassado.',
    },
    'onr_onus': {
        'label':'ONR / RI Digital — Ônus',
        'enabled_flag':'RX_ONR_ENABLED',
        'credentials':['ONR_CLIENT_ID','ONR_CLIENT_SECRET'],
        'cost_mode':'pass_through',
        'purpose':'Certidão de ônus e atos registrais, com custo repassado.',
    },
    'onr_pesquisa_bens': {
        'label':'Pesquisa Nacional de Bens',
        'enabled_flag':'RX_ONR_ENABLED',
        'credentials':['ONR_CLIENT_ID','ONR_CLIENT_SECRET'],
        'cost_mode':'pass_through',
        'purpose':'Busca patrimonial autorizada por CPF/CNPJ conforme regras do serviço.',
    },
    'holder_search': {
        'label':'Busca por titular / CPF / CNPJ',
        'enabled_flag':'RX_HOLDER_SEARCH_ENABLED',
        'credentials':['RX_HOLDER_PROVIDER_KEY'],
        'cost_mode':'usage',
        'purpose':'Pesquisa por titular apenas em provedor/base com autorização e base legal.',
    },
}


def status() -> dict[str,Any]:
    rows=[]
    for code,cfg in INTEGRATIONS.items():
        enabled=_flag(cfg['enabled_flag'])
        missing=[k for k in cfg['credentials'] if not os.getenv(k)]
        ready=enabled and not missing
        rows.append({
            'code':code,
            'label':cfg['label'],
            'enabled':enabled,
            'configured':not missing,
            'ready':ready,
            'missing_credentials':missing if enabled else [],
            'cost_mode':cfg['cost_mode'],
            'purpose':cfg['purpose'],
            'state':'ATIVO' if ready else ('CONFIGURAR CREDENCIAIS' if enabled else 'PREPARADO — OFF'),
        })
    return {
        'zero_cost_mode':not any(x['ready'] for x in rows),
        'integrations':rows,
        'policy':'Integrações pagas/restritas permanecem OFF por padrão. Ativação exige credencial e feature flag explícita; nenhum custo é disparado silenciosamente.',
    }
