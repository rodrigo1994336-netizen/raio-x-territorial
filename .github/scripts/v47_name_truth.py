"""Regression: audited OSM seeds remain references throughout identity and map APIs."""
from unittest.mock import patch

import property_identity_runtime as identity
import property_names_viewport_v30 as names
import portal_map_panel_v45 as panel


def check():
    for code, seed in identity.seed.SAFE_BY_CAR.items():
        result = identity._seed_identity(code, [])
        assert result['name'] is None and result['panel_name_eligible'] is False, result
        assert result['validation_status'] == 'UNVALIDATED', result
        assert result['geographic_reference_names'] == [seed['name']], result

    code = 'MG-3120904-82852A8D699342599B7B5B9FB04FC820'
    car = {'ok': True, 'properties': {'cod_imovel': code, 'municipio': 'Curvelo', 'uf': 'MG', 'area': 593.5167},
           'geometry': {'type': 'Polygon', 'coordinates': [[[-44.21,-18.49],[-44.19,-18.49],[-44.19,-18.47],[-44.21,-18.49]]]},
           'bbox': [-44.21,-18.49,-44.19,-18.47]}
    with patch.object(identity, 'fetch_car_live_resilient', return_value=car), patch.object(identity, '_sigef_candidates', return_value={'items': []}), patch.object(panel, 'fetch_car_live_resilient', return_value=car):
        identity._CACHE.clear()
        panel._CACHE.clear()
        result = panel._panel_sync(code)
        assert result['validated_name'] is None, result
        assert 'Fazenda Mocambo' in result['geographic_references'], result
        # A real explicit SICAR field must remain eligible.
        car['properties']['nome_imovel'] = 'Nome cadastral de teste'
        identity._CACHE.clear()
        direct = identity.resolve_property_identity_sync(code)
        assert direct['method'] == 'explicit_sicar_field' and direct['panel_name_eligible'] is True, direct

    # C2a: the panel exposes a name only under the full identity contract
    # (VALIDATED + eligible + non-empty name), and SICAR's creation date field is dat_criacao.
    dated = {'ok': True, 'properties': {'cod_imovel': code, 'municipio': 'Curvelo', 'uf': 'MG', 'area': 593.5167,
                                        'dat_criacao': '2025-09-10T23:38:30.026Z'},
             'geometry': car['geometry'], 'bbox': car['bbox']}
    unvalidated = {'ok': True, 'car_code': code, 'name': 'Nome de outro cadastro', 'panel_name_eligible': True,
                   'validation_status': 'UNVALIDATED', 'candidates': []}
    with patch.object(panel, 'fetch_car_live_resilient', return_value=dated), patch.object(panel, 'resolve_property_identity_sync', return_value=unvalidated):
        panel._CACHE.clear()
        result = panel._panel_sync(code)
        assert result['validated_name'] is None, result
        assert result['panel_name_eligible'] is False, result
        assert result['name_validation_status'] == 'UNVALIDATED', result
        assert result['created_at'] == '2025-09-10T23:38:30.026Z', result
    validated = dict(unvalidated, validation_status='VALIDATED', name='Nome cadastral validado')
    with patch.object(panel, 'fetch_car_live_resilient', return_value=dated), patch.object(panel, 'resolve_property_identity_sync', return_value=validated):
        panel._CACHE.clear()
        result = panel._panel_sync(code)
        assert result['validated_name'] == 'Nome cadastral validado', result
        assert result['panel_name_eligible'] is True and result['name_validation_status'] == 'VALIDATED', result
    panel._CACHE.clear()

    with patch.object(names, '_curl', return_value={'ok': True, 'json': {'features': []}}):
        names._CACHE.clear()
        result = names._query_names_sync(-44.21, -18.49, -44.19, -18.47)
        mocambo = [x for x in result['items'] if x['name'] == 'Fazenda Mocambo']
        assert len(mocambo) == 1, result
        assert mocambo[0]['reference_kind'] == 'OSM_AUDITED' and mocambo[0]['panel_name_eligible'] is False
        assert result['validated_count'] == 0, result
    print('RX_V47_AUDITED_OSM_REFERENCE_ONLY=PASS')


check()
