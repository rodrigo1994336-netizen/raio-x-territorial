from __future__ import annotations

import portal_v8


V45_SELECTION_FIX = r'''
<script id="rxMapPanelSelectionV45">
(function(){
  function install(){
    const base=window.showProperty;
    if(typeof base!=='function' || window.__rx45SelectionFixInstalled)return;
    window.__rx45SelectionFixInstalled=true;
    const wrapped=function(p,g){
      const selected={...(p||{}),geometry:g||(p||{}).geometry||null};
      window.current=selected;
      return base(p,g);
    };
    window.showProperty=wrapped;
    try{showProperty=wrapped}catch(e){}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
<!-- RX_MAP_PANEL_SELECTION_V45 -->
'''

portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", V45_SELECTION_FIX + "</body>")

print("RX_MAP_PANEL_SELECTION_V45=deterministic_showProperty_bridge_no_observer_no_polling", flush=True)
