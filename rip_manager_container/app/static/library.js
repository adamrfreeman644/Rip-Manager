(()=>{const q=s=>document.querySelector(s);const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(url,opt={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...opt});if(!r.ok)throw Error((await r.json().catch(()=>({}))).detail||r.statusText);return r.json()}
function render(items,term){q("#libraryStatus").textContent=items.length?items.length+" match"+(items.length===1?"":"es"):term?"NOT FOUND IN YOUR COLLECTION":"Search by title or scan a barcode";q("#libraryResults").innerHTML=items.map(x=>`<article class="archive-card"><div><strong>${esc(x.title||"Owned — metadata pending")}</strong><small>${esc(x.year||"")} ${esc(x.media_type||"")} · UPC ${esc(x.barcode||"not recorded")} · Extras: ${x.has_extras?"Yes":"No"}</small></div></article>`).join("")}
async function search(){const v=q("#librarySearch").value.trim();const d=await api("/library?q="+encodeURIComponent(v));render(d.items,v)}
async function check(){q("#libraryStatus").textContent="Checking manifests…";const d=await api("/library/check-manifests",{method:"POST"});q("#libraryStatus").textContent=`${d.manifests_found} manifests checked · ${d.database_updated} database records updated · ${d.skipped} skipped`;await search()}
async function bulk(){const d=await api("/library/bulk",{method:"POST",body:JSON.stringify({upcs:q("#libraryBulkText").value})});q("#libraryBulkState").textContent=`${d.submitted} submitted · ${d.added} added · ${d.already_owned} already owned · ${d.duplicates} duplicates · ${d.invalid} invalid`;await search()}
function open(){q("#libraryOverlay").classList.remove("hidden");history.pushState({library:true},"","/library");search();setTimeout(()=>q("#librarySearch").focus(),0)}
function close(){q("#libraryOverlay").classList.add("hidden");history.pushState({},"","/")}
q("#libraryHome").onclick=close;q("#libraryManifestCheck").onclick=check;q("#libraryBulkToggle").onclick=()=>q("#libraryBulk").classList.toggle("hidden");q("#libraryBulkAdd").onclick=bulk;let t;q("#librarySearch").oninput=()=>{clearTimeout(t);t=setTimeout(search,180)};
window.openLibraryLookup=open;
if(location.pathname==="/library")open();
})();
