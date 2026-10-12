const assert=require('node:assert/strict');
const {create}=require('../notion_static/notion_database_range_history');
(async()=>{
  let scope='workspace/page',editable=true,writes=0,reject=false,pause=null;
  const database={id:'db',capabilities:{atomic_cell_batch:true},rows:[{id:'r',cells:{text:'original',number:1},cell_versions:{text:1,number:1}}]};
  const host={scope:()=>scope,canEdit:()=>editable,prepare:async()=>{},read:()=>database,changed:async()=>{},send:async action=>{
    if(pause)await pause;
    if(reject)throw Error('server failure');
    const cells=action.payload.cells,row=database.rows[0];
    for(const cell of cells)assert.equal(cell.expected_target_version,row.cell_versions[cell.property_id]);
    writes++;
    return {cells:cells.map(cell=>{row.cells[cell.property_id]=cell.value;return {...cell,target_version:++row.cell_versions[cell.property_id]};})};
  }};
  const history=create(host),cells=value=>[{row_id:'r',property_id:'text',value,expected_target_version:database.rows[0].cell_versions.text},{row_id:'r',property_id:'number',value:2,expected_target_version:database.rows[0].cell_versions.number}];
  assert.equal(history.canUndo('db'),false);
  await history.change('db',cells('first'));await history.change('db',cells('second'));
  assert.equal(history.canUndo('db'),true);
  await history.undo('db');assert.equal(database.rows[0].cells.text,'first');
  await history.undo('db');assert.deepEqual(database.rows[0].cells,{text:'original',number:1});
  await history.redo('db');await history.redo('db');assert.equal(database.rows[0].cells.text,'second');
  const before=writes;reject=true;
  await assert.rejects(history.undo('db'),/server failure/);assert.equal(writes,before);assert.equal(history.isBusy(),false);
  reject=false;await history.undo('db');assert.equal(database.rows[0].cells.text,'first');
  // An edit made by another participant must never be overwritten by our undo.
  database.rows[0].cells.text='remote';database.rows[0].cell_versions.text++;
  await assert.rejects(history.undo('db'),/변경되었습니다/);assert.equal(database.rows[0].cells.text,'remote');
  editable=false;await assert.rejects(history.change('db',cells('denied')),/권한/);editable=true;
  scope='workspace/another-page';assert.equal(history.canUndo('db'),false);assert.equal(await history.undo('db'),false);
  let release;pause=new Promise(resolve=>release=resolve);
  const pending=history.change('db',cells('once'));
  await assert.rejects(history.change('db',cells('twice')),/저장하고/);release();await pending;
  assert.equal(database.rows[0].cells.text,'once');
  console.log('UI-D03: atomic range history, successive undo/redo, failure retention, remote-edit conflict, scope isolation and in-flight exclusion passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
