(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionPageActivity=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  function lastEdit(value,sequence){
    if(value===null)return null;
    if(!Number.isSafeInteger(sequence)||sequence<1||!value||typeof value.actor_subject_id!=='string'||!value.actor_subject_id.trim()
      ||!Number.isSafeInteger(value.document_sequence)||value.document_sequence<1||value.document_sequence>sequence
      ||!Number.isSafeInteger(value.edited_at)||value.edited_at<0)return undefined;
    return {actor_subject_id:value.actor_subject_id,document_sequence:value.document_sequence,edited_at:value.edited_at};
  }
  function apply(page,fresh){
    if(!Object.hasOwn(fresh,'last_edit'))return;
    const edit=lastEdit(fresh.last_edit,fresh.document_sequence);
    if(edit!==undefined)page.last_edit=edit;
  }
  function footer(page,people,formatTime){
    const sequence=Math.max(Number(page.document_sequence||0),Number(page.activity_document_sequence||0));
    const edit=lastEdit(page.last_edit,sequence);
    if(!edit)return {byline:'최종 편집',time:formatTime(page.updated_at)};
    const person=people.find(user=>user?.id===edit.actor_subject_id);
    const name=person?.display_name||edit.actor_subject_id;
    return {byline:`${name} 최종 편집`,time:formatTime(edit.edited_at)};
  }
  function watch(host){
    let stopped=false,timer=null;
    async function tick(){
      timer=null;if(stopped||!host.current())return;
      try{if(host.visible())await host.read();}catch{ /* Keep the last confirmed metadata until the next read. */ }
      if(!stopped&&host.current())timer=host.setTimeout(tick,15000);
    }
    void tick();
    return {stop(){stopped=true;if(timer!==null)host.clearTimeout(timer);timer=null;}};
  }
  return {lastEdit,apply,footer,watch};
});
