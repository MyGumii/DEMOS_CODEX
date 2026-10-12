'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../notion_static/notion_database_item_blocks.js');
const paragraph=(id,content=id)=>({id,type:'paragraph',content,props:{}});
function tree(){return [paragraph('a'),{id:'toggle',type:'toggle',content:'Parent',props:{children:[paragraph('child'),{id:'table',type:'simple_table',content:'',props:{rows:[['A','B'],['C','D']]}}]}},paragraph('z')];}
test('UI-ITEM-BLOCKS selection roots prevent duplicate child removal and preserve the other branches',()=>{
  const original=tree(),copy=structuredClone(original);
  assert.deepEqual(api.roots(original,['child','toggle','table','absent']),['toggle']);
  assert.deepEqual(api.remove(original,['child']).blocks[1].props.children.map(b=>b.id),['table']);
  assert.deepEqual(api.remove(original,['toggle','child']).blocks.map(b=>b.id),['a','z']);assert.deepEqual(original,copy);
});
test('UI-ITEM-BLOCKS multi-block move keeps nested content and canonical order',()=>{
  const original=tree(),copy=structuredClone(original),result=api.move(original,['a','toggle','child'],'z','after');
  assert.deepEqual(result.blocks.map(b=>b.id),['z','a','toggle']);assert.deepEqual(result.ids,['a','toggle']);
  assert.deepEqual(result.blocks.map(b=>b.position),[0,1,2]);assert.equal(result.blocks[2].props.children[1].props.rows[1][1],'D');assert.deepEqual(original,copy);
});
test('UI-ITEM-BLOCKS cross-level move can nest and unnest without duplication',()=>{
  const nested=api.move(tree(),['z'],'toggle','inside');assert.deepEqual(nested.blocks.map(b=>b.id),['a','toggle']);
  assert.deepEqual(nested.blocks[1].props.children.map(b=>b.id),['child','table','z']);assert.equal(nested.blocks[1].props.open,true);
  const extracted=api.move(nested.blocks,['child','table'],'a','before');assert.deepEqual(extracted.blocks.map(b=>b.id),['child','table','a','toggle']);
  assert.deepEqual(extracted.blocks[3].props.children.map(b=>b.id),['z']);
});
test('UI-ITEM-BLOCKS self/descendant drop, invalid target and ineffective movement are rejected',()=>{
  for(const [ids,target,placement] of [[['toggle'],'child','before'],[['toggle'],'table','after'],[['a'],'a','after'],[['z'],'a','inside'],[['a'],'missing','before'],[[],'a','before'],[['a'],'toggle','before']]){
    assert.equal(api.move(tree(),ids,target,placement),null);
  }
});
test('UI-ITEM-BLOCKS duplicate allocates fresh subtree IDs once and leaves the source untouched',()=>{
  let n=0;const original=tree(),result=api.duplicate(original,['toggle','child','z'],()=>`new-${++n}`),ids=api.entries(result.blocks).map(e=>e.block.id);
  assert.equal(ids.length,9);assert.equal(new Set(ids).size,9);assert.deepEqual(result.ids,['new-1','new-4']);
  assert.equal(result.blocks[2].props.children[1].props.rows[0][0],'A');assert.equal(original.length,3);
});
test('UI-ITEM-BLOCKS visible keyboard moves skip descendants of the selected parent',()=>{
  const result=api.step(tree(),['toggle'],['a','toggle','child','table','z'],1);assert.deepEqual(result.blocks.map(b=>b.id),['a','z','toggle']);
  assert.equal(api.step(tree(),['a'],['a','toggle','z'],-1),null);
  assert.deepEqual(api.step(tree(),['z'],['a','toggle','z'],-1).blocks.map(b=>b.id),['a','z','toggle']);
  assert.deepEqual(api.step(tree(),['z'],['a','toggle','child','table','z'],-1).blocks.map(b=>b.id),['a','z','toggle']);
});
test('UI-ITEM-BLOCKS selected parent and child copy text only once, including table cells',()=>{
  assert.equal(api.text(tree(),['toggle','child','table']),'Parent\nchild\nA\tB\nC\tD');
  assert.equal(api.remove(tree(),['a','toggle','z']).blocks.length,0);
});
