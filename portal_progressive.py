from __future__ import annotations

import portal_v8

app=portal_v8.app

PROGRESSIVE_JS=r'''
<style>
.rx-progress{margin:0 0 12px;border:1px solid #244136;background:#0b1b14;border-radius:12px;padding:11px 12px;display:flex;gap:10px;align-items:center}
.rx-progress-dot{width:9px;height:9px;border-radius:50%;background:#63e6a5;box-shadow:0 0 0 5px rgba(99,230,165,.10);flex:0 0 auto}.rx-progress.running .rx-progress-dot{animation:rxpulse 1.25s ease-in-out infinite}.rx-progress.warn .rx-progress-dot{background:#ffc866;box-shadow:0 0 0 5px rgba(255,200,102,.10)}
.rx-progress b{display:block;font-size:10px}.rx-progress small{display:block;color:#9bb1a6;font-size:9px;margin-top:3px;line-height:1.35}@keyframes rxpulse{50%{opacity:.35;transform:scale(.72)}}
.rx-quick-loader{padding:34px 18px}.rx-quick-loader h3{font-size:14px;margin:12px 0 4px}.rx-quick-loader p{font-size:10px;color:#9bb1a6;line-height:1.45;margin:0}.rx-bars{display:grid;gap:7px;margin-top:18px}.rx-bars i{height:8px;border-radius:7px;background:linear-gradient(90deg,#163427,#245840,#163427);background-size:200% 100%;animation:rxbar 1.2s linear infinite}.rx-bars i:nth-child(2){width:82%}.rx-bars i:nth-child(3){width:64%}@keyframes rxbar{to{background-position:-200% 0}}
</style>
<script>
(function(){
  let token=0;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  function progressBox(title,detail,state='running'){
    let box=document.querySelector('#rxProgressBox');
    if(!box){box=document.createElement('div');box.id='rxProgressBox';const host=document.querySelector('#pbody');if(host)host.prepend(box)}
    box.className='rx-progress '+state;
    box.innerHTML=`<span class="rx-progress-dot"></span><div><b>${title}</b><small>${detail}</small></div>`;
  }
  async function pollDeep(code,myToken){
    for(let i=0;i<48;i++){
      if(myToken!==token)return;
      await sleep(i<5?1200:1800);
      try{
        const r=await fetch(`/v1/live/progressive/status/${encodeURIComponent(code)}`,{cache:'no-store'});
        const d=await r.json();
        if(myToken!==token)return;
        if(d.state==='ready'){
          renderAnalysis({analysis:d.analysis});
          progressBox('Raio-X aprofundado concluído',`As fontes que responderam foram incorporadas em ${Math.max(0,Math.round((d.elapsed_ms||0)/1000))} s.`,'ready');
          return;
        }
        if(d.state==='failed'){
          progressBox('Resultado inicial disponível','A etapa aprofundada encontrou uma indisponibilidade externa. O que já foi confirmado permanece visível e nenhuma falha é tratada como ausência de risco.','warn');
          return;
        }
        progressBox('Resultado inicial pronto • aprofundando fontes',`Camadas ambientais, hídricas, produtivas e minerais estão sendo consultadas sem bloquear a tela. Etapa: ${d.stage||'processando'}.`,'running');
      }catch(e){
        progressBox('Resultado inicial pronto','O aprofundamento continua; houve uma falha temporária ao consultar o estado do processamento.','warn');
      }
    }
    progressBox('Resultado inicial disponível','Algumas fontes profundas continuam lentas. Você pode navegar normalmente; fonte pendente não é tratada como ausência de ocorrência.','warn');
  }
  async function progressiveAnalyze(){
    if(!current?.car_code)return;
    const myToken=++token,code=current.car_code;
    document.querySelector('#panel')?.classList.remove('hidden');
    const pt=document.querySelector('#ptitle');if(pt)pt.textContent=current?.name||code;
    const body=document.querySelector('#pbody');
    if(body)body.innerHTML='<div class="rx-quick-loader"><div class="spin"></div><h3>Montando a leitura inicial…</h3><p>O CAR entra primeiro. O aprofundamento só começou porque você pediu a análise completa.</p><div class="rx-bars"><i></i><i></i><i></i></div></div>';
    try{
      const r=await fetch(`/v1/live/quick/${encodeURIComponent(code)}?deep=1`,{cache:'no-store'});
      const d=await r.json();
      if(!r.ok)throw new Error(d.detail||'Não foi possível concluir a leitura inicial.');
      if(myToken!==token)return;
      renderAnalysis({analysis:d.analysis});
      const secs=Math.max(.1,(d.elapsed_ms||0)/1000).toFixed(1).replace('.',',');
      progressBox('Leitura inicial pronta',`Resultado essencial em ${secs} s. O aprofundamento continua em segundo plano.`,'running');
      pollDeep(code,myToken);
    }catch(e){
      if(myToken!==token)return;
      if(body)body.innerHTML=`<div class="row"><b class="warn">FONTE PRINCIPAL LENTA</b><br><span>${String(e.message||e)}. Tente novamente; nenhuma conclusão será inventada.</span></div>`;
    }
  }
  function install(){const b=document.querySelector('#analyze');if(b)b.onclick=progressiveAnalyze}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
  window.rxProgressiveAnalyze=progressiveAnalyze;
})();
</script>
'''

portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',PROGRESSIVE_JS+'</body>')
print('RX_PORTAL_PROGRESSIVE_V24=deep_only_on_explicit_analysis',flush=True)
