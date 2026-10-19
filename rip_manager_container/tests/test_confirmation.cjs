const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const nodes=new Map(),events=new Map();let focus=0;
const previous={isConnected:true,focus(){focus++}};
for(const id of ['#confirmationDialog','#confirmationMessage','#confirmationCancel','#confirmationAccept'])nodes.set(id,{focus(){},addEventListener(name,fn){events.set(name,fn)}});
const dialog=nodes.get('#confirmationDialog');dialog.showModal=()=>{dialog.open=true};dialog.close=value=>{dialog.returnValue=value;dialog.open=false;events.get('close')()};
const window={},handlers={};
const source=fs.readFileSync(path.join(__dirname,'../app/static/ui.js'),'utf8').split('/* Only explicitly')[0];
vm.runInNewContext(source,{window,document:{querySelector:id=>nodes.get(id),activeElement:previous,addEventListener:(type,fn)=>handlers[type]=fn}});
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
(async()=>{
 const first=window.confirmAction('<script>unsafe</script>'),second=window.confirmAction('Second');await tick();
 assert.equal(nodes.get('#confirmationMessage').textContent,'<script>unsafe</script>','confirmation text remains plain text');
 let prevented=false,stopped=false;handlers.keydown({key:'Escape',preventDefault(){prevented=true},stopImmediatePropagation(){stopped=true}});
 assert.equal(await first,false);assert(prevented&&stopped);await tick();assert.equal(nodes.get('#confirmationMessage').textContent,'Second');
 nodes.get('#confirmationAccept').onclick();assert.equal(await second,true);assert.equal(focus,2);assert.equal(dialog.open,false);
 const third=window.confirmAction('Cancel');await tick();nodes.get('#confirmationCancel').onclick();assert.equal(await third,false);
 console.log('Confirmation queue, text safety, cancel, Escape and focus restoration passed');
})().catch(e=>{console.error(e);process.exitCode=1});
