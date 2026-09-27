(()=>{
  const form=document.querySelector('#downloadForm');
  const list=document.querySelector('#downloadList');
  const preview=document.querySelector('#downloadPreview');
  const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const data=()=>{const values=Object.fromEntries(new FormData(form));return {
    url:values.url.trim(),media_type:values.media_type,quality:values.quality,
    destination:values.destination,subtitles:!!values.subtitles,
    playlist:!!values.playlist,custom_name:values.custom_name||''
  }};
  const call=(path,options={})=>api('/api/downloads'+path,options);
  const showError=error=>toast(error.message||String(error));
  form.elements.playlist.onchange=()=>{document.querySelector('#downloadNameRow').hidden=form.elements.playlist.checked};
  form.querySelector('#previewDownload').onclick=async()=>{
    try{
      preview.hidden=false;preview.textContent='Loading preview…';
      const item=await call('/preview',{method:'POST',body:JSON.stringify(data())});
      if(!form.elements.playlist.checked&&!form.elements.custom_name.value)form.elements.custom_name.value=item.title||'';
      preview.innerHTML=`${item.thumbnail?`<img class="download-thumb" src="${escapeHTML(item.thumbnail)}" alt="">`:''}<strong>${escapeHTML(item.title||'Untitled')}</strong>${item.duration?` · ${Math.round(item.duration/60)} min`:''}${item.playlist?' · playlist':''}`;
    }catch(error){preview.textContent=error.message;showError(error)}
  };
  form.onsubmit=async event=>{
    event.preventDefault();
    try{
      await call('',{method:'POST',body:JSON.stringify(data())});
      form.elements.url.value='';form.elements.custom_name.value='';
      preview.hidden=true;toast('Added to queue');await refresh();
    }catch(error){showError(error)}
  };
  async function refresh(){
    try{
      const rows=await call('');
      list.innerHTML=rows.length?rows.map(row=>{
        const percent=Math.max(0,Math.min(100,Number(row.percent)||0));
        const title=row.title||row.custom_name||'Fetching title…';
        const state=String(row.status||'queued');
        const detail=[`${row.media_type==='audio'?'Audio':'Video'} · ${row.destination}`,row.speed?`${(row.speed/1048576).toFixed(1)} MiB/s`:null,row.eta?`${Math.round(row.eta)}s left`:null].filter(Boolean).join(' · ');
        return `<article class="download-job"><div class="download-job-head"><div class="download-job-copy"><strong>${escapeHTML(title)}</strong><small>${escapeHTML(detail)}</small></div><span class="download-state download-state-${escapeHTML(state)}">${escapeHTML(state)}</span></div><div class="transfer-track" role="progressbar" aria-label="Download progress" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><div class="transfer-fill" style="width:${percent}%"></div></div><div class="download-job-foot"><small>${percent.toFixed(1)}%${row.error?` · ${escapeHTML(row.error)}`:''}</small><div class="download-job-actions">${state==='failed'?`<button type="button" data-download-retry="${row.id}">Retry</button>`:''}${state!=='running'?`<button type="button" data-download-remove="${row.id}">Remove</button>`:''}</div></div></article>`;
      }).join(''):'<div class="note">No downloads queued.</div>';
    }catch(error){list.textContent=error.message}
  }
  list.onclick=async event=>{
    const retry=event.target.closest('[data-download-retry]');
    const remove=event.target.closest('[data-download-remove]');
    if(!retry&&!remove)return;
    try{await call(`/${retry?.dataset.downloadRetry||remove.dataset.downloadRemove}${retry?'/retry':''}`,{method:retry?'POST':'DELETE'});await refresh()}catch(error){showError(error)}
  };
  window.refreshDownloads=refresh;
})();
