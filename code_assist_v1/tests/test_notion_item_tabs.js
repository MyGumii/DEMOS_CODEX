'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const tabs=require('../notion_static/notion_database_item_tabs'),ops=require('../notion_static/notion_database_item_blocks'),core=require('../notion_static/notion_editor_core'),clipboard=require('../notion_static/notion_block_clipboard');
const b=(id,content=id,props={},type='paragraph')=>({id,type,content,props});
const tree=()=>[b('tabs','',{tabs:[{id:'a',name:'첫 탭'},{id:'b',name:'둘째 탭'}],active_tab_id:'a',children:[
 b('a','첫 글',{tab_group:'tabs',tab_id:'a',layout_group:'columns',layout_column:0,layout_columns:2}),
 b('b','둘째 글',{tab_group:'tabs',tab_id:'b'}),
 b('fold','접힌 글',{tab_group:'tabs',tab_id:'a',open:false,children:[b('inside','보존')]},'toggle'),
 b('nested','',{tab_group:'tabs',tab_id:'a',tabs:[{id:'n',name:'안쪽'}],active_tab_id:'n',children:[b('nested-text','안쪽 글',{tab_group:'nested',tab_id:'n'})]},'tabs')]},'tabs'),b('tail')];
function factory(){let n=0;return (type='paragraph',options={})=>core.createBlock(type,{...options,id:`fresh-${++n}`});}
const find=(rows,id)=>ops.entries(rows).find(e=>e.block.id===id)?.block;
test('UI-ITEM-TABS conversion wraps original content, rich formatting, children and local layout without mutation',()=>{
 const source=b('c','안내',{icon:'★',rich_text_html:'<b>안내</b>',layout_group:'g',layout_column:1,layout_columns:2,tab_group:'outer',tab_id:'x',children:[b('child','원문')]},'callout'),before=structuredClone(source);
 const out=tabs.initialize(source,factory()),child=out.props.children[0];
 assert.deepEqual(source,before);assert.equal(out.type,'tabs');assert.equal(out.props.tab_group,'outer');assert.equal(child.type,'callout');assert.equal(child.props.icon,'★');assert.equal(child.props.rich_text_html,'<b>안내</b>');assert.equal(child.props.tab_group,'c');assert.equal(child.props.tab_id,out.props.active_tab_id);assert.equal(child.props.children[0].content,'원문');assert.equal(child.props.layout_group,'g');
 const unwrapped=tabs.unwrap(out);assert.equal(unwrapped.props.tabs,undefined);assert.equal(unwrapped.props.children[0].props.tab_group,undefined);assert.equal(unwrapped.props.children[0].content,'안내');
});
test('UI-ITEM-TABS add, rename, select and order changes retain every other panel and original input',()=>{
 const original=tree(),before=structuredClone(original),createBlock=factory();
 let out=tabs.mutate(original,'tabs','add',{createBlock}),id=out.focusTabId;
 assert.equal(out.blocks[0].props.tabs.length,3);assert.equal(out.blocks[0].props.active_tab_id,id);assert.ok(out.blocks[0].props.children.some(c=>c.props.tab_id===id));
 out=tabs.mutate(out.blocks,'tabs','rename',{tabId:id,name:' 확인 ',createBlock});assert.equal(out.blocks[0].props.tabs.at(-1).name,'확인');
 out=tabs.mutate(out.blocks,'tabs','left',{tabId:id,createBlock});assert.equal(out.blocks[0].props.tabs[1].id,id);
 out=tabs.mutate(out.blocks,'tabs','select',{tabId:'b',createBlock});assert.equal(out.blocks[0].props.active_tab_id,'b');assert.equal(find(out.blocks,'a').content,'첫 글');assert.deepEqual(original,before);
});
test('UI-ITEM-TABS duplicate remaps nested owners and independent columns but never points back to source panel',()=>{
 const original=tree(),before=structuredClone(original),out=tabs.mutate(original,'tabs','duplicate',{tabId:'a',createBlock:factory()}),owner=out.blocks[0];
 const copies=owner.props.children.filter(c=>tabs.belongs(c,owner.id,out.focusTabId));assert.equal(copies.length,3);
 const nested=copies.find(c=>c.type==='tabs'),child=nested.props.children[0];assert.equal(child.props.tab_group,nested.id);assert.equal(child.props.tab_id,'n');
 assert.equal(nested.props.tab_group,'tabs');assert.equal(nested.props.tab_id,out.focusTabId);assert.notEqual(copies[0].props.layout_group,'columns');assert.equal(copies.find(c=>c.type==='toggle').props.open,false);
 const all=ops.entries(out.blocks).map(e=>e.block.id);assert.equal(new Set(all).size,all.length);assert.deepEqual(original,before);
});
test('UI-ITEM-TABS deleting a panel preserves unrelated and unassigned blocks, and last panel cannot be deleted',()=>{
 const original=tree();original[0].props.children.push(b('orphan','보관',{tab_id:'missing'}));
 const out=tabs.mutate(original,'tabs','remove',{tabId:'a',createBlock:factory()});assert.equal(out.blocks[0].props.active_tab_id,'b');assert.deepEqual(out.blocks[0].props.children.map(c=>c.id),['b','orphan']);assert.equal(tabs.mutate(out.blocks,'tabs','remove',{tabId:'b'}),null);
 assert.equal(original[0].props.children.length,5);
});
test('UI-ITEM-TABS moving and pasting across panel boundaries rebases only external membership',()=>{
 const original=tree(),moved=ops.move(original,['nested'],'b','after');assert.equal(find(moved.blocks,'nested').props.tab_id,'b');assert.equal(find(moved.blocks,'nested-text').props.tab_group,'nested');
 const outside=ops.move(moved.blocks,['nested'],'tail','after');assert.equal(find(outside.blocks,'nested').props.tab_group,undefined);assert.equal(find(outside.blocks,'nested-text').props.tab_group,'nested');
 const inside=ops.move(outside.blocks,['tail'],'tabs','inside');assert.equal(find(inside.blocks,'tail').props.tab_id,'a');
 const pasted=ops.insert(original,'b',[b('new')]);assert.equal(find(pasted.blocks,'new').props.tab_group,'tabs');assert.equal(find(pasted.blocks,'new').props.tab_id,'b');
 assert.equal(ops.move(original,['tabs'],'nested-text','after'),null);
});
test('UI-ITEM-TABS whole block duplicate rebinds tabs while retaining its existing outer column',()=>{
 const original=tree();original[0].props.layout_group='outside-columns';original[0].props.layout_column=0;
 const out=ops.duplicate(original,['tabs'],()=>factoryId()),copy=out.blocks[1];
 function factoryId(){factoryId.n=(factoryId.n||0)+1;return `copy-${factoryId.n}`;}
 assert.equal(copy.props.layout_group,'outside-columns');assert.equal(copy.props.children[0].props.tab_group,copy.id);assert.notEqual(copy.props.children[0].props.layout_group,'columns');
 const nested=copy.props.children.find(c=>c.type==='tabs');assert.equal(nested.props.children[0].props.tab_group,nested.id);
});
test('UI-ITEM-TABS keyboard step skips hidden panel neighbours and rich clipboard keeps tab trees independent',()=>{
 const original=tree(),moved=ops.step(original,['a'],['tabs','a','fold','nested','nested-text','tail'],1);
 assert.equal(find(moved.blocks,'a').props.tab_id,'a');assert.deepEqual(moved.blocks[0].props.children.map(c=>c.id),['b','fold','a','nested']);
 assert.equal(ops.step(original,['b'],['tabs','b','tail'],1),null);
 const encoded=clipboard.encode(original,{shape:'tree'});let n=0;
 const copied=clipboard.decode(encoded,{shape:'tree',createId:()=>`clip-${++n}`,allowedTypes:new Set(['tabs','paragraph','toggle'])});
 assert.equal(copied[0].props.children[0].props.tab_group,copied[0].id);assert.equal(copied[0].props.children.find(c=>c.type==='tabs').props.children[0].content,'안쪽 글');
});
test('UI-ITEM-TABS rendering hides only inactive panels, preserves orphan content and gates mutation controls',()=>{
 const source=fs.readFileSync(require.resolve('../notion_static/notion_database_item_editor'),'utf8'),escape=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const blocks=tree();blocks[0].props.children.push(b('orphan','별도 글',{tab_id:'missing'}));
 function render(editable,active){const ctx={core,tabs,tabViews:new Map([['tabs',active]]),escape,canEdit:()=>editable,types:new Set(['paragraph','tabs','toggle']),mediaTypes:new Set(),host:{html:b=>escape(b.content)}};vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('    function listHtml('),source.indexOf('    function draw(){')),ctx);return ctx.listHtml(blocks);}
 const html=render(true,'b');assert.match(html,/data-body-tab-panel="a" hidden/);assert.match(html,/data-body-tab-panel="b">/);assert.match(html,/data-body-tab-add/);assert.match(html,/별도 글/);assert.equal((html.match(/data-body-text="a"/g)||[]).length,1);
 const readonly=render(false,'a');assert.doesNotMatch(readonly,/data-body-tab-(?:menu|add|content)=|data-body-tab-add/);assert.match(readonly,/role="tab"/);assert.match(readonly,/data-body-tab-panel="b" hidden/);
});

