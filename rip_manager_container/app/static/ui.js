/* Native top-layer dialog keeps confirmations above every app popup.
   Requests are queued; cancel/Escape always resolve false and focus returns. */
(()=>{
 const dialog=document.querySelector('#confirmationDialog'),message=document.querySelector('#confirmationMessage'),cancel=document.querySelector('#confirmationCancel'),accept=document.querySelector('#confirmationAccept');
 let queue=Promise.resolve();
 window.confirmAction=text=>{
  const request=()=>new Promise(resolve=>{
   const previous=document.activeElement;
   message.textContent=String(text);dialog.returnValue='';
   const finish=()=>{const approved=dialog.returnValue==='confirm';if(previous?.isConnected)previous.focus({preventScroll:true});resolve(approved)};
   dialog.addEventListener('close',finish,{once:true});
   dialog.showModal();cancel.focus({preventScroll:true});
  });
  const result=queue.then(request);queue=result.catch(()=>{});return result;
 };
 cancel.onclick=()=>dialog.close('cancel');accept.onclick=()=>dialog.close('confirm');
 dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close('cancel')});
 document.addEventListener('keydown',event=>{
  if(!dialog.open)return;
  if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();dialog.close('cancel')}
 },true);
})();
/* Only explicitly marked explanatory copy becomes contextual help.
   Live state, errors and safety warnings are never selected by class name. */
(()=>{
 const modal=document.querySelector('#infoModal'),body=document.querySelector('#infoBody'),heading=document.querySelector('#infoTitle'),closeButton=document.querySelector('#infoClose');
 let returnFocus=null;
 function close(){modal.classList.add('hidden');body.replaceChildren();if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});returnFocus=null;}
 function upgrade(root){
  if(!(root instanceof Element))return;
  const notes=[...(root.matches('[data-help-note]')?[root]:[]),...root.querySelectorAll('[data-help-note]')];
  for(const note of notes){
   if(note.id||note.hasAttribute('aria-live')||note.matches('[role="status"],[role="alert"],.bad,.warning-box,.storage-setup-result'))continue;
   const title=note.dataset.helpNote;
   const wrapper=document.createElement('div');wrapper.className='context-help';
   const button=document.createElement('button');button.type='button';button.className='context-help-button';button.setAttribute('aria-label','About '+title);button.setAttribute('aria-haspopup','dialog');button.title='About '+title;button.textContent='ⓘ';
   const caption=document.createElement('span');caption.textContent=title;
   const template=document.createElement('template');template.content.append(...note.childNodes);
   wrapper.append(button,caption,template);note.replaceWith(wrapper);
  }
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('.context-help-button');if(!button)return;
  returnFocus=button;heading.textContent=button.parentElement.querySelector('span').textContent;
  body.replaceChildren(button.parentElement.querySelector('template').content.cloneNode(true));modal.classList.remove('hidden');closeButton.focus({preventScroll:true});
 });
 closeButton.onclick=close;modal.onclick=e=>{if(e.target===modal)close()};
 document.addEventListener('keydown',event=>{
  if(modal.classList.contains('hidden'))return;
  if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close();return}
  if(event.key==='Tab'){
   const controls=[...modal.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')].filter(el=>!el.disabled&&el.tabIndex>=0&&el.getClientRects().length);
   const first=controls[0],last=controls.at(-1);
   if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
  }
 },true);
 upgrade(document.body);
 const observer=new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)upgrade(node)});
 observer.observe(document.body,{childList:true,subtree:true});
 window.uiHelp={upgrade};
})();
