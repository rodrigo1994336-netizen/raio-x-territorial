from __future__ import annotations

import portal_v8

UI = r'''
<script>
(function(){
  const prior=(typeof showProperty==='function')?showProperty:null;
  if(!prior)return;
  function contract(p){
    const name=String(p?.name||'').trim();
    const car=String(p?.car_code||'').trim().toUpperCase();
    const status=String(p?.name_validation_status||p?.validation_status||'').trim().toUpperCase();
    return !!name&&!!car&&status==='VALIDATED'&&p?.panel_name_eligible===true;
  }
  function sanitize(p){
    const safe={...(p||{})};
    if(!contract(safe)){
      delete safe.name;delete safe.rx_name;delete safe.denominacao;
      delete safe.nome_imovel;delete safe.nome_area;delete safe.nome_fazenda;delete safe.nome_propriedade;
    }
    return safe;
  }
  window.showProperty=showProperty=function(p,g){return prior(sanitize(p),g)};
  window.rxIdentityTitleContractV49=contract;
  window.rxIdentitySanitizeV49=sanitize;
})();
</script>
'''
if 'RX_IDENTITY_TITLE_GUARD_V49' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_IDENTITY_TITLE_GUARD_V49 --></body>')

print('RX_IDENTITY_TITLE_GUARD_V49=validated_identity_required_for_car_title',flush=True)