test('UI-ITEM-TABS composing a Korean tab name keeps the input alive and commits only after composition',()=>{
 const source=fs.readFileSync(require.resolve('../notion_static/notion_database_item_editor'),'utf8');
 const events=[],actions=[];
 const input={value:'검증',matches:s=>s.includes('input')||s==='[data-body-tab-name]',closest:()=>null};
 const ctx={composing:false,inputRevision:0,timer:null,menu:{id:'tabs',tabId:'a',tabMenu:true},clearTimeout(){},
  host:{composing:v=>events.push(v)},popup:{contains:el=>el===input},colorMenu:{contains:()=>false},
  closeMenu:()=>events.push('closed'),focusTab(){},onInput:()=>events.push('input'),tabAction:(...args)=>actions.push(args)};
 vm.createContext(ctx);
 vm.runInContext(source.slice(source.indexOf('    function keydown('),source.indexOf('    const onInput='))+
  source.slice(source.indexOf('    const onCompositionStart='),source.indexOf('    const onPaste='))+
  ';this.start=onCompositionStart;this.end=onCompositionEnd;',ctx);
 const event=key=>({target:input,key,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
 ctx.start(event(''));assert.deepEqual(events,[true]);
 for(const key of ['Enter','Escape','Home']){const e=event(key);ctx.keydown(e);assert.equal(e.prevented,undefined);assert.equal(e.stopped,true);}
 assert.equal(actions.length,0);ctx.end(event(''));assert.deepEqual(events,[true,false,'input']);
 const home=event('Home');ctx.keydown(home);assert.equal(home.prevented,undefined);assert.equal(home.stopped,true);
 const enter=event('Enter');ctx.keydown(enter);assert.equal(enter.prevented,true);assert.equal(actions.length,1);assert.equal(actions[0][1],'rename');assert.equal(actions[0][2].name,'검증');
 for(const extra of [{isComposing:true},{keyCode:229}]){ctx.keydown({...event('Enter'),...extra});assert.equal(actions.length,1);}
});

test('UI-ITEM-TABS tab focus clears block selection while undo remains inside the item editor',()=>{
 const source=fs.readFileSync(require.resolve('../notion_static/notion_database_item_editor'),'utf8'),calls=[];
 const button={closest:s=>s==='[data-body-tab]'?button:null,focus:()=>calls.push('tab focus')};
 const ctx={composing:false,menu:null,selection:new Set(),canvas:{querySelector:()=>button,focus:()=>calls.push('canvas focus')},
  CSS:{escape:v=>v},select:ids=>calls.push(['selection',...ids]),restoreHistory:delta=>calls.push(['history',delta])};
 vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('    function focusTab('),source.indexOf('    function tabAction('))+
  source.slice(source.indexOf('    function keydown('),source.indexOf('    const onInput=')),ctx);
 ctx.focusTab('tabs','a');assert.deepEqual(calls,[['selection'],'tab focus']);
 const event={target:button,ctrlKey:true,key:'z',preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};
 ctx.keydown(event);assert.equal(event.prevented,true);assert.equal(event.stopped,true);assert.deepEqual(calls.slice(2),[['history',-1],'canvas focus']);
});
