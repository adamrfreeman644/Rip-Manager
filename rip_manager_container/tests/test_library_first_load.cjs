const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/static/library.js'),'utf8');
async function scenario(responses){
 const elements=new Map(),statuses=[];let calls=0;
 const element=selector=>{
  if(!elements.has(selector)){
   let text='';const value={value:selector==='#librarySearch'?'Film':'',innerHTML:'',classList:{add(){},remove(){},toggle(){}},focus(){}};
   Object.defineProperty(value,'textContent',{get:()=>text,set:v=>{text=v;if(selector==='#libraryStatus')statuses.push(v)}});
   elements.set(selector,value);
  }return elements.get(selector);
 };
 const document={querySelector:element,querySelectorAll:()=>[],addEventListener(){}};
 const window={addEventListener(){}};
 vm.runInNewContext(source,{document,window,location:{pathname:'/library'},history:{pushState(){}},setTimeout:fn=>{fn();return 1},clearTimeout(){},fetch:async()=>{calls++;assert(responses.length,'unexpected repeated fetch');return{ok:true,json:async()=>responses.shift()}}});
 await window.openLibraryLookup(false);
 return {elements,statuses,calls};
}
(async()=>{
 const result=await scenario([{items:[],indexing:true,index_job:{status:'running',done:1,total:2,current:'Film'}},{items:[{id:1,title:'Film',media_type:'movie'}],indexing:false,index_job:null}]);
 assert.equal(result.calls,2);
 assert(result.statuses.some(x=>x.includes('Indexing library')&&x.includes('1 / 2')));
 assert(result.elements.get('#libraryResults').innerHTML.includes('Film'));
 const failure=await scenario([{items:[],indexing:false,index_job:{status:'failed',error:'storage offline'}}]);
 assert.equal(failure.calls,1);
 assert(failure.statuses.some(x=>x.includes('storage offline')&&x.includes('Check manifests')));
 console.log('Library first-load progress and failure tests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
