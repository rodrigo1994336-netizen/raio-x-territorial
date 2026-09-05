from __future__ import annotations

# Audited public geographic-name snapshot captured from OpenStreetMap on 2026-09-05
# and matched to exact SICAR CAR polygons via /v1/live/resolve. These are geographic
# denominations only; they never imply ownership or registrant identity.
# Evidence workflow: GitHub Actions run 33991816916, artifact 9976884491.
SOURCE='OpenStreetMap contributors — denominação geográfica pública (ODbL) · snapshot auditado 2026-09-05'

SAFE_BY_CAR={
'MG-3120904-37E8BD7FC17D480C903EE388D3F4B72D':{'name':'Fazenda Agnelical','lat':-18.4448863,'lon':-44.2181254,'osm_id':5600682099,'area_ha':1471.3776},
'MG-3120904-E0C91FE86A3646D5AFA40786D6C554A9':{'name':'Fazenda Barrinha','lat':-19.1717854,'lon':-44.6657032,'osm_id':4348989954,'area_ha':60.5788},
'MG-3120904-CF2110922A0F4FE59CE82732567814C2':{'name':'Fazenda Buriti dos Coelhos','lat':-19.0409524,'lon':-44.5136901,'osm_id':4348989961,'area_ha':165.1436},
'MG-3120904-785BB8E4F83D4196A9B7BE2A8F96C37C':{'name':'Fazenda Capão do Meio','lat':-19.0836947,'lon':-44.6529986,'osm_id':4348989964,'area_ha':129.1893},
'MG-3120904-2598BAE9AFB9412E9144C4EA3A00B17F':{'name':'Fazenda Chicão','lat':-18.3737499,'lon':-44.2671777,'osm_id':5600682103,'area_ha':701.4646},
'MG-3120904-0744DF070C114977B91C17814B919E6C':{'name':'Fazenda Curral de Pedra','lat':-19.1015488,'lon':-44.3991919,'osm_id':4348989968,'area_ha':228.0436},
'MG-3120904-A733B814F0064F99BA07AA336D752BFB':{'name':"Fazenda Engenho d'Água de Baixo",'lat':-19.1934298,'lon':-44.6015877,'osm_id':4348989970,'area_ha':327.8061},
'MG-3120904-0C5B370E642A47DE91626BBD298B4983':{'name':'Fazenda Harmonia','lat':-18.3354617,'lon':-44.2716409,'osm_id':5600682090,'area_ha':596.8213},
'MG-3120904-9FBC97676035463FACA157F0DBD59BDA':{'name':'Fazenda Jacaré','lat':-19.1510053,'lon':-44.5536067,'osm_id':4348989976,'area_ha':2147.1248},
'MG-3120904-82852A8D699342599B7B5B9FB04FC820':{'name':'Fazenda Mocambo','lat':-18.4823938,'lon':-44.2012722,'osm_id':5600682096,'area_ha':593.5167},
'MG-3120904-D22D777AF0DE4F10BBA69BCA86A1B5DA':{'name':'Fazenda Modelo','lat':-19.1254877,'lon':-44.6383232,'osm_id':4348989983,'area_ha':29.0562},
'MG-3120904-DD160474A07C47FDB08B599FC35B105F':{'name':'Fazenda Monjolo','lat':-19.1216763,'lon':-44.5342106,'osm_id':4348989984,'area_ha':710.6909},
'MG-3120904-DEB45DA405324E9A80553CC91E37D030':{'name':'Fazenda Mãe-Joana','lat':-19.0887236,'lon':-44.6432139,'osm_id':4348989986,'area_ha':371.3618},
'MG-3120904-FE15B9F7800348D994046DBF6F379A24':{'name':'Fazenda Nossa Senhora de Fátima','lat':-19.1632728,'lon':-44.5642513,'osm_id':4348989988,'area_ha':55.6512},
'MG-3120904-2647CCFB65C147EEAD8F8CD395BC4692':{'name':'Fazenda Nova Alvorada','lat':-19.0959424,'lon':-44.5647647,'osm_id':4348990589,'area_ha':538.5362},
'MG-3120904-2F5E1E03416543F8843E38F0C85160BB':{'name':'Fazenda Poço Azul','lat':-19.1497328,'lon':-44.629311,'osm_id':4348990600,'area_ha':1150.359},
'MG-3120904-BC49775212C74D359C8CF1AA80F921BD':{'name':'Fazenda Primavera','lat':-19.1907548,'lon':-44.632315,'osm_id':4348990601,'area_ha':656.1116},
'MG-3120904-578244BE1F434E63B34C1081AE0FA047':{'name':'Fazenda Quilombo de Geraldo Correia','lat':-19.1846751,'lon':-44.6608108,'osm_id':4348990603,'area_ha':454.079},
'MG-3120904-C461AEF2436049BF94832E85FD4BAFAC':{'name':'Fazenda Quilombo de Sadir Figueiredo','lat':-19.199347,'lon':-44.6827835,'osm_id':4348990604,'area_ha':127.0},
'MG-3120904-5B41B320596A4BB490A1FD46D2D8BB10':{'name':'Fazenda Rodrigues','lat':-19.1309209,'lon':-44.6620983,'osm_id':4348990607,'area_ha':54.6253},
'MG-3120904-8E7CC69719A74E9CA21321BF75D7E9AA':{'name':'Fazenda Sebastião','lat':-19.1147583,'lon':-44.5780684,'osm_id':4348990609,'area_ha':401.2488},
'MG-3120904-550F09F53C924D5AA02E50BD1B427972':{'name':'Fazenda Varginha','lat':-19.1349505,'lon':-44.4712951,'osm_id':4348990610,'area_ha':371.6617},
'MG-3120904-93B644185ADC47A78FE1A98BE0A98A4F':{'name':'Fazenda da Morada','lat':-19.0905892,'lon':-44.6766879,'osm_id':4348990611,'area_ha':143.2442},
'MG-3120904-54B43CD8E8DB47F8A39ED0CB2135E777':{'name':'Fazenda das Pedras','lat':-18.9999643,'lon':-44.7228648,'osm_id':4348990615,'area_ha':12101.294},
'MG-3120904-1622A283926042CA8F25CC6468B7FE8A':{'name':'Fazenda das Pedras de José Alves dos Santos','lat':-19.0627762,'lon':-44.4891425,'osm_id':4348990613,'area_ha':974.5408},
'MG-3120904-7370398AB44C4788ADBD541A2AD72601':{'name':'Fazenda das Pedras de Paulo A. dos Santos','lat':-19.0476864,'lon':-44.5027896,'osm_id':4348990614,'area_ha':149.976},
'MG-3120904-4903BEE63C064B12BE1B44B3F525DD4A':{'name':'Fazenda do Açude','lat':-19.1104848,'lon':-44.6710247,'osm_id':4348990616,'area_ha':201.7662},
'MG-3120904-D185FEAA940446BD9934921C292826CA':{'name':'Fazenda do Porto','lat':-18.4565292,'lon':-44.1969252,'osm_id':5600682098,'area_ha':765.4029},
'MG-3120904-DAB61237638D4448A17B26D2BCF9C216':{'name':'Fazenda dos Poções','lat':-18.4558371,'lon':-44.2515564,'osm_id':5600682094,'area_ha':609.2649},
'MG-3120904-B1765C0D0BC348ABB195ABAC0429FD28':{'name':'Sítio Paiol','lat':-18.8679407,'lon':-44.7359672,'osm_id':4348813082,'area_ha':106.6645},
}

# Explicitly ambiguous CAR from the same audit: two distinct public farm names fell
# inside this polygon, so no name may be promoted without stronger evidence.
CONFLICT_BY_CAR={
'MG-3120904-DB15158579EB4E07A0FE7ABFD922C7D3':{
    'names':['Fazenda Manga Grande','Retiro da Fazenda Manga Grande'],
    'osm_ids':[7156552944,7156552943],
}
}


def by_car(car_code:str):
    return SAFE_BY_CAR.get(str(car_code or '').upper())


def conflict_by_car(car_code:str):
    return CONFLICT_BY_CAR.get(str(car_code or '').upper())


def in_bbox(west:float,south:float,east:float,north:float,limit:int=80):
    out=[]
    for car,item in SAFE_BY_CAR.items():
        lat=float(item['lat']);lon=float(item['lon'])
        if west<=lon<=east and south<=lat<=north:
            out.append({'car_code':car,**item,'source':SOURCE})
    out.sort(key=lambda x:str(x['name']).casefold())
    return out[:max(1,min(int(limit),80))]

print('RX_PUBLIC_NAME_SEED_V43=curvelo_osm_car_audited_snapshot',flush=True)
