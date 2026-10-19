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
const settings={theme:'dark',nodes:[{id:'node',name:'Rip Node 1',url:'http://node:8000',enabled:true}],dashboard_columns:1,dashboard_rows:5,dashboard_tiles:[],mover_node_mounts:{},mover_node_source_roots:{},mover_destination_folders:{movie:'Movies',tv:'TV',music:'Music',audiobook:'Audiobooks'},drive_preferences:{"node:DVD4":{enabled:false},"node:DVD1":{enabled:false}}};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname;
  const target=name.startsWith('/static/')?path.join(staticRoot,name.slice(8)):path.join(staticRoot,'index.html');
  try{const body=fs.readFileSync(target);res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':target.endsWith('.png')?'image/png':'text/html');res.end(body)}catch{res.writeHead(404);res.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser,activePage,activeDialog;
 try{
  browser=await chromium.launch({headless:true});
  for(const viewport of [{width:360,height:740},{width:692,height:720},{width:390,height:844},{width:844,height:390},{width:1280,height:800}]){
   const page=await browser.newPage({viewport});activePage=page;const errors=[],requests=[];let previewCount=0,downloadCount=0,transferReads=0,slowTransfers=false;
   page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>{activeDialog=dialog});
   await page.addInitScript(()=>window.addEventListener('unhandledrejection',e=>{throw e.reason}));
   await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname;
    if(p.startsWith('/static/')||(!p.startsWith('/api/')&&!p.startsWith('/archive/')&&!p.startsWith('/updates/')&&!p.startsWith('/nodes/')&&!['/auth/status','/settings','/overview','/drives','/jobs','/system/stats','/events'].includes(p)))return route.continue();
    requests.push({path:p,method:req.method(),body:req.postDataJSON(),query:url.searchParams.toString()});
    let data=[];
    if(p==='/auth/status')data={authenticated:true};
    else if(p==='/api/info')data={version:'test'};
    else if(p==='/settings')data=settings;
    else if(p==='/updates/status')data={manager:{installed:'test'},nodes:[],host_updater:{},rollback_backups:[]};
    else if(p.endsWith('/drive-mapping'))data={mappings:drives.map((d,i)=>({name:d.name,device:'/dev/sr'+i,exists:true})),available:[]};
    else if(p==='/drives')data=drives;
    else if(p==='/overview')data={nodes:[],settings};
    else if(p==='/events')data=[];
    else if(p==='/api/collection'){if(url.searchParams.get('q')==='progress check')await wait(600);data={items:Array.from({length:30},(_,i)=>({...physical,id:i+1})),indexing:false}}
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
   await page.evaluate(()=>{State.settings.drive_preferences['node:DVD4'].visible=false;State.settings.drive_preferences['node:DVD1'].visible=false;renderGrid()});
   assert.equal(await page.locator('button.drive').count(),3,'hide two drives and show only the working three');
   assert.equal(await page.locator('#driveGrid').evaluate(el=>el.style.getPropertyValue('--dashboard-rows')),'3');
   await page.evaluate(()=>{State.settings.drive_preferences['node:DVD4'].visible=true;State.settings.drive_preferences['node:DVD1'].visible=true;renderGrid()});
   assert.equal(await page.locator('button.drive').count(),5,'restore hidden drives without losing them');
   const navigate=async name=>{await page.click('#mobileNavToggle');await page.click(`[data-mobile-action="${name}"]`)};
   await navigate('library');await page.waitForSelector('[data-library-id]');
   assert.equal(await page.locator('#libraryOverlay').evaluate(el=>el.parentElement.className),'app');
   const header=await page.locator('.v02-header').boundingBox(),library=await page.locator('#libraryOverlay').boundingBox();assert(library.y>=header.y+header.height-1,'Library must retain shared navigation');
   assert(await page.locator('#libraryResults').evaluate(el=>el.scrollHeight>el.clientHeight),'long collection must scroll rather than shrink rows');
   await page.fill('#librarySearch','progress check');
   assert(await page.locator('#libraryProgress').isVisible(),'search feedback must appear immediately');
   assert.equal(await page.locator('#libraryResults').getAttribute('aria-busy'),'true');
   assert.equal(await page.locator('[data-library-id]').count(),30,'retain collection during loading');
   await page.waitForFunction(()=>document.querySelector('#libraryResults').getAttribute('aria-busy')==='false');
   assert(!(await page.locator('#libraryProgress').isVisible()));
   await page.click('#libraryMore');await page.click('#libraryCheck');await page.waitForFunction(()=>document.querySelector('#libraryToolState').textContent.includes('30 manifests checked'));
   await page.keyboard.press('Escape');assert(await page.locator('#libraryMoreModal').evaluate(el=>el.classList.contains('hidden')));
   await page.waitForFunction(()=>document.querySelector('#libraryResults').getAttribute('aria-busy')==='false');
   await page.click('[data-library-id="1"]');await page.waitForSelector('#archiveDetail.active');
   const chooser=page.waitForEvent('filechooser');await page.click('[data-add="extra"][data-index="0"]');await (await chooser).setFiles({name:'new.png',mimeType:'image/png',buffer:Buffer.from('photo')});
   await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Photo saved');
   assert(requests.some(r=>r.path==='/archive/node%3Aolder/images/extra'&&r.method==='PUT'&&r.query==='index=2'),'replace must target the actual photo owner');
   await page.click('[data-detail-panel="archiveText"]');await page.waitForFunction(()=>document.querySelector('#archiveTextArea').value==='Disc notes');await page.click('#archiveSaveText');await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Storage disconnected');
   await page.click('#archiveBack');await page.waitForSelector('[data-library-id]');
   await navigate('mover');await page.waitForSelector('[data-recovery-analyse]');
   const intakeHelp=page.getByRole('button',{name:'About Where to put imported media'});await intakeHelp.click();await page.waitForSelector('#infoModal:not(.hidden)');assert((await page.textContent('#infoBody')).includes('/media/0 ~ Intake/Movies'));
   await page.keyboard.press('Tab');assert.equal(await page.locator('#infoClose').evaluate(el=>el===document.activeElement),true,'info popup must contain keyboard focus');
   await page.keyboard.press('Escape');assert(await page.locator('#infoModal').evaluate(el=>el.classList.contains('hidden')));assert(await intakeHelp.evaluate(el=>el===document.activeElement),'closing help returns focus to its icon');assert(await page.locator('#archiveTransfers').isVisible());
await page.click('[data-recovery-analyse]');await page.waitForSelector('#confirmRecoveryMove');assert.equal(requests.findLast(r=>r.path.endsWith('/recovery/analyse')).body.media_type,'music');await page.keyboard.press('Escape');
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
     if(viewport.width===390&&theme==='light'){fs.mkdirSync('gui-test-artifacts',{recursive:true});await page.screenshot({path:`gui-test-artifacts/fluent-${name}.png`});}
    }
   }
   await page.evaluate(()=>toast('Notification layout check'));const notification=await page.locator('#toast').boundingBox(),appHeader=await page.locator('.v02-header').boundingBox();assert(notification.y>appHeader.y+appHeader.height,'notifications must not cover the app header');
   await navigate('settings');await page.waitForSelector('#overlay:not(.hidden)');
   const chooseCategory=async value=>{if(viewport.width<=760)await page.selectOption('#settingsCategory',value);else await page.click(`#settingsSidebar [data-settings-category="${value}"]`)};
   assert.equal(Math.round((await page.locator('.settings-window').boundingBox()).width),viewport.width,'Settings should fill the display');
   for(const theme of ['dark','light','system']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    for(const category of ['dashboard','hardware','ripping','mover','security','updates','system','home']){
     await chooseCategory(category);
     assert(await page.locator('.settings-main').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${category} settings must not overflow at ${viewport.width}px in ${theme}`);
     if(category==='system'){
      const spacing=await page.locator('#settingsContent .settings-nav-row').evaluateAll(els=>els.map(el=>({padding:parseFloat(getComputedStyle(el).paddingLeft),inset:el.querySelector('.setting-copy').getBoundingClientRect().left-el.getBoundingClientRect().left})));
      assert(spacing.length&&spacing.every(x=>x.padding>=14&&x.inset>=14),'navigation copy must have even internal padding');
      assert(await page.locator('#settingsContent .setting-desc').evaluateAll(els=>els.every(el=>Number(getComputedStyle(el).fontWeight)===400)),'secondary navigation copy should have regular weight');
      if(viewport.width===390&&theme==='light'){fs.mkdirSync('gui-test-artifacts',{recursive:true});await page.screenshot({path:'gui-test-artifacts/fluent-settings-system.png'});}
      const groups=await page.locator('#settingsContent .settings-page>.settings-group').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return{top:r.top,bottom:r.bottom}}));assert(groups.slice(1).every((r,i)=>r.top-groups[i].bottom>=12),'settings cards must have visible gaps');
     }
     const save=await page.locator('#settingsApply').boundingBox();assert(save.y+save.height<=viewport.height,'global Save must remain within the screen');
    }
   }
   await chooseCategory('dashboard');const cleanDraft=await page.evaluate(()=>JSON.stringify(State.draft));
   await page.getByRole('button',{name:'About Drive placement',exact:true}).click();await page.waitForSelector('#infoModal:not(.hidden)');assert((await page.textContent('#infoBody')).includes('A drive can only be assigned once'));await page.click('#infoClose');assert.equal(await page.evaluate(()=>JSON.stringify(State.draft)),cleanDraft,'help must not mutate settings');
   // Marked live messages and warnings are protected even if marked in error.
   await page.evaluate(()=>openModal('Help regression','<div data-help-note="Nested guide">Original <code>folder/path</code> guidance.</div><div id="liveHelpState" class="note" data-help-note="Live state">Scanning media…</div><div class="note bad" data-help-note="Warning">Verification failed: source retained.</div><div role="alert" data-help-note="Alert">Destination collision.</div>'));
   await page.getByRole('button',{name:'About Nested guide',exact:true}).click();assert((await page.textContent('#infoBody')).includes('folder/path'));await page.keyboard.press('Escape');assert(await page.locator('#genericModal').isVisible(),'help must not replace or dismiss the underlying dialog');assert(await page.locator('#liveHelpState').isVisible());assert(await page.getByText('Verification failed: source retained.',{exact:true}).isVisible());assert(await page.getByRole('alert').isVisible());await page.keyboard.press('Escape');
   await chooseCategory('home');
   const settingsRequests=()=>requests.filter(r=>r.path.startsWith('/nodes/')||r.path.startsWith('/updates/')).length;
   const beforeSearch=settingsRequests();await page.fill('#settingsSearch','English subtitles');assert.equal(settingsRequests(),beforeSearch,'typing search must not run settings actions or requests');const searchBox=await page.locator('#settingsSearch').boundingBox(),searchPanel=await page.locator('#settingsSearchPanel').boundingBox();assert(searchPanel.y>=searchBox.y+searchBox.height,'search results must appear below the search field');
   await page.keyboard.press('Enter');await page.waitForSelector('[data-toggle-key="prefer_english_subtitles"]');
   assert(await page.locator('[data-toggle-key="prefer_english_subtitles"]').evaluate(el=>el.closest('.setting-row').classList.contains('settings-found')));
   await page.click('[data-toggle-key="prefer_english_subtitles"]');assert(await page.locator('#settingsApply').isEnabled());
   await chooseCategory('dashboard');await page.click('#settingsBackHeader');assert.equal(await page.locator('[data-toggle-key="prefer_english_subtitles"]').getAttribute('aria-pressed'),'true','draft survives category navigation');
   await page.fill('#settingsSearch','hide drives');await page.click('#settingsSearchResults button:first-child');await page.waitForSelector('[data-drive-key="visible"]');
   await page.click('[data-drive-id="node:DVD4"][data-drive-key="visible"]');await chooseCategory('security');
   await page.fill('#newPin','2468');await page.fill('#confirmPin','2468');await chooseCategory('dashboard');await chooseCategory('security');assert.equal(await page.inputValue('#newPin'),'2468','PIN draft must survive navigation');
   await page.fill('#newPin','');await page.fill('#confirmPin','');
   await page.fill('#settingsSearch','unknown setting qqq');assert((await page.textContent('#settingsSearchStatus')).includes('No settings found'));await page.keyboard.press('Escape');assert(await page.locator('#overlay').isVisible(),'Escape clears search before closing Settings');
   await page.click('#settingsApply');await page.waitForSelector('#overlay.hidden',{state:'attached'});
   const saved=requests.findLast(r=>r.path==='/settings'&&r.method==='PUT').body;assert.equal(saved.prefer_english_subtitles,true);assert.equal(saved.drive_preferences['node:DVD4'].visible,false);assert(!saved.new_pin,'cleared PIN draft must not be submitted');
   await navigate('settings');await page.waitForSelector('#overlay:not(.hidden)');
   await page.fill('#settingsSearch','theme');await page.keyboard.press('Enter');await page.selectOption('[data-setting-input="theme"]','light');
   const dialog=page.waitForEvent('dialog').then(dialog=>dialog.dismiss());await page.click('#settingsClose');await dialog;assert(await page.locator('#overlay').isVisible(),'declining discard keeps unsaved settings');
   await page.selectOption('[data-setting-input="theme"]','dark');await chooseCategory('home');
   fs.mkdirSync('gui-test-artifacts',{recursive:true});await page.screenshot({path:`gui-test-artifacts/settings-${viewport.width}x${viewport.height}.png`});
   await page.evaluate(()=>openModal('Help','<p>Settings help</p>'));await page.keyboard.press('Escape');assert(await page.locator('#genericModal').evaluate(el=>el.classList.contains('hidden')));assert(await page.locator('#overlay').evaluate(el=>!el.classList.contains('hidden')),'Escape closes help before Settings');await page.keyboard.press('Escape');
   assert.deepEqual(errors,[],`Uncaught errors at ${viewport.width}x${viewport.height}`);
   fs.mkdirSync('gui-test-artifacts',{recursive:true});await page.screenshot({path:`gui-test-artifacts/dashboard-${viewport.width}x${viewport.height}.png`});
   await page.close();
  }
  console.log('GUI browser regressions passed: 5 viewports, 3 themes, all pages, navigation, recovery, photos, errors and download races');
 }catch(error){console.error('Original GUI failure:',error);if(activeDialog)await activeDialog.dismiss().catch(()=>{});if(activePage&&!activePage.isClosed()){fs.mkdirSync('gui-test-artifacts',{recursive:true});await activePage.screenshot({path:'gui-test-artifacts/failure.png',fullPage:true,timeout:5000}).catch(e=>console.error('Screenshot failed:',e.message));console.error(await activePage.locator('body').innerText())}throw error}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(e=>{console.error(e);process.exitCode=1});
