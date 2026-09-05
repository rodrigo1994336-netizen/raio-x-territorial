from __future__ import annotations

from typing import Any


CROP_CODES={18,19,20,35,36,39,40,41,46,47,48,62}
SPECIFIC={
    'soy':(39,'Soja'),
    'sugarcane':(20,'Cana-de-açúcar'),
    'rice':(40,'Arroz'),
    'coffee':(46,'Café'),
    'citrus':(47,'Citros'),
    'cotton':(62,'Algodão'),
    'perennial_crops':(36,'Lavouras perenes'),
    'temporary_crops':(19,'Lavouras temporárias'),
}


def _classes(mapbiomas:dict[str,Any]) -> dict[int,float]:
    out={}
    for row in mapbiomas.get('classes') or []:
        try:out[int(row.get('code'))]=float(row.get('share_pct') or 0)
        except Exception:continue
    return out


def _confidence(share:float) -> str:
    if share>=50:return 'forte'
    if share>=20:return 'moderado'
    if share>=5:return 'indício'
    return 'baixo'


def classify_landuse_profile(mapbiomas:dict[str,Any]) -> dict[str,Any]:
    if not isinstance(mapbiomas,dict) or not mapbiomas.get('ok'):
        return {
            'ok':False,
            'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11',
            'detail':(mapbiomas or {}).get('detail') if isinstance(mapbiomas,dict) else 'invalid_mapbiomas_result',
            'profile_codes':[],
            'profiles':[],
            'interpretation':'O perfil produtivo não foi concluído; indisponibilidade da fonte não é tratada como ausência de atividade.'
        }
    shares=_classes(mapbiomas)
    pasture=shares.get(15,0.0)
    crops=sum(shares.get(c,0.0) for c in CROP_CODES)
    silv=shares.get(9,0.0)
    aqua=shares.get(31,0.0)
    mining=shares.get(30,0.0)
    water=shares.get(26,0.0)+shares.get(33,0.0)
    native=float(mapbiomas.get('native_vegetation_share_pct') or 0)
    profiles=[]
    def add(code,label,share,note):
        profiles.append({'code':code,'label':label,'share_pct':round(float(share),2),'confidence':_confidence(float(share)),'note':note})
    if pasture>=12:
        add('livestock_pasture','Pecuária / pastagem',pasture,'Pastagem mapeada dentro do imóvel. É sinal de uso compatível com pecuária; não comprova rebanho presente.')
    if crops>=10:
        add('agriculture','Agricultura',crops,'Classes agrícolas mapeadas dentro do imóvel.')
    for code,(class_code,label) in SPECIFIC.items():
        share=shares.get(class_code,0.0)
        if share>=3:add(code,label,share,f'Classe {label} mapeada pelo MapBiomas dentro do imóvel.')
    if silv>=3:add('silviculture','Silvicultura',silv,'Classe de silvicultura mapeada dentro do imóvel; não identifica espécie florestal isoladamente.')
    if aqua>=0.5:add('aquaculture','Aquicultura',aqua,'Classe de aquicultura mapeada dentro do imóvel.')
    if mining>=0.2:add('mining_landcover','Área minerada',mining,'Classe de mineração mapeada dentro do imóvel; não substitui consulta a processo/título minerário.')
    if pasture>=12 and crops>=8:add('mixed_agro','Uso agropecuário misto',pasture+crops,'Pastagem e agricultura aparecem com participação material dentro do imóvel.')
    if native>=50:add('native_dominant','Vegetação nativa predominante',native,'Mais da metade da área classificada aparece como vegetação nativa/remanescente.')
    if water>=3:add('water_relevant','Água superficial relevante',water,'Corpos d’água ocupam participação relevante no recorte do imóvel.')
    profiles.sort(key=lambda x:x.get('share_pct') or 0,reverse=True)
    return {
        'ok':True,
        'source':mapbiomas.get('source') or 'MapBiomas Brasil — Cobertura 30 m — Coleção 11',
        'year':mapbiomas.get('year'),
        'profile_codes':[p['code'] for p in profiles],
        'profiles':profiles,
        'signals':{
            'pasture_share_pct':round(pasture,2),
            'crop_share_pct':round(crops,2),
            'silviculture_share_pct':round(silv,2),
            'aquaculture_share_pct':round(aqua,2),
            'mining_share_pct':round(mining,2),
            'native_share_pct':round(native,2),
            'water_share_pct':round(water,2),
        },
        'interpretation':'Perfil estimado por uso/cobertura do solo dentro do polígono. Serve para localizar imóveis compatíveis com determinada atividade; não prova produção, rebanho, faturamento ou exploração efetiva sem fonte específica.'
    }


print('RX_LANDUSE_PROFILE_V39=parcel_level_productive_signals',flush=True)
