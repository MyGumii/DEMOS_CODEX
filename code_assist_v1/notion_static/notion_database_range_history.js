(function(root,factory) {
  const api=factory();
  if(typeof module==='object' && module.exports) module.exports=api;
  root.NotionDatabaseRangeHistory=api;
})(typeof globalThis!=='undefined'?globalThis:this,function() {
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value));
  const key=cell=>JSON.stringify([cell.row_id,cell.property_id]);
  function create(host) {
    let scope='',busy=false;
    const histories=new Map(),versions=new Map();
    function refresh() {
      const current=host.scope();
      if(current!==scope) {scope=current;histories.clear();versions.clear();}
      return current;
    }
    function history(databaseId) {
      refresh();
      if(!histories.has(databaseId))histories.set(databaseId,{undo:[],redo:[]});
      return histories.get(databaseId);
    }
    async function execute(databaseId,cells,direction=0) {
      if(busy)throw Error('셀 변경을 저장하고 있습니다. 잠시 후 다시 시도하세요.');
      const token=refresh(),stack=history(databaseId);
      if(!token || !host.canEdit())throw Error('표를 편집할 권한이 없습니다.');
      const entry=direction<0?stack.undo.at(-1):direction>0?stack.redo.at(-1):null;
      if(direction && !entry)return false;
      busy=true;host.busy?.(true);
      try {
        await host.prepare();
        if(host.scope()!==token || !host.canEdit())throw Error('페이지가 변경되었습니다.');
        const database=host.read(databaseId);
        if(!database?.capabilities?.atomic_cell_batch)throw Error('일괄 셀 저장을 사용할 수 없습니다.');
        const requested=direction?(direction<0?entry.before:entry.after).map(cell=>({...cell,expected_target_version:versions.get(databaseId+key(cell))})):copy(cells);
        if(!requested.length || requested.length>Math.min(1000,Number(database.capabilities.max_cell_batch)||1000))throw Error('선택 범위가 너무 큽니다.');
        const before=requested.map(cell=>{
          const row=database.rows.find(row=>row.id===cell.row_id);
          if(!row || Number(row.cell_versions?.[cell.property_id]||0)!==cell.expected_target_version)throw Error('선택한 셀이 변경되었습니다. 최신 값을 확인한 뒤 다시 선택하세요.');
          return {row_id:cell.row_id,property_id:cell.property_id,value:copy(row.cells?.[cell.property_id]??null)};
        });
        const result=await host.send({database_id:databaseId,target_id:databaseId,command_kind:'database.cells.set',payload:{cells:requested}});
        if(host.scope()!==token)return true;
        for(const cell of result.cells)versions.set(databaseId+key(cell),cell.target_version);
        if(direction<0) {stack.undo.pop();stack.redo.push(entry);}
        else if(direction>0) {stack.redo.pop();stack.undo.push(entry);}
        else {
          stack.undo.push({before,after:requested.map(({expected_target_version,...cell})=>cell)});
          if(stack.undo.length>100)stack.undo.shift();
          stack.redo=[];
        }
        await host.changed(databaseId);
        return true;
      } finally {busy=false;host.busy?.(false);}
    }
    return {change:(databaseId,cells)=>execute(databaseId,cells),undo:databaseId=>execute(databaseId,null,-1),redo:databaseId=>execute(databaseId,null,1),
      canUndo:databaseId=>!busy && history(databaseId).undo.length>0,isBusy:()=>busy};
  }
  return {create};
});
