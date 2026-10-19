/* Real-browser GUI checks use synthetic API fixtures; no live media is touched. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const staticRoot=path.join(__dirname,'../app/static');
const long='A very long media title and folder name '.repeat(8);
const physical={id:1,manager_job_id:'node:disc',title:long,media_type:'movie',state:'complete',storage_available:true,extra_urls:['/fixture.png'],extra_sources:[{manager_job_id:'node:older',index:2}]};
const drives=Array.from({length:5},(_,i)=>({name:'DVD'+(5-i),node_id:'node',node_name:'Rip Node 1',node_online:true,exists:true,use_allowed:i!==1&&i!==4,tray:i===2||i===3?'open':'empty'}));
const settings={theme:'dark',nodes:[],dashboard_columns:1,dashboard_rows:5,dashboard_tiles:[],mover_node_mounts:{},mover_node_source_roots:{},mover_destination_folders:{movie:'Movies',tv:'TV',music:'Music',audiobook:'Audiobooks'},drive_preferences:{"node:DVD4":{enabled:false},"node:DVD1":{enabled:false}}};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname;
  const target=name.startsWith('/static/')?path.join(staticRoot,name.slice(8)):path.join(staticRoot,'index.html');
  try{const body=fs.readFileSync(target);res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':target.endsWith('.png')?'image/png':'text/html');res.end(body)}catch{res.writeHead(404);res.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser,activePage;
 try{
  browser=await chromium.launch({headless:true});
  for(const viewport of [{width:360,height:740},{width:692,height:720},{width:390,height:844},{width:844,height:390},{width:1280,height:800}]){
   const page=await browser.newPage({viewport});activePage=page;const errors=[],requests=[];let previewCount=0,downloadCount=0,transferReads=0,slowTransfers=false;
   page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>window.addEventListener('unhandledrejection',e=>{throw e.reason}));
   await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname;
    if(p.startsWith('/static/')||(!p.startsWith('/api/')&&!p.startsWith('/archive/')&&!['/auth/status','/settings','/overview','/drives','/jobs','/system/stats','/events'].includes(p)))return route.continue();
    requests.push({path:p,method:req.method(),body:req.postDataJSON(),query:url.searchParams.toString()});
    let data=[];
    if(p==='/auth/status')data={authenticated:true};
    else if(p==='/api/info')data={version:'test'};
    else if(p==='/settings')data=settings;
    else if(p==='/drives')data=drives;
    else if(p==='/overview')data={nodes:[],settings};
    else if(p==='/events')data=[];
    else if(p==='/api/collection')data={items:Array.from({length:30},(_,i)=>({...physical,id:i+1})),indexing:false};
    else if(p==='/api/collection/maintenance')data={status:'complete',kind:'index',result:{manifests_found:30,database_updated:30,skipped:0}};
    else if(p==='/archive/media')data=[physical];
    else if(p.endsWith('/text')&&req.method()==='PUT')return route.fulfill({status:422,json:{detail:'Storage disconnected'}});
    else if(p.endsWith('/text'))data={text:'Disc notes'};
    else if(p==='/archive/mover/transfers'){transferReads++;if(slowTransfers)await wait(500);data=[{id:1,title:long,state:'failed',error:long,destination_dir:'/media/'+long}]}
    else if(p==='/archive/mover/intake')data={root:'/media/Intake',items:[{id:'a',name:long,media_type:'movie',destination:'/media/'+long}]};
    else if(p==='/archive/mover/recovery')data=[{node_id:'node',folder:'Artist/Album/Disc 1',media_type:'music',files:2,bytes:100,destination:'/media/Music/'+long}];
    else if(p==='/archive/mover/recovery/analyse')data={ready:true,destination:'/media/Music/Album',files:[{path:'Disc 1/song.flac',bytes:100}]};
    else if(p==='/api/downloads/preview'){const count=++previewCount;await wait(count===1?350:10);data={title:count===1?'Stale preview':'Current preview'}}
    else if(p==='/api/downloads'&&req.method()==='POST'){downloadCount++;await wait(250);data={count:1}}
    else if(p==='/api/downloads')data=[{id:1,title:long,status:'completed',destination:'movie',media_type:'video',percent:100,error:'History needs repair',manager_job_id:'node:download',transfer:{state:'failed',destination_dir:'/media/'+long}}];
    else if(p.startsWith('/api/collection/')&&p.endsWith('/physical'))data=physical;
    else if(p.includes('/images/'))data={ok:true};
    return route.fulfill({json:data});
   });
   await page.goto(`http://127.0.0.1:${server.address().port}/`);
   await page.waitForFunction(()=>document.querySelector('#managerVersion').textContent==='vtest'&&document.querySelector('#loginModal').classList.contains('hidden'));
   await page.waitForSelector('button.drive');
   const tiles=await page.locator('button.drive').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return{bottom:r.bottom,right:r.right,sub:el.querySelector('.drive-sub').scrollHeight,space:el.querySelector('.drive-main').clientHeight}}));
   assert.equal(tiles.length,5);assert(tiles.every(t=>t.bottom<=viewport.height&&t.right<=viewport.width),'all five drives must fit the screen');
   const disabled=await page.locator('.drive.do-not-use .drive-sub').evaluateAll(els=>els.map(el=>({actual:el.scrollHeight,visible:el.clientHeight})));assert(disabled.every(x=>x.actual<=x.visible+1),'disabled drive instructions must not be cut off');
   const navigate=async name=>{await page.click('#mobileNavToggle');await page.click(`[data-mobile-action="${name}"]`)};
   await navigate('library');await page.waitForSelector('[data-library-id]');
   assert.equal(await page.locator('#libraryOverlay').evaluate(el=>el.parentElement.className),'app');
   const header=await page.locator('.v02-header').boundingBox(),library=await page.locator('#libraryOverlay').boundingBox();assert(library.y>=header.y+header.height-1,'Library must retain shared navigation');
   assert(await page.locator('#libraryResults').evaluate(el=>el.scrollHeight>el.clientHeight),'long collection must scroll rather than shrink rows');
   await page.click('#libraryMore');await page.click('#libraryCheck');await page.waitForFunction(()=>document.querySelector('#libraryToolState').textContent.includes('30 manifests checked'));
   await page.keyboard.press('Escape');assert(await page.locator('#libraryMoreModal').evaluate(el=>el.classList.contains('hidden')));
   await page.click('[data-library-id="1"]');await page.waitForSelector('#archiveDetail.active');
   const chooser=page.waitForEvent('filechooser');await page.click('[data-add="extra"][data-index="0"]');await (await chooser).setFiles({name:'new.png',mimeType:'image/png',buffer:Buffer.from('photo')});
   await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Photo saved');
   assert(requests.some(r=>r.path==='/archive/node%3Aolder/images/extra'&&r.method==='PUT'&&r.query==='index=2'),'replace must target the actual photo owner');
   await page.click('[data-detail-panel="archiveText"]');await page.waitForFunction(()=>document.querySelector('#archiveTextArea').value==='Disc notes');await page.click('#archiveSaveText');await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Storage disconnected');
   await page.click('#archiveBack');await page.waitForSelector('[data-library-id]');
   await navigate('mover');await page.waitForSelector('[data-recovery-analyse]');await page.click('[data-recovery-analyse]');await page.waitForSelector('#confirmRecoveryMove');assert.equal(requests.findLast(r=>r.path.endsWith('/recovery/analyse')).body.media_type,'music');await page.keyboard.press('Escape');
   // Leaving while Mover startup is pending must not restart its poll timer.
   slowTransfers=true;await navigate('dashboard');await navigate('mover');await navigate('library');await wait(700);const reads=transferReads;await wait(2200);assert.equal(transferReads,reads,'hidden Mover must stop polling');slowTransfers=false;
   await navigate('downloads');await page.waitForSelector('[data-download-intake="1"]');
   await page.fill('[name=url]','https://example.com/old');await page.click('#previewDownload');await page.fill('[name=url]','https://example.com/new');await wait(450);assert(await page.locator('#downloadPreview').evaluate(el=>el.hidden),'outdated preview must stay discarded');
   await page.locator('#downloadForm').evaluate(form=>{form.requestSubmit();form.requestSubmit()});await wait(400);assert.equal(downloadCount,1,'duplicate form submission must queue once');
   for(const theme of ['dark','light','system']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    for(const name of ['library','mover','downloads','stats','dashboard']){
     await navigate(name);await wait(50);
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${name} ${theme} must not overflow ${viewport.width}px`);
    }
   }
   await navigate('settings');await page.waitForSelector('#overlay:not(.hidden)');
   await page.evaluate(()=>openModal('Help','<p>Settings help</p>'));await page.keyboard.press('Escape');assert(await page.locator('#genericModal').evaluate(el=>el.classList.contains('hidden')));assert(await page.locator('#overlay').evaluate(el=>!el.classList.contains('hidden')),'Escape closes help before Settings');await page.keyboard.press('Escape');
   assert.deepEqual(errors,[],`Uncaught errors at ${viewport.width}x${viewport.height}`);
   fs.mkdirSync('gui-test-artifacts',{recursive:true});await page.screenshot({path:`gui-test-artifacts/dashboard-${viewport.width}x${viewport.height}.png`});
   await page.close();
  }
  console.log('GUI browser regressions passed: 5 viewports, 3 themes, all pages, navigation, recovery, photos, errors and download races');
 }catch(error){if(activePage&&!activePage.isClosed()){fs.mkdirSync('gui-test-artifacts',{recursive:true});await activePage.screenshot({path:'gui-test-artifacts/failure.png',fullPage:true});console.error(await activePage.locator('body').innerText())}throw error}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(e=>{console.error(e);process.exitCode=1});
