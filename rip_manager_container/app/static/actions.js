/* Shared, persistent feedback for user-triggered actions. No work is retried automatically. */
(function(root){
 const active=new Set();
 function state(element,message,phase){if(!element)return;element.hidden=false;element.classList.remove('hidden');if(element.textContent!==message)element.textContent=message;element.dataset.phase=phase;element.setAttribute('role',phase==='error'?'alert':'status');element.setAttribute('aria-live',phase==='error'?'assertive':'polite');element.setAttribute('aria-atomic','true')}
 async function run(button,options,work){
  const key=options.key||button;if(active.has(key))return {ok:false,busy:true};active.add(key);
  const label=button.textContent,disabled=button.disabled,oldBusy=button.getAttribute('aria-busy');let ok=false;
  button.disabled=true;button.setAttribute('aria-busy','true');button.textContent=options.pending;state(options.state,options.pending,'pending');
  try{const value=await work();ok=true;state(options.state,typeof options.success==='function'?options.success(value):options.success||'Complete','success');return {ok:true,value}}
  catch(error){state(options.state,(options.failure||'Action failed')+': '+(error.message||String(error)),'error');return {ok:false,error}}
  finally{active.delete(key);button.textContent=label;button.disabled=ok&&options.keepDisabled?true:disabled;if(oldBusy===null)button.removeAttribute('aria-busy');else button.setAttribute('aria-busy',oldBusy)}
 }
 const api={run,state};root.uiActions=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
