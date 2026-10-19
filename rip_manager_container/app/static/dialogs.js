/* Shared focus containment for application popups; native dialogs own their top layer. */
(()=>{
 const backdrops=[...document.querySelectorAll('.modal-backdrop')],app=document.querySelector('.app'),original=new Map([app,...backdrops].map(el=>[el,el.inert]));
 let stack=[],lastControl=null;
 const controls=panel=>[...panel.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')].filter(el=>!el.disabled&&!el.closest('[inert]')&&el.tabIndex>=0&&el.getClientRects().length);
 const visible=el=>el?.isConnected&&!el.closest('.hidden,[inert]')&&el.getClientRects().length;
 function focusFirst(backdrop){const panel=backdrop.querySelector('[role="dialog"]')||backdrop,first=controls(panel)[0];if(first)first.focus({preventScroll:true});else{panel.tabIndex=-1;panel.focus({preventScroll:true})}}
 function sync(){
  const removed=stack.filter(entry=>entry.backdrop.classList.contains('hidden'));
  stack=stack.filter(entry=>!entry.backdrop.classList.contains('hidden'));
  for(const backdrop of backdrops){if(!backdrop.classList.contains('hidden')&&!stack.some(entry=>entry.backdrop===backdrop)){
   const previous=backdrop.contains(document.activeElement)?lastControl:document.activeElement;stack.push({backdrop,previous});
  }}
  const top=stack.at(-1)?.backdrop;
  for(const [el,value] of original)el.inert=top?(el!==top):value;
  if(top&&!top.contains(document.activeElement)&&!document.querySelector('dialog[open]')){
   const previous=removed.at(-1)?.previous;if(visible(previous)&&top.contains(previous))previous.focus({preventScroll:true});else focusFirst(top);
  }else if(!top&&removed.length){const previous=removed.at(-1).previous;if(visible(previous))previous.focus({preventScroll:true});else document.querySelector('#mobileNavToggle')?.focus({preventScroll:true})}
 }
 document.addEventListener('click',event=>{const target=event.target.closest('button,a,input,select,textarea');if(target)lastControl=target},true);
 document.addEventListener('keydown',event=>{
  const top=stack.at(-1)?.backdrop;if(!top||document.querySelector('dialog[open]')||event.key!=='Tab'||event.defaultPrevented)return;
  const panel=top.querySelector('[role="dialog"]')||top,items=controls(panel),first=items[0],last=items.at(-1);
  if(!first){event.preventDefault();focusFirst(top);return}
  if(event.shiftKey&&(document.activeElement===first||!top.contains(document.activeElement))){event.preventDefault();last.focus()}
  else if(!event.shiftKey&&(document.activeElement===last||!top.contains(document.activeElement))){event.preventDefault();first.focus()}
 },true);
 document.addEventListener('focusin',event=>{const top=stack.at(-1)?.backdrop;if(top&&!top.contains(event.target)&&!document.querySelector('dialog[open]'))focusFirst(top)});
 new MutationObserver(records=>{if(records.some(record=>backdrops.includes(record.target)))sync()}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['class']});
 sync();
})();
