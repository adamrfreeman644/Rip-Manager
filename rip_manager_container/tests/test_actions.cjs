const assert=require('node:assert/strict'),{run}=require('../app/static/actions.js');
const element=()=>({textContent:'Retry',disabled:false,hidden:true,dataset:{},attrs:{},classList:{remove(){}},setAttribute(k,v){this.attrs[k]=v},getAttribute(k){return this.attrs[k]??null},removeAttribute(k){delete this.attrs[k]}});
(async()=>{
 const button=element(),state=element();let release,calls=0;
 const pending=run(button,{key:'transfer:1',state,pending:'Retrying…',success:'Queued'},()=>{calls++;return new Promise(resolve=>release=resolve)});
 assert(button.disabled);assert.equal(button.attrs['aria-busy'],'true');assert.equal(state.dataset.phase,'pending');
 const duplicate=await run(element(),{key:'transfer:1',state,pending:'Retrying…'},()=>calls++);assert(duplicate.busy);assert.equal(calls,1);
 release({id:1});assert((await pending).ok);assert.equal(state.textContent,'Queued');assert.equal(button.textContent,'Retry');assert.equal(button.disabled,false);assert.equal(button.attrs['aria-busy'],undefined);
 const failed=await run(button,{state,pending:'Saving…',failure:'Save failed'},()=>{throw Error('<storage unavailable>')});assert.equal(failed.ok,false);assert.equal(state.textContent,'Save failed: <storage unavailable>');assert.equal(state.attrs.role,'alert');assert.equal(state.attrs['aria-live'],'assertive');assert(!button.disabled);
 assert((await run(button,{state,pending:'Retrying…',success:'Complete'},async()=>true)).ok);assert.equal(state.attrs.role,'status');assert.equal(state.dataset.phase,'success');
 await run(button,{pending:'Updating…',keepDisabled:true},async()=>true);assert(button.disabled);
 console.log('Action feedback, keyed duplicate suppression, failure recovery and busy state passed');
})().catch(e=>{console.error(e);process.exitCode=1});
