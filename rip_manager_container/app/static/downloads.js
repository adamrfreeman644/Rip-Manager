(()=>{
  const form=document.querySelector('#downloadForm');
  const list=document.querySelector('#downloadList');
  const preview=document.querySelector('#downloadPreview');
  let previewedUrl='',previewedEntries=null,previewSequence=0,refreshSequence=0,submitting=false;
  const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const data=()=>{const values=Object.fromEntries(new FormData(form));return {
    url:values.url.trim(),media_type:values.media_type,quality:values.quality,
    destination:values.destination,subtitles:!!values.subtitles,
    playlist:!!values.playlist,custom_name:values.custom_name||''
  }};
  const call=(path,options={})=>api('/api/downloads'+path,options);
  const showError=error=>toast(error.message||String(error));
  const clearPreview=()=>{++previewSequence;previewedUrl='';previewedEntries=null;preview.hidden=true;preview.innerHTML='';preview.removeAttribute('aria-busy')};
  form.elements.url.addEventListener('input',clearPreview);
  form.elements.playlist.onchange=()=>{document.querySelector('#downloadNameRow').hidden=form.elements.playlist.checked;clearPreview()};
  form.querySelector('#previewDownload').onclick=async()=>{
    const ticket=++previewSequence,request=data();
    try{
      preview.hidden=false;preview.setAttribute('aria-busy','true');preview.textContent='Loading preview…';
      const item=await call('/preview',{method:'POST',body:JSON.stringify(request)});
      if(ticket!==previewSequence)return;
      uiActions.state(document.querySelector('#downloadActionState'),'Preview ready','success');
      if(item.playlist){
        previewedUrl=request.url;previewedEntries=item.entries;
        preview.innerHTML=`<div class="download-preview-head"><strong>${escapeHTML(item.title||'Playlist')}</strong><small>${item.entries.length} videos · untick any to leave out</small></div><div class="download-preview-entries">${item.entries.map(entry=>`<label><input type="checkbox" data-playlist-index="${entry.index}" checked><span>${escapeHTML(entry.index+'. '+entry.title)}</span></label>`).join('')}</div>`;
        return;
      }
      if(!form.elements.custom_name.value)form.elements.custom_name.value=item.title||'';
      preview.innerHTML=`${item.thumbnail?`<img class="download-thumb" src="${escapeHTML(item.thumbnail)}" alt="">`:''}<strong>${escapeHTML(item.title||'Untitled')}</strong>${item.duration?` · ${Math.round(item.duration/60)} min`:''}${item.playlist?' · playlist':''}`;
    }catch(error){if(ticket!==previewSequence)return;preview.textContent=error.message;uiActions.state(document.querySelector('#downloadActionState'),'Preview failed: '+error.message,'error');showError(error)}finally{if(ticket===previewSequence)preview.removeAttribute('aria-busy')}
  };
  form.onsubmit=async event=>{
    event.preventDefault();
    if(submitting)return;submitting=true;const button=form.querySelector('[type=submit]');button.disabled=true;button.setAttribute('aria-busy','true');const label=button.textContent;button.textContent='Adding to queue…';uiActions.state(document.querySelector('#downloadActionState'),'Adding download to queue…','pending');
    try{
      const request=data();
      if(request.playlist && previewedUrl===request.url && previewedEntries){
        request.selected_indices=[...preview.querySelectorAll('[data-playlist-index]:checked')].map(input=>Number(input.dataset.playlistIndex));
        if(!request.selected_indices.length){uiActions.state(document.querySelector('#downloadActionState'),'Select at least one video','error');toast('Select at least one video');return}
      }
      const result=await call('',{method:'POST',body:JSON.stringify(request)});
      form.elements.url.value='';form.elements.custom_name.value='';clearPreview();
      uiActions.state(document.querySelector('#downloadActionState'),`${result.count||1} download${result.count===1?'':'s'} added to queue`,'success');toast(`${result.count||1} video${result.count===1?'':'s'} added to queue`);await refresh();
    }catch(error){uiActions.state(document.querySelector('#downloadActionState'),'Download was not queued: '+error.message,'error');showError(error)}finally{submitting=false;button.disabled=false;button.textContent=label;button.removeAttribute('aria-busy')}
  };
  async function refresh(){
    const ticket=++refreshSequence;
    try{
      const rows=await call('');
      if(ticket!==refreshSequence)return;
      list.innerHTML=rows.length?rows.map(row=>{
        const percent=Math.max(0,Math.min(100,Number(row.percent)||0));
        const title=row.title||row.custom_name||'Fetching title…';
        const state=String(row.status||'queued');
        const detail=[`${row.media_type==='audio'?'Audio':'Video'} · ${row.destination}`,row.group_title?`Playlist: ${row.group_title}`:null,row.transfer?`Transfer: ${row.transfer.state}${row.transfer.destination_dir?' → '+row.transfer.destination_dir:''}`:null,row.speed?`${(row.speed/1048576).toFixed(1)} MiB/s`:null,row.eta?`${Math.round(row.eta)}s left`:null].filter(Boolean).join(' · ');
        return `<article class="download-job"><div class="download-job-head"><div class="download-job-copy"><strong>${escapeHTML(title)}</strong><small>${escapeHTML(detail)}</small></div><span class="download-state download-state-${escapeHTML(state)}">${escapeHTML(state)}</span></div><div class="transfer-track" role="progressbar" aria-label="Download progress for ${escapeHTML(title)}" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><div class="transfer-fill" style="width:${percent}%"></div></div><div class="download-job-foot"><small>${percent.toFixed(1)}%${row.error?` · ${escapeHTML(row.error)}`:''}</small><div class="download-job-actions">${state==='completed'&&row.error&&!['copying','verifying'].includes(row.transfer?.state)?`<button type="button" data-download-intake="${row.id}">${row.destination==='downloads'?'Repair history':'Retry Intake'}</button>`:''}${state==='failed'?`<button type="button" data-download-retry="${row.id}">Retry</button>`:''}${state!=='running'?`<button type="button" data-download-remove="${row.id}">Remove</button>`:''}</div></div></article>`;
      }).join(''):'<div class="note">No downloads queued.</div>';
    }catch(error){if(ticket===refreshSequence)list.textContent=error.message}
  }
  list.onclick=async event=>{
    const intake=event.target.closest('[data-download-intake]'),retry=event.target.closest('[data-download-retry]'),remove=event.target.closest('[data-download-remove]'),button=intake||retry||remove;
    if(!button)return;
    const id=intake?.dataset.downloadIntake||retry?.dataset.downloadRetry||remove.dataset.downloadRemove;
    const result=await uiActions.run(button,{key:'download:'+id,state:document.querySelector('#downloadActionState'),pending:remove?'Removing from queue…':'Requesting retry…',failure:remove?'Queue removal failed':'Retry request failed',success:remove?'Download removed from queue':'Retry requested. Check the download status for progress.'},()=>call(`/${id}${intake?'/retry-intake':retry?'/retry':''}`,{method:remove?'DELETE':'POST'}));
    if(result.ok)await refresh();
  };
  window.refreshDownloads=refresh;
})();
