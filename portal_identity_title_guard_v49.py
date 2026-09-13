from __future__ import annotations

import portal_v8

# D1a/C2a: one identity contract for every visible title.
# V43 replaces window.showProperty without delegating, so the parse-time
# showProperty wrap alone never runs. This script is appended after V46, so the
# final window.rxV46SelectProperty (every polygon click, search result and CTA
# payload passes through it) and the V45 immediate panel renderer already exist
# here and are wrapped with sanitize directly.
UI = r'''
<script id="rxIdentityTitleGuardV49">
(function(){
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
  // Title rule: the validated name only under the contract; otherwise the CAR code.
  // The municipality is only a location line, never a title.
  function cardIdentity(p){
    const car=String(p?.car_code||'').trim().toUpperCase();
    const name=String(p?.validated_name||'').trim();
    const named=!!name&&contract({name,car_code:car,name_validation_status:p?.name_validation_status,validation_status:p?.validation_status,panel_name_eligible:p?.panel_name_eligible});
    const city=String(p?.municipality||'').trim(),uf=String(p?.uf||'').trim().toUpperCase();
    return {named,title:named?name:car,code:car,place:city?(uf?`${city} / ${uf}`:city):''};
  }
  window.rxIdentityTitleContractV49=contract;
  window.rxIdentitySanitizeV49=sanitize;
  window.rxCardIdentityC2=cardIdentity;

  const prior=(typeof showProperty==='function')?showProperty:null;
  if(prior)window.showProperty=showProperty=function(p,g){return prior(sanitize(p),g)};

  const select=window.rxV46SelectProperty;
  if(typeof select==='function'&&!select.__rxIdentitySanitizedV49){
    const selectWrapped=function(p,g,latlng){return select(sanitize(p),g,latlng)};
    selectWrapped.__rxIdentitySanitizedV49=true;
    window.rxV46SelectProperty=selectWrapped;
  }
  const render=window.rxV46RenderV45Immediate;
  if(typeof render==='function'&&!render.__rxIdentitySanitizedV49){
    const renderWrapped=function(p){return render(p?sanitize(p):p)};
    renderWrapped.__rxIdentitySanitizedV49=true;
    window.rxV46RenderV45Immediate=renderWrapped;
  }
})();
</script>
<!-- RX_IDENTITY_TITLE_GUARD_V49_WRAP -->
'''
if 'RX_IDENTITY_TITLE_GUARD_V49' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_IDENTITY_TITLE_GUARD_V49 --></body>')

print('RX_IDENTITY_TITLE_GUARD_V49=validated_identity_required_for_car_title wrap:rxV46SelectProperty,rxV46RenderV45Immediate',flush=True)
