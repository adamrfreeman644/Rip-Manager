/* Physical-media library and safe mover UI. */
(()=>{
const q=s=>document.querySelector(s), E=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let media=[],current=null,slot=null,refreshTimer=null,fromLibrary=false;
const overlay=q("#archiveOverlay"), list=q("#archiveList"), file=q("#archiveFile");
q(".app").appendChild(overlay);
function request(path,options={}){return api(path,options)}
function showPanel(id){overlay.querySelectorAll(".archive-panel,.archive-head-tab").forEach(x=>x.classList.remove("active"));q("#"+id).classList.add("active");overlay.querySelector(`[data-archive-panel="${id}"]`)?.classList.add("active")}
function title(x){return `${x.display_title||x.title||"Untitled disc"}${x.year?` (${x.year})`:""}`}
function renderList(){
  const terms=q("#archiveSearch").value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const rows=media.map((x,i)=>({x,i})).filter(({x})=>{
    const text=[title(x),x.title,x.media_type,x.barcode,x.existing_dir,x.final_dir,x.output_dir,x.expected_dir].filter(Boolean).join(" ").toLocaleLowerCase();
    return terms.every(term=>text.includes(term));
  });
  const missing=media.filter(x=>!x.storage_available).length;
  q("#archiveCount").textContent=`${terms.length?`${rows.length} of `:""}${media.length} disc${media.length===1?"":"s"} · ${missing?`${missing} storage unavailable`:"storage healthy"}`;
  list.innerHTML=rows.map(({x,i})=>`<button class="archive-card${x.storage_available?"":" archive-card-unavailable"}" data-media="${i}"><span><span class="archive-card-title">${E(title(x))}</span><span class="archive-card-meta">${E(x.media_type||"disc")} · ${E(x.state||"")} · ${E(x.node_id||"")}</span>${x.attempt_count>1?`<span class="archive-attempt-summary">${E(attemptLabel(x))}</span>`:""}${x.storage_available?"":`<span class="archive-storage-warning">Storage unavailable · Expected ${E(x.expected_dir||"configured media folder")}</span>`}</span><span class="archive-statuses"><span class="archive-status"><small>Front</small><b class="${x.front_url?"archive-ok":"archive-missing"}">${x.front_url?"✓":"×"}</b></span><span class="archive-status"><small>Rear</small><b class="${x.rear_url?"archive-ok":"archive-missing"}">${x.rear_url?"✓":"×"}</b></span><span class="archive-status"><small>Extras</small><b>${(x.extra_urls||[]).length}</b></span></span></button>`).join("")||`<div class="archive-empty">${terms.length?"No matching media. Try another title, barcode or folder name.":"No physical media yet. Scan Byte-Me to find existing media."}</div>`;
}
function attemptLabel(x){return x.attempt_count>1?`${x.attempt_count} attempts · ${x.state==="complete"?"latest successful rip":"latest attempt"}${x.latest_attempt_state&&x.latest_attempt_state!==x.state?` · latest attempt: ${x.latest_attempt_state.replaceAll("_"," ")}`:""}`:""}
async function loadMedia(){media=await request("/archive/media");renderList()}
function empty(label){return `<span><span class="archive-plus">+</span><b>Tap to add</b>${label} photo</span>`}
function image(src,kind,index=""){return `<img src="${E(src)}?v=${Date.now()}" alt="${kind}"><span class="archive-image-actions"><button data-remove="${kind}" data-index="${index}">Delete</button><button data-add="${kind}" data-index="${index}">Replace</button></span>`}
function renderDetail(){const x=current,front=q("#archiveFront"),rear=q("#archiveRear");q("#archiveDetailName").textContent=title(x);q("#archiveDetailMeta").textContent=`${x.media_type||"disc"} · ${x.state||""} · UPC ${x.barcode||"not recorded"}${x.attempt_count>1?` · ${x.attempt_count} attempts`:""}`;q("#archiveBarcode").value=x.barcode||"";q("#archiveBarcodeState").textContent=x.barcode?"Recorded":"Not recorded";front.className=`archive-slot${x.front_url?" has":""}`;front.innerHTML=x.front_url?image(x.front_url,"front"):empty("front");front.toggleAttribute("data-add",!x.front_url);if(!x.front_url)front.dataset.add="front";rear.className=`archive-slot${x.rear_url?" has":""}`;rear.innerHTML=x.rear_url?image(x.rear_url,"rear"):empty("rear");rear.toggleAttribute("data-add",!x.rear_url);if(!x.rear_url)rear.dataset.add="rear";[front,rear].forEach(el=>{const adding=el.hasAttribute("data-add");el.toggleAttribute("tabindex",adding);if(adding)el.tabIndex=0;if(adding)el.setAttribute("role","button");else el.removeAttribute("role");el.setAttribute("aria-label",`${adding?"Add":""} ${el===front?"front":"rear"} photo`.trim())});q("#archiveExtras").innerHTML=`<button class="archive-slot" data-add="extra">${empty("extra")}</button>`+(x.extra_urls||[]).map((src,i)=>`<span class="archive-slot has">${image(src,"extra",i)}</span>`).join("")}
overlay.addEventListener("keydown",e=>{const target=e.target.closest("[data-add]");if(target&&target===e.target&&(e.key==="Enter"||e.key===" ")&&target.tagName!=="BUTTON"){e.preventDefault();choose(target.dataset.add)}});
async function openDetail(index){current=media[index];renderDetail();q("#archiveLibrary").classList.remove("active");q("#archiveDetail").classList.add("active");activateDetail("archivePhotos")}
function activateDetail(id){overlay.querySelectorAll(".archive-detail-panel,.archive-detail-tab").forEach(x=>x.classList.remove("active"));q("#"+id).classList.add("active");overlay.querySelector(`[data-detail-panel="${id}"]`)?.classList.add("active");if(id==="archiveText")loadText();if(id==="archiveManifest")window.renderLibraryManifest?.(current,q("#archiveManifest"))}
async function loadText(){const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/text`);q("#archiveTextArea").value=data.text;q("#archiveTextState").textContent="No unsaved changes"}
async function saveBarcode(){const input=q("#archiveBarcode");const value=input.value.trim();if(value&&!/^\d+$/.test(value)){toast("UPC / EAN must contain digits only");input.focus();return}const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/barcode`,{method:"PUT",body:JSON.stringify({barcode:value||null})});current.barcode=data.barcode;const row=media.find(x=>x.manager_job_id===current.manager_job_id);if(row)row.barcode=data.barcode;q("#archiveBarcodeState").textContent=data.barcode?"Saved":"Cleared";q("#archiveDetailMeta").textContent=`${current.media_type||"disc"} · ${current.state||""} · UPC ${current.barcode||"not recorded"}`;toast(data.barcode?"UPC saved":"UPC cleared")}
async function saveText(){const text=q("#archiveTextArea").value;const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/text`,{method:"PUT",body:JSON.stringify({text})});q("#archiveTextArea").value=data.text;q("#archiveTextState").textContent="Saved to disc-info.txt";toast("Disc information saved")}
function choose(kind){slot=kind;file.value="";file.click()}
file.onchange=()=>{const selected=file.files[0];if(!selected||!current||!slot)return;if(selected.size>20*1024*1024){toast("Photo must be 20 MB or smaller");return}const reader=new FileReader();reader.onload=async()=>{try{await request(`/archive/${encodeURIComponent(current.manager_job_id)}/images/${slot}`,{method:"PUT",body:JSON.stringify({data_url:reader.result})});await loadMedia();current={...current,...media.find(x=>x.manager_job_id===current.manager_job_id)};renderDetail();toast("Photo added")}catch(e){toast(e.message)}};reader.readAsDataURL(selected)};
async function remove(kind,index){
  const source=kind==="extra"?current.extra_sources?.[index]:null;
  const owner=source?.manager_job_id||current[`${kind}_image_job_id`]||current.manager_job_id;
  const suffix=kind==="extra"?`?index=${source?.index??index}`:"";
  const selectedId=current.manager_job_id;
  await request(`/archive/${encodeURIComponent(owner)}/images/${kind}${suffix}`,{method:"DELETE"});
  await loadMedia();current={...current,...media.find(x=>x.manager_job_id===selectedId)};renderDetail();toast("Photo removed")
}
function renderMoverShares(){
  const box=q("#moverShares");
  if(!box)return;
  const nodes=State.nodes.filter(node=>node.enabled&&!isSimulatorNode(node));
  box.innerHTML=nodes.length?nodes.map(node=>{
    const share=nodeShare(node);
    return `<div class="share-card">
      <div class="node-top"><strong>${E(node.name)}</strong><span class="status ${node.online?"":"offline"}">● ${node.online?"ONLINE":"OFFLINE"}</span></div>
      <div class="share-path">${E(share.unc)}</div>
      <div class="share-actions"><a class="primary" href="${E(share.link)}">Open share</a><button class="secondary" type="button" data-copy-share="${E(share.unc)}">Copy path</button></div>
    </div>`;
  }).join(""):`<div class="note">No real Rip Nodes are configured.</div>`;
  box.querySelectorAll("[data-copy-share]").forEach(button=>button.onclick=()=>copyShare(button.dataset.copyShare));
}


async function loadIntake(){
  const box=q("#intakeList");if(!box)return;
  try{
    const data=await request("/archive/mover/intake");
    box.innerHTML=data.items.length?data.items.map(x=>`<article class="transfer-card"><div class="transfer-top"><div><strong>${E(x.name)}</strong><small>${E(x.media_type)} · ${x.manifest?"JSON ready":"JSON missing"}</small></div><b>${x.needs_attention?"Needs attention":"Waiting"}</b></div><small>Destination: ${E(x.destination)}</small>${x.destination_exists?'<div class="note">Destination already exists. Nothing will be queued until the conflict is resolved.</div>':""}${x.invalid_manifest?'<div class="note">disc-info.json is invalid and will not be overwritten.</div>':""}<button class="archive-button" data-intake-queue="${E(x.id)}" ${x.needs_attention?"disabled":""}>Prepare & queue</button></article>`).join(""):`<div class="note">Intake is empty · ${E(data.root)}</div>`;
  }catch(e){box.innerHTML=`<div class="note">${E(e.message)}</div>`;}
}
async function loadRecovery(){
  const box=q("#recoveryList"); if(!box)return;
  box.innerHTML='<div class="note">Scanning configured Rip Node shares…</div>';
  try{
    const rows=await request("/archive/mover/recovery");
    box.innerHTML=rows.length?rows.map(x=>`<article class="transfer-card"><div class="transfer-top"><div><strong>${E(x.folder)}</strong><small>${E(x.node_id)} · ${E(x.media_type)} · ${x.files} file${x.files===1?"":"s"} · ${formatBytes(x.bytes)}</small></div><b>${x.destination_exists?"Already exists":"Waiting"}</b></div><small>Destination: ${E(x.destination)}</small><button class="archive-button" data-recovery-analyse data-node="${E(x.node_id)}" data-folder="${E(x.folder)}" data-media-type="${E(x.media_type)}" ${x.destination_exists?"disabled":""}>Review</button></article>`).join(""):'<div class="note">No stranded rips found.</div>';
  }catch(e){box.innerHTML=`<div class="note">${E(e.message)}</div>`;}
}
async function analyseRecovery(nodeId,folder,mediaType){
  try{
    const p=await request("/archive/mover/recovery/analyse",{method:"POST",body:JSON.stringify({node_id:nodeId,folder,media_type:mediaType,delete_source:true})});
    const rows=p.files.map(x=>`<tr><td>${E(x.path)}</td><td>${formatBytes(x.bytes)}</td></tr>`).join("");
    q("#genericModalTitle").textContent=`Recovery preview · ${folder}`;
    q("#genericModalBody").innerHTML=`<div class="note">The complete folder will be queued into the normal Mover unchanged. JSON, subtitles, artwork and nested season/album folders are included and SHA-256 verified before source cleanup.</div><div class="note">Destination: ${E(p.destination)}</div><div style="overflow:auto"><table class="archive-table"><thead><tr><th>File</th><th>Size</th></tr></thead><tbody>${rows}</tbody></table></div>${p.ready?'<button class="primary action-full" id="confirmRecoveryMove">Queue verified move</button>':""}`;
    q("#genericModal").classList.remove("hidden");
    const go=q("#confirmRecoveryMove");
    if(go)go.onclick=async()=>{if(!confirm(`Queue ${folder} for verified transfer to Byte-Me?`))return;go.disabled=true;go.textContent="Queueing…";try{await request("/archive/mover/recovery/move",{method:"POST",body:JSON.stringify({node_id:nodeId,folder,media_type:mediaType,delete_source:true})});closeModal("genericModal");toast(`${folder} queued in Mover`);await loadRecovery();await loadTransfers()}catch(e){go.disabled=false;go.textContent="Queue verified move";toast(e.message)}};
  }catch(e){toast(e.message)}
}

async function loadTransfers(){const rows=await request("/archive/mover/transfers");q("#transferList").innerHTML=transferView.render(rows)}
function markRoute(route){document.querySelectorAll("[data-app-route]").forEach(x=>x.classList.toggle("current-page",x.dataset.appRoute===route))}
async function renderRoute(route){
  clearInterval(refreshTimer);refreshTimer=null;if(route==="library"||route==="physical-media"){window.openLibraryLookup?.(false);return}window.hideLibraryLookup?.();markRoute(route);
  if(route==="dashboard"){overlay.classList.add("hidden");q("#driveGrid").classList.remove("hidden");document.title="Rip Remote";return}
  q("#driveGrid").classList.add("hidden");overlay.classList.remove("hidden");
  if(route==="downloads"){q("#archiveHeading").textContent="Downloads";q("#archiveSubheading").textContent="Server download queue";document.title="Downloads · Rip Remote";showPanel("archiveDownloads");window.refreshDownloads?.();refreshTimer=setInterval(()=>window.refreshDownloads?.(),2000);return}
  if(route==="mover"){q("#archiveHeading").textContent="Mover";q("#archiveSubheading").textContent="Verified transfers and existing-rip recovery";document.title="Mover · Rip Remote";showPanel("archiveTransfers");renderMoverShares();await loadTransfers();await loadIntake();await loadRecovery();refreshTimer=setInterval(loadTransfers,2000);return}
  if(route==="stats"){q("#archiveHeading").textContent="Stats";q("#archiveSubheading").textContent="Live system health and drive performance";document.title="Stats · Rip Remote";showPanel("archiveStats");renderStatsPage();return}
  q("#archiveHeading").textContent="Physical media";q("#archiveSubheading").textContent="Photos and disc details";document.title="Physical Media · Rip Remote";showPanel("archiveLibrary");await loadMedia()
}
function routeFromPath(){return location.pathname==="/downloads"?"downloads":location.pathname==="/mover"?"mover":location.pathname==="/physical-media"?"physical-media":location.pathname==="/stats"?"stats":location.pathname==="/library"?"library":"dashboard"}
async function navigate(route,push=true){if(route==="library"||route==="physical-media"){window.openLibraryLookup?.(push);return}const path=route==="downloads"?"/downloads":route==="mover"?"/mover":route==="physical-media"?"/physical-media":route==="stats"?"/stats":"/";if(push&&location.pathname!==path)history.pushState({route},"",path);await renderRoute(route)}
window.openDashboard=()=>navigate("dashboard");
window.openArchive=()=>window.openLibraryLookup?.();
window.openLibraryPhysical=async item=>{clearInterval(refreshTimer);fromLibrary=true;current=item;window.hideLibraryLookup?.();overlay.classList.remove("hidden");q("#archiveHeading").textContent="Library";q("#archiveSubheading").textContent="Photos and media details";showPanel("archiveDetail");renderDetail();activateDetail("archivePhotos")};
window.openMover=()=>navigate("mover");
window.openDownloads=()=>navigate("downloads");
window.openStats=()=>navigate("stats");
window.addEventListener("popstate",()=>renderRoute(routeFromPath()));
q("#archiveSearch").addEventListener("input",renderList);
q("#archiveBack").onclick=()=>{if(fromLibrary){fromLibrary=false;window.openLibraryLookup?.(false);return}q("#archiveDetail").classList.remove("active");q("#archiveLibrary").classList.add("active");current=null};q("#archiveSaveBarcode").onclick=saveBarcode;q("#archiveBarcode").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();saveBarcode()}});q("#archiveSaveText").onclick=saveText;q("#archiveTextArea").oninput=()=>q("#archiveTextState").textContent="Unsaved changes";
q("#archiveScan").onclick=async()=>{try{const result=await window.runLibraryMaintenance("scan",q("#archiveCount"));await loadMedia();toast(`Scan complete · ${result.folders_seen} discovered · ${result.imported} imported · ${result.manifests_created||0} manifests created · ${result.manifests_updated||0} updated`)}catch(e){toast(e.message)}};
q("#recoveryScan").onclick=loadRecovery;q("#intakeScan").onclick=loadIntake;const intakeAll=q("#intakeQueue");if(intakeAll)intakeAll.hidden=true;
overlay.onclick=async e=>{const intake=e.target.closest("[data-intake-queue]");if(intake){intake.disabled=true;try{await request(`/archive/mover/intake/${encodeURIComponent(intake.dataset.intakeQueue)}/queue`,{method:"POST"});toast("Intake item queued");await loadIntake();await loadTransfers()}catch(err){toast(err.message)}finally{intake.disabled=false}return}const recovery=e.target.closest("[data-recovery-analyse]");if(recovery){analyseRecovery(recovery.dataset.node,recovery.dataset.folder);return}const m=e.target.closest("[data-media]");if(m){openDetail(Number(m.dataset.media));return}const del=e.target.closest("[data-remove]");if(del){remove(del.dataset.remove,Number(del.dataset.index));return}const add=e.target.closest("[data-add]");if(add){choose(add.dataset.add);return}const tab=e.target.closest("[data-detail-panel]");if(tab){activateDetail(tab.dataset.detailPanel);return}const top=e.target.closest("[data-archive-panel]");if(top){showPanel(top.dataset.archivePanel);if(top.dataset.archivePanel==="archiveTransfers")loadTransfers();return}const retry=e.target.closest("[data-retry]");if(retry){await request(`/archive/mover/transfers/${retry.dataset.retry}/retry`,{method:"POST"});loadTransfers();return}const cancel=e.target.closest("[data-cancel]");if(cancel){await request(`/archive/mover/transfers/${cancel.dataset.cancel}/cancel`,{method:"POST"});loadTransfers()}};
renderRoute(routeFromPath());
})();
