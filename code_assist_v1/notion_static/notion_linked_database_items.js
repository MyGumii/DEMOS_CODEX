(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionLinkedDatabaseItems=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const clone=value=>structuredClone(value);
  const same=(a,b)=>JSON.stringify(a??null)===JSON.stringify(b??null);
  const rowId=row=>String(row?._canonical_row_id||'');
  function create(host){
    let opened=null,sequence=0;
    const live=s=>Boolean(s&&opened===s&&host.current(s.scope));
    const row=s=>host.rows(s.block).find(item=>rowId(item)===s.rowId);
    const permitted=s=>live(s)&&!s.unavailable&&host.canEdit(s.scope,s.data);
    function snapshot(){
      const s=opened;if(!s)return null;
      const item=s.unavailable?null:row(s),visible=item?host.visibleRows(s.block).map(entry=>rowId(entry.item)):[],position=visible.indexOf(s.rowId);
      return {...s,item,drafts:new Map(s.drafts),editable:permitted(s),busy:Boolean(s.flight||s.status.busy),
        previous:position>0,next:position>=0&&position<visible.length-1,position,total:visible.length};
    }
    const paint=focus=>{if(!opened?.composing||opened?.unavailable)host.render(snapshot(),focus);};
    function reset(){const prior=opened;opened=null;host.closed?.(prior);paint();}
    function invalidate(){sequence++;reset();}
    function update(scope,blockId,block,data){
      const s=opened;if(!s||s.blockId!==blockId||s.scope.pageId!==scope.pageId)return;
      if(!live(s)||!same(s.data.source,data.source)){invalidate();return;}
      s.block=clone(block);s.data=clone(data);s.unavailable='';
      if(!row(s)){s.unavailable='이 항목이 삭제되었거나 더 이상 접근할 수 없습니다.';s.drafts.clear();}
      if(!permitted(s))s.drafts.clear();
      paint();
    }
    function draft(field,value,expectedVersion=null){
      const s=opened;if(!permitted(s)||s.flight||s.status.busy||s.status.pending)return false;
      const item=row(s),property=host.properties(s.block).find(p=>p.id===field);
      if(!item||!property||!host.fieldEditable(property))return false;
      const before=s.drafts.get(field),base=s.data.database.rows.find(r=>r.id===s.rowId);
      const currentVersion=Number(base?.cell_versions?.[field]||0);
      if(expectedVersion!==null&&expectedVersion!==currentVersion){
        s.error='속성이 다른 곳에서 변경되었습니다. 선택창을 다시 열어 주세요.';paint();return false;
      }
      s.drafts.set(field,{value:clone(value),before:before?before.before:clone(base?.cells?.[field]??null),version:before?before.version:currentVersion});
      s.error='';return true;
    }
    async function saveProperties(){
      const s=opened;if(!s)return true;if(s.flight)return s.flight;
      if(!live(s))return false;
      if(s.composing){s.error='입력 중인 글자를 확정한 뒤 이동해 주세요.';paint();return false;}
      if(s.status.busy||s.status.pending){s.error='보관한 입력의 저장을 먼저 확인해 주세요.';paint();return false;}
      if(!s.drafts.size)return true;
      if(!permitted(s)){s.error='이 항목을 편집할 권한을 확인해 주세요.';paint();return false;}
      const submitted=new Map(s.drafts),cells=[];
      try{
        for(const [field,entry] of submitted){
          const property=host.properties(s.block).find(p=>p.id===field);
          if(!property||!host.fieldEditable(property))throw Error('속성이 변경되어 저장하지 못했습니다.');
          const value=host.value(property,entry.value,row(s));
          if(!same(value,entry.before))cells.push({row_id:s.rowId,property_id:field,value,expected_target_version:entry.version});
        }
      }catch(error){s.error=host.error(error);paint();return false;}
      if(!cells.length){s.drafts.clear();paint();return true;}
      s.error='';
      // Start on a microtask so reentrant close/navigation observes the flight.
      s.flight=Promise.resolve().then(()=>host.change(s.scope,s.blockId,cells)).then(()=>{
        if(live(s)){for(const [field,entry] of submitted)if(s.drafts.get(field)===entry)s.drafts.delete(field);s.error='';}
        return true;
      },error=>{if(live(s))s.error=host.error(error);return false;}).finally(()=>{s.flight=null;if(live(s))paint();});
      paint();return s.flight;
    }
    async function save(){
      const s=opened;
      if(s&&host.beforeSave&&(await host.beforeSave(s))===false)return false;
      if(opened!==s)return false;
      if(!s||!host.hasBody?.(s))return saveProperties();
      if(s.bodyFlight)return s.bodyFlight;
      s.bodyFlight=Promise.resolve().then(async()=>{
        if(!(await saveProperties())||!live(s))return false;
        return (await host.saveBody(s))!==false;
      }).finally(()=>{s.bodyFlight=null;});
      return s.bodyFlight;
    }
    async function close(restoreFocus=true){
      const s=opened;if(!s)return true;
      if(!(await save())||opened!==s)return false;
      if(s.status.pending||s.status.busy)return false;
      reset();if(restoreFocus)host.restoreFocus?.(s);return true;
    }
    async function open(scope,blockId,block,data,id,returnFocus){
      const token=++sequence;
      if(!(await close(false))||token!==sequence)return false;
      if(!host.current(scope)||!host.rows(block).some(item=>rowId(item)===id))return false;
      opened={scope,blockId,block:clone(block),data:clone(data),rowId:id,returnFocus,drafts:new Map(),status:host.status(scope,blockId),composing:false,error:'',unavailable:'',flight:null,mode:'side'};
      paint('title');return true;
    }
    async function navigate(delta){
      const s=opened;if(!s||!(await save())||!live(s))return false;
      const visible=host.visibleRows(s.block).map(entry=>rowId(entry.item)),position=visible.indexOf(s.rowId),next=position+delta;
      if(position<0||next<0||next>=visible.length)return false;
      s.rowId=visible[next];s.drafts.clear();s.error='';if(s.mode==='full')host.modeChanged?.(s,false);paint('title');return true;
    }
    function status(scope,blockId,status){
      const s=opened;if(!s||s.blockId!==blockId||s.scope.pageId!==scope.pageId||!live(s))return;
      const recovered=s.status.pending&&!status.pending&&!status.busy;
      s.status={...status};
      // Retrying/discarding a journaled request resolves those drafts; otherwise
      // an old cell version could submit the same acknowledged edit a second time.
      if(recovered&&!s.flight){s.drafts.clear();s.error='';}
      paint();
    }
    function unavailable(scope,blockId,message){
      const s=opened;if(!s||s.blockId!==blockId||s.scope.pageId!==scope.pageId)return;
      s.unavailable=message;s.drafts.clear();s.error='';s.block={props:{rows:[],properties:[]}};s.data={source:s.data.source,database:{rows:[]}};paint();
    }
    async function history(delta){
      const s=opened;if(!s||s.drafts.size||s.composing||s.flight||s.status.busy||s.status.pending||!permitted(s))return false;
      try{return await host.history(s.scope,s.blockId,delta);}catch(error){if(live(s)){s.error=host.error(error);paint();}return false;}
    }
    return {open,close,save,draft,navigate,update,status,unavailable,history,snapshot,isOpen:()=>Boolean(opened),
      invalidate,composing:value=>{if(opened)opened.composing=value;},
      pending:()=>Boolean(opened&&(opened.drafts.size||opened.composing||opened.flight||opened.bodyFlight||opened.status.busy||opened.status.pending||host.bodyPending?.(opened))),
      // Layout changes keep the same editor and draft; they need no mutation or save.
      mode:async(mode,push=true)=>{const s=opened;if(!s||!['side','full'].includes(mode)||!live(s))return false;s.mode=mode;host.modeChanged?.(s,push);paint();return true;},
      reconcile:(scope,ids)=>{if(opened&&(!live(opened)||opened.scope.pageId!==scope.pageId||!ids.includes(opened.blockId)))invalidate();},
      interacting:(scope,blockId)=>Boolean(opened&&live(opened)&&opened.scope.pageId===scope.pageId&&opened.blockId===blockId&&opened.composing),
      source:async()=>{const s=opened;if(s&&!s.unavailable&&await close(false)&&host.current(s.scope))await host.openSource(s.data.source.document_id);},
      retry:async()=>{const s=opened;if(!s||!live(s))return;try{await host.retry(s.scope,s.blockId);}catch(error){if(live(s)){s.error=host.error(error);paint();}}},
      discard:async()=>{const s=opened;if(!s||!live(s))return;try{await host.discard(s.scope,s.blockId);}catch(error){if(live(s)){s.error=host.error(error);paint();}}},
    };
  }
  return {create,rowId};
});
