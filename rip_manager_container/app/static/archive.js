/* Physical-media library and safe mover UI. */
(()=>{
const q=s=>document.querySelector(s), E=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let media=[],current=null,slot=null,refreshTimer=null;
const overlay=q("#archiveOverlay"), list=q("#archiveList"), file=q("#archiveFile");
q(".app").appendChild(overlay);
function request(path,options={}){return api(path,options)}
function showPanel(id){overlay.querySelectorAll(".archive-panel,.archive-head-tab").forEach(x=>x.classList.remove("active"));q("#"+id).classList.add("active");overlay.querySelector(`[data-archive-panel="${id}"]`)?.classList.add("active")}
function title(x){return `${x.title||"Untitled disc"}${x.year?` (${x.year})`:""}`}
function renderList(){const missing=media.filter(x=>!x.storage_available).length;q("#archiveCount").textContent=`${media.length} disc${media.length===1?"":"s"} · ${missing?`${missing} storage unavailable`:"storage healthy"}`;list.innerHTML=media.map((x,i)=>`<button class="archive-card${x.storage_available?"":" archive-card-unavailable"}" data-media="${i}"><span><span class="archive-card-title">${E(title(x))}</span><span class="archive-card-meta">${E(x.media_type||"disc")} · ${E(x.state||"")} · ${E(x.node_id||"")}</span>${x.storage_available?"":`<span class="archive-storage-warning">Storage unavailable · Expected ${E(x.expected_dir||"configured media folder")}</span>`}</span><span class="archive-statuses"><span class="archive-status"><small>Front</small><b class="${x.front_url?"archive-ok":"archive-missing"}">${x.front_url?"✓":"×"}</b></span><span class="archive-status"><small>Rear</small><b class="${x.rear_url?"archive-ok":"archive-missing"}">${x.rear_url?"✓":"×"}</b></span><span class="archive-status"><small>Extras</small><b>${x.extra_urls.length}</b></span></span></button>`).join("");}
async function loadMedia(){media=await request("/archive/media");renderList()}
function empty(label){return `<span><span class="archive-plus">＋</span><b>Tap to add</b>${label} photo</span>`}
function image(src,kind,index=""){return `<img src="${E(src)}?v=${Date.now()}" alt="${kind}"><span class="archive-image-actions"><button data-remove="${kind}" data-index="${index}">Delete</button><button data-add="${kind}" data-index="${index}">Replace</button></span>`}
function renderDetail(){const x=current,front=q("#archiveFront"),rear=q("#archiveRear");q("#archiveDetailName").textContent=title(x);q("#archiveDetailMeta").textContent=`${x.media_type||"disc"} · ${x.state||""} · UPC ${x.barcode||"not recorded"}`;q("#archiveBarcode").value=x.barcode||"";q("#archiveBarcodeState").textContent=x.barcode?"Recorded":"Not recorded";front.className=`archive-slot${x.front_url?" has":""}`;front.innerHTML=x.front_url?image(x.front_url,"front"):empty("front");front.toggleAttribute("data-add",!x.front_url);if(!x.front_url)front.dataset.add="front";rear.className=`archive-slot${x.rear_url?" has":""}`;rear.innerHTML=x.rear_url?image(x.rear_url,"rear"):empty("rear");rear.toggleAttribute("data-add",!x.rear_url);if(!x.rear_url)rear.dataset.add="rear";q("#archiveExtras").innerHTML=`<button class="archive-slot" data-add="extra">${empty("extra")}</button>`+x.extra_urls.map((src,i)=>`<span class="archive-slot has">${image(src,"extra",i)}</span>`).join("")}
async function openDetail(index){current=media[index];renderDetail();q("#archiveLibrary").classList.remove("active");q("#archiveDetail").classList.add("active");activateDetail("archivePhotos")}
function activateDetail(id){overlay.querySelectorAll(".archive-detail-panel,.archive-detail-tab").forEach(x=>x.classList.remove("active"));q("#"+id).classList.add("active");overlay.querySelector(`[data-detail-panel="${id}"]`)?.classList.add("active");if(id==="archiveText")loadText()}
async function loadText(){const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/text`);q("#archiveTextArea").value=data.text;q("#archiveTextState").textContent="No unsaved changes"}
async function saveBarcode(){const input=q("#archiveBarcode");const value=input.value.trim();if(value&&!/^\d+$/.test(value)){toast("UPC / EAN must contain digits only");input.focus();return}const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/barcode`,{method:"PUT",body:JSON.stringify({barcode:value||null})});current.barcode=data.barcode;const row=media.find(x=>x.manager_job_id===current.manager_job_id);if(row)row.barcode=data.barcode;q("#archiveBarcodeState").textContent=data.barcode?"Saved":"Cleared";q("#archiveDetailMeta").textContent=`${current.media_type||"disc"} · ${current.state||""} · UPC ${current.barcode||"not recorded"}`;toast(data.barcode?"UPC saved":"UPC cleared")}
async function saveText(){const text=q("#archiveTextArea").value;const data=await request(`/archive/${encodeURIComponent(current.manager_job_id)}/text`,{method:"PUT",body:JSON.stringify({text})});q("#archiveTextArea").value=data.text;q("#archiveTextState").textContent="Saved to disc-info.txt";toast("Disc information saved")}
function choose(kind){slot=kind;file.value="";file.click()}
file.onchange=()=>{const selected=file.files[0];if(!selected||!current||!slot)return;if(selected.size>20*1024*1024){toast("Photo must be 20 MB or smaller");return}const reader=new FileReader();reader.onload=async()=>{try{await request(`/archive/${encodeURIComponent(current.manager_job_id)}/images/${slot}`,{method:"PUT",body:JSON.stringify({data_url:reader.result})});await loadMedia();current=media.find(x=>x.manager_job_id===current.manager_job_id);renderDetail();toast("Photo added")}catch(e){toast(e.message)}};reader.readAsDataURL(selected)};
async function remove(kind,index){const suffix=kind==="extra"?`?index=${index}`:"";await request(`/archive/${encodeURIComponent(current.manager_job_id)}/images/${kind}${suffix}`,{method:"DELETE"});await loadMedia();current=media.find(x=>x.manager_job_id===current.manager_job_id);renderDetail();toast("Photo removed")}
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

async function loadTransfers(){const rows=await request("/archive/mover/transfers");q("#transferList").innerHTML=rows.length?rows.map(x=>{const p=x.bytes_total?Math.min(100,x.bytes_copied/x.bytes_total*100):0;return `<article class="transfer-card"><div class="transfer-top"><div><strong>${E(title(x))}</strong><small>${E(x.node_id)} → Byte-Me</small></div><b>${E(x.state)}</b></div><div class="transfer-track"><div class="transfer-fill" style="width:${p}%"></div></div><small>${formatBytes(x.bytes_copied)} / ${formatBytes(x.bytes_total)} · ${x.files_copied}/${x.files_total} files${x.error?` · ${E(x.error)}`:""}</small>${x.state==="failed"?`<button class="archive-button" data-retry="${x.id}">Retry</button>`:""}${x.state==="queued"?`<button class="archive-button" data-cancel="${x.id}">Remove</button>`:""}</article>`}).join(""):`<div class="note">No transfers queued. Completed rips will appear here when the mover is enabled.</div>`}
function markRoute(route){document.querySelectorAll("[data-app-route]").forEach(x=>x.classList.toggle("current-page",x.dataset.appRoute===route));q("#archiveButton")?.classList.toggle("current-page",route==="physical-media")}
async function renderRoute(route){
  clearInterval(refreshTimer);refreshTimer=null;markRoute(route);
  if(route==="dashboard"){overlay.classList.add("hidden");q("#driveGrid").classList.remove("hidden");document.title="Rip Remote";return}
  q("#driveGrid").classList.add("hidden");overlay.classList.remove("hidden");
  if(route==="mover"){q("#archiveHeading").textContent="Mover";q("#archiveSubheading").textContent="One folder at a time to Byte-Me";document.title="Mover · Rip Remote";showPanel("archiveTransfers");renderMoverShares();await loadTransfers();refreshTimer=setInterval(loadTransfers,2000);return}
  if(route==="stats"){q("#archiveHeading").textContent="Stats";q("#archiveSubheading").textContent="Live system health and drive performance";document.title="Stats · Rip Remote";showPanel("archiveStats");renderStatsPage();return}
  q("#archiveHeading").textContent="Physical media";q("#archiveSubheading").textContent="Photos and disc details";document.title="Physical Media · Rip Remote";showPanel("archiveLibrary");await loadMedia()
}
function routeFromPath(){return location.pathname==="/mover"?"mover":location.pathname==="/physical-media"?"physical-media":location.pathname==="/stats"?"stats":"dashboard"}
async function navigate(route,push=true){const path=route==="mover"?"/mover":route==="physical-media"?"/physical-media":route==="stats"?"/stats":"/";if(push&&location.pathname!==path)history.pushState({route},"",path);await renderRoute(route)}
window.openDashboard=()=>navigate("dashboard");
window.openArchive=()=>navigate("physical-media");
window.openMover=()=>navigate("mover");
window.openStats=()=>navigate("stats");
window.addEventListener("popstate",()=>renderRoute(routeFromPath()));
q("#archiveBack").onclick=()=>{q("#archiveDetail").classList.remove("active");q("#archiveLibrary").classList.add("active");current=null};q("#archiveSaveBarcode").onclick=saveBarcode;q("#archiveBarcode").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();saveBarcode()}});q("#archiveSaveText").onclick=saveText;q("#archiveTextArea").oninput=()=>q("#archiveTextState").textContent="Unsaved changes";
q("#archiveScan").onclick=async()=>{try{const result=await request("/archive/scan",{method:"POST"});await loadMedia();toast(`Scan complete · ${result.folders_seen} discovered · ${result.imported} imported · ${result.manifests_created||0} manifests created · ${result.manifests_updated||0} updated`)}catch(e){toast(e.message)}};
overlay.onclick=async e=>{const m=e.target.closest("[data-media]");if(m){openDetail(Number(m.dataset.media));return}const del=e.target.closest("[data-remove]");if(del){remove(del.dataset.remove,Number(del.dataset.index));return}const add=e.target.closest("[data-add]");if(add){choose(add.dataset.add);return}const tab=e.target.closest("[data-detail-panel]");if(tab){activateDetail(tab.dataset.detailPanel);return}const top=e.target.closest("[data-archive-panel]");if(top){showPanel(top.dataset.archivePanel);if(top.dataset.archivePanel==="archiveTransfers")loadTransfers();return}const retry=e.target.closest("[data-retry]");if(retry){await request(`/archive/mover/transfers/${retry.dataset.retry}/retry`,{method:"POST"});loadTransfers();return}const cancel=e.target.closest("[data-cancel]");if(cancel){await request(`/archive/mover/transfers/${cancel.dataset.cancel}/cancel`,{method:"POST"});loadTransfers()}};
renderRoute(routeFromPath());
})();
