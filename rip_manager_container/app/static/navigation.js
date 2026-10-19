/* Main pages share one history/guard/state coordinator. Popups remain local. */
(()=>{
 const paths={dashboard:'/',library:'/library',downloads:'/downloads',mover:'/mover',stats:'/stats',settings:'/preferences'};
 const handlers=new Map(),positions=new Map(),urls=new Map();
 const selectors={library:'#libraryResults',downloads:'#archiveDownloads',mover:'#archiveTransfers',stats:'#archiveStats',settings:'.settings-main'};
 const routeFromURL=()=>Object.keys(paths).find(key=>paths[key]===location.pathname)||(location.pathname==='/physical-media'?'library':'dashboard');
 let active=routeFromURL(),index=Number(history.state?.pageIndex)||0,sequence=0,undoing=false,currentURL=location.pathname+location.search;
 history.replaceState({...history.state,pageIndex:index},'',location.href);
 function remember(){const el=document.querySelector(selectors[active]||'#driveGrid');if(el)positions.set(active,el.scrollTop);urls.set(active,currentURL)}
 async function go(route,{push=true,popIndex=null}={}){
  if(!handlers.has(route))return;
  const ticket=++sequence;
  if(window.leaveMediaNotes&&!await window.leaveMediaNotes())return rejectPop();
  if(ticket!==sequence)return;
  if(window.leaveSettings&&route!=='settings'&&!await window.leaveSettings())return rejectPop();
  if(ticket!==sequence)return;
  remember();
  if(push){const url=urls.get(route)||paths[route];if(location.pathname+location.search!==url){index++;history.pushState({route,pageIndex:index},'',url)}}
  else if(popIndex!==null)index=popIndex;
  active=route;currentURL=location.pathname+location.search;
  document.querySelectorAll('[data-app-route]').forEach(el=>{const current=el.dataset.appRoute===route;el.classList.toggle('current-page',current);if(current)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});
  await handlers.get(route)(()=>ticket===sequence);
  if(ticket!==sequence)return;
  const el=document.querySelector(selectors[route]||'#driveGrid');if(el)el.scrollTop=positions.get(route)||0;
  function rejectPop(){if(popIndex!==null){const delta=index-popIndex;if(delta){undoing=true;history.go(delta)}}}
 }
 window.appNavigation={register:(route,handler)=>handlers.set(route,handler),go,rememberURL(){currentURL=location.pathname+location.search;urls.set(active,currentURL)},current:()=>active};
 window.addEventListener('popstate',event=>{if(undoing){undoing=false;return}go(routeFromURL(),{push:false,popIndex:Number(event.state?.pageIndex)||0}).catch(error=>window.toast?.(error.message))});
 window.addEventListener('rip-manager-ready',()=>go(routeFromURL(),{push:false}).catch(error=>window.toast?.(error.message)),{once:true});
})();
