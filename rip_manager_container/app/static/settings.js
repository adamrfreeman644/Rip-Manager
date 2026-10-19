/* Settings shell: navigation/search only. Existing pages own their controls. */
(()=>{
 const q=s=>document.querySelector(s);
 const categories=[
  ['home','⌂','Home','Your Rip Manager settings'],
  ['dashboard','▦','Personalisation','Dashboard layout, tile spacing and appearance'],
  ['hardware','▣','Nodes & drives','Connections, drive visibility, mapping and maintenance'],
  ['ripping','◉','Ripping & metadata','Disc handling, languages, barcodes and notifications'],
  ['mover','⇄','Storage & mover','Source folders, destinations and verified transfers'],
  ['security','◇','Privacy & security','PIN protection and access'],
  ['updates','↻','Updates & recovery','Manager and node updates, verified rollback'],
  ['system','⚙','System','Diagnostics, polling, developer tools and restart'],
 ];
 const parent={provider:'ripping','hardware-node':'hardware','hardware-drives':'hardware','add-node':'hardware','install-node-page':'hardware','connect-node':'hardware','simulator-manage':'hardware',advanced:'system',diagnostics:'system'};
 const entry=(title,route,keywords='',selector='',args=[])=>({title,route,keywords,selector,args});
 const field=(key)=>`[data-setting-input="${key}"]`,toggle=key=>`[data-toggle-key="${key}"]`;
 const catalogue=[
  entry('Dashboard rows, columns and drive placement','dashboard','grid tiles layout arrangement'),
  entry('Theme: light, dark or system','dashboard','appearance colour color personalisation',field('theme')),
  entry('Tile spacing','dashboard','gaps dashboard size',field('dashboard_spacing_percent')),
  entry('Show or hide drives on dashboard','hardware-drives','visibility hidden faulty drives working disabled','[data-drive-key="visible"]'),
  entry('Enable or disable a drive','hardware-drives','do not use faulty drive enabled','[data-drive-key="enabled"]'),
  entry('Drive mapping and tray controls','hardware-drives','USB udev DVD Blu-ray optical eject close assign swap'),
  entry('Add or connect a Rip Node','add-node','install adopt server SSH hostname'),
  entry('Built-in simulator','simulator-manage','demo demonstration fake drives reset'),
  entry('Barcode lookup','ripping','UPC EAN ISBN scan identification',toggle('upc_lookup')),
  entry('Minimum video title length','ripping','minutes extras trailers',field('minimum_video_minutes')),
  entry('Prefer English audio','ripping','language tracks',toggle('prefer_english_audio')),
  entry('Prefer English subtitles','ripping','language captions',toggle('prefer_english_subtitles')),
  entry('Verify completed rips','ripping','verification safety checks',toggle('verify_before_eject')),
  entry('Auto-eject after successful rip','ripping','tray open disc finished',toggle('auto_eject')),
  entry('Confirm manual eject','ripping','tray warning',toggle('confirm_eject')),
  entry('Browser sounds and notifications','ripping','alerts completion audio',toggle('sounds')),
  entry('Notification volume','ripping','sound loud quiet',field('volume')),
  ...[['musicbrainz','MusicBrainz music metadata'],['google_books','Google Books audiobook metadata'],['upcitemdb','UPCitemdb barcode metadata'],['omdb','OMDb movie and TV metadata']].map(([id,title])=>entry(title,'provider','API key provider lookup credentials','',[id])),
  entry('Verify storage and build library','mover','scan manifest Byte-Me mounted folders','[data-settings-action="verify-storage"]'),
  entry('Automatically queue completed rips','mover','mover enable transfers',toggle('mover_enabled')),
  entry('Remove verified source folder','mover','delete source cleanup verification',toggle('mover_delete_source')),
  entry('Byte-Me destination folders','mover','movies TV music audiobooks library path NAS',field('mover_destination_root')),
  entry('Rip Node source paths and mounted shares','mover','SMB mount folder network storage source'),
  entry('PIN protection','security','lock password access privacy security',toggle('lock_enabled')),
  entry('Set or change PIN','security','password unlock current new confirm','#newPin'),
  entry('Manager and Rip Node updates','updates','install latest software version update'),
  entry('Restore a verified backup','updates','rollback undo recovery previous version','#rollbackStatus'),
  entry('Run system diagnostics','diagnostics','troubleshoot health check online offline'),
  entry('Polling intervals','advanced','refresh seconds active idle',field('idle_poll_seconds')),
  entry('API developer tools','advanced','docs swagger technical'),
  entry('Restart Rip Manager','system','reboot server app','[data-settings-action="restart-manager"]'),
 ];
 let results=[],observer=null,focusTimer=null;
 const search=q('#settingsSearch'),panel=q('#settingsSearchPanel'),content=q('#settingsContent');
 const category=route=>parent[route]||route;
 const label=route=>categories.find(x=>x[0]===category(route))?.[2]||'Settings';
 function entries(){return [...categories.slice(1).map(([route,,title,description])=>entry(title,route,description)),...catalogue,...(State.draft?.nodes||[]).filter(n=>!isSimulatorNode(n)).flatMap(n=>{
  const index=State.draft.nodes.indexOf(n);
  return [entry(n.name+' connection, SMB and storage','hardware-node','node URL host network share path','',[index]),entry(n.name+' SMB network share','hardware-node','network username repair share setup','[data-settings-action="setup-node-smb"]',[index]),entry(n.name+' MakeMKV and dependencies','hardware-node','maintenance beta key software packages SSH update','#makemkvMaintenanceStatus',[index]),entry(n.name+' drive visibility and mapping','hardware-drives','hide show disabled drives USB optical','',[n.id])];
 })]}
 const normalise=v=>String(v).normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
 function find(term){const words=normalise(term).trim().split(/\s+/).filter(Boolean);if(!words.length)return[];return entries().filter(x=>words.every(w=>normalise(x.title+' '+x.keywords+' '+label(x.route)).includes(w))).sort((a,b)=>Number(normalise(b.title).includes(normalise(term)))-Number(normalise(a.title).includes(normalise(term))));}
 function clearSearch(){panel.classList.add('hidden');search.value='';search.setAttribute('aria-expanded','false');results=[];}
 function positionResults(){if(panel.classList.contains('hidden'))return;const anchor=search.getBoundingClientRect(),host=q('.settings-window').getBoundingClientRect(),footer=q('.settings-window-footer').getBoundingClientRect();panel.style.top=(anchor.bottom-host.top+6)+'px';panel.style.left=Math.max(10,anchor.left-host.left)+'px';panel.style.width=Math.min(520,host.right-anchor.left-16)+'px';panel.style.maxHeight=Math.max(0,Math.min(560,footer.top-anchor.bottom-14))+'px';}
 window.addEventListener?.('resize',positionResults);q('.settings-sidebar').addEventListener('scroll',positionResults);
 function showResults(){results=find(search.value);if(!search.value.trim()){clearSearch();return}panel.classList.remove('hidden');search.setAttribute('aria-expanded','true');q('#settingsSearchStatus').textContent=results.length?`${results.length} setting${results.length===1?'':'s'} found`:'No settings found. Try “hide drives”, “PIN”, “subtitles” or “updates”.';q('#settingsSearchResults').innerHTML=results.map((x,i)=>`<button type="button" data-settings-result="${i}"><strong>${esc(x.title)}</strong><small>${esc(label(x.route))}</small><span aria-hidden="true">›</span></button>`).join('');positionResults();}
 function focusTarget(selector){if(!selector)return;const focus=()=>{const el=content.querySelector(selector);if(!el)return false;const group=el.closest('.setting-row,.field,.settings-group')||el;group.classList.add('settings-found');group.scrollIntoView({block:'center'});el.focus?.({preventScroll:true});observer?.disconnect();clearTimeout(focusTimer);focusTimer=setTimeout(()=>group.classList.remove('settings-found'),3500);return true};if(!focus()){observer=new MutationObserver(focus);observer.observe(content,{childList:true,subtree:true});focusTimer=setTimeout(()=>observer?.disconnect(),5000)}}
 function select(route,args=[]){clearSearch();if(route==='home'){State.settingsNav=[];renderSettingsHome();return}navigateSettings(route,{args});}
 function dirty(){const changed=settingsDirty();q('#settingsApply').disabled=!changed||Boolean(State.settingsSaving);q('#settingsSaveState').textContent=changed?'You have unsaved changes':'Changes apply after saving';}
 function render(title){observer?.disconnect();clearTimeout(focusTimer);const route=State.settingsNav?.at(-1)?.page||'home',active=category(route);q('#settingsSidebar').innerHTML=categories.map(([id,icon,name])=>`<button type="button" data-settings-category="${id}" ${id===active?'aria-current="page"':''}><span aria-hidden="true">${icon}</span>${esc(name)}</button>`).join('');q('#settingsCategory').value=active;q('#settingsBreadcrumbs').innerHTML=route==='home'?'':`<button type="button" data-settings-category="home">Settings</button><span aria-hidden="true">›</span>${route!==active?`<button type="button" data-settings-category="${active}">${esc(label(route))}</button><span aria-hidden="true">›</span>`:''}<span aria-current="page">${esc(title)}</span>`;q('.settings-main').scrollTop=0;dirty();}
 function home(){return `<p class="settings-home-intro">Make Rip Manager work the way you want.</p><div class="settings-home-cards">${categories.slice(1).map(([route,icon,title,description])=>`<button type="button" class="settings-home-card" data-settings-page="${route}"><span class="settings-category-icon" aria-hidden="true">${icon}</span><span><strong>${esc(title)}</strong><small>${esc(description)}</small></span><span aria-hidden="true">›</span></button>`).join('')}</div>`}
 q('#settingsCategory').innerHTML=categories.map(([id,,title])=>`<option value="${id}">${esc(title)}</option>`).join('');q('#settingsCategory').onchange=e=>select(e.target.value);
 [q('#settingsSidebar'),q('#settingsBreadcrumbs')].forEach(el=>el.onclick=e=>{const b=e.target.closest('[data-settings-category]');if(b)select(b.dataset.settingsCategory)});
 search.oninput=showResults;search.onkeydown=e=>{if(e.key==='Escape'&&!panel.classList.contains('hidden')){e.stopPropagation();e.preventDefault();clearSearch()}if(e.key==='ArrowDown'||e.key==='Enter'){const first=q('#settingsSearchResults button');if(first&&!panel.classList.contains('hidden')){e.preventDefault();if(e.key==='Enter')first.click();else first.focus()}}};
 panel.onclick=e=>{const b=e.target.closest('[data-settings-result]');if(!b)return;const item=results[Number(b.dataset.settingsResult)];if(!item)return;select(item.route,item.args);focusTarget(item.selector)};
 panel.onkeydown=e=>{if(e.key==='Escape'){e.stopPropagation();clearSearch();search.focus()}};
 q('#settingsDiscard').onclick=()=>cancelSettings();q('#settingsApply').onclick=()=>{if(State.draft&&!q('#settingsApply').disabled)saveSettings()};
 q('#overlay').addEventListener('pointerdown',e=>{if(!e.target.closest('.settings-search-panel,.settings-search-box'))clearSearch()});
 window.settingsShell={render,dirty,home,find,begin(){clearSearch();q('#settingsVersion').textContent=q('#managerVersion').textContent;}};
})();
