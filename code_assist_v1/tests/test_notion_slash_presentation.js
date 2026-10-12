'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const start=source.indexOf('let BlockSlashCombobox ='),end=source.indexOf('let BlockMenuReturnFocus = null;');
assert.ok(start>=0&&end>start,'Extract the actual presentation helpers');
const helpers=source.slice(start,end);
function fixture(){
  let rect={left:153,right:160,top:120,bottom:144,width:7,height:24},reads=[];
  const node={},fallback={getBoundingClientRect:()=>({left:20,top:80,bottom:160})};
  const editor={isConnected:true,textContent:'앞 문장 /제목',attributes:{role:'textbox','aria-label':'페이지 블록'},getAttribute(key){return this.attributes[key]??null;},setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];},getBoundingClientRect:()=>({left:100,top:100,bottom:150})};
  const context={document:{createRange:()=>({setStart:(node,offset)=>reads.push(['start',offset]),setEnd:(node,offset)=>reads.push(['end',offset]),getClientRects:()=>[rect]})},textPointAtOffset:(_editor,offset)=>({node,offset})};
  vm.createContext(context);vm.runInContext(helpers,context);
  return {context,editor,fallback,reads,move:value=>rect=value};
}
test('UI-SLASH-PRESENTATION menu follows the slash glyph rather than the block gutter through scroll and wrap',()=>{
  const f=fixture(),anchor=f.context.blockSlashAnchor(f.editor,f.fallback,{start:5});
  assert.equal(anchor.getBoundingClientRect().left,153);assert.deepEqual(f.reads,[['start',5],['end',6]]);
  f.move({left:100,right:107,top:40,bottom:64,width:7,height:24});assert.equal(anchor.getBoundingClientRect().top,40);assert.equal(anchor.getBoundingClientRect().left,100);
  assert.equal(f.editor.textContent,'앞 문장 /제목');
  f.editor.textContent='앞 문장';assert.equal(anchor.getBoundingClientRect().left,100);
  f.editor.isConnected=false;assert.equal(anchor.isConnected,false);assert.equal(anchor.getBoundingClientRect().left,20);
});
test('UI-SLASH-PRESENTATION inline combobox exposes the menu and restores the original editor attributes',()=>{
  const f=fixture(),before={...f.editor.attributes};f.context.setBlockSlashCombobox(f.editor);
  assert.equal(f.editor.attributes.role,'combobox');assert.equal(f.editor.attributes['aria-controls'],'blockMenuOptions');assert.equal(f.editor.attributes['aria-expanded'],'true');
  f.editor.setAttribute('aria-activedescendant','option-heading');f.context.setBlockSlashCombobox(f.editor);
  f.context.setBlockSlashCombobox(null);assert.deepEqual(f.editor.attributes,before);assert.equal(f.editor.textContent,'앞 문장 /제목');
});
test('UI-SLASH-PRESENTATION moving to another editor clears the previous active descendant',()=>{
  const f=fixture(),g=fixture();f.context.setBlockSlashCombobox(f.editor);f.editor.setAttribute('aria-activedescendant','old');
  f.context.setBlockSlashCombobox(g.editor);assert.equal(f.editor.attributes.role,'textbox');assert.equal(f.editor.attributes['aria-activedescendant'],undefined);assert.equal(g.editor.attributes.role,'combobox');
  f.context.setBlockSlashCombobox(null);assert.equal(g.editor.attributes.role,'textbox');
});
test('UI-SLASH-PRESENTATION a saved projection rebinds the replaced editor without leaving a stale menu anchor',()=>{
  const f=fixture(),g=fixture();let placements=0,closed=0;
  const menu={hidden:false},row={querySelector:()=>g.editor};
  Object.assign(f.context,{$:()=>menu,State:{activePageId:'p',pageData:{blocks:[{id:'b'}]},menuMode:'slash',slashSourceIndex:0,slashToken:{blockId:'b',start:5,end:8,text:'/제목'}},activePageCanEdit:()=>true,closeBlockMenu:()=>closed++,refreshBlockMenuSelection:()=>{},window:{NotionUiInteraction:{place:(_menu,anchor)=>{assert.equal(anchor.isConnected,true);placements++;}}}});
  f.context.document.querySelector=()=>row;vm.runInContext("BlockSlashPageId='p'",f.context);f.context.setBlockSlashCombobox(f.editor);
  f.editor.isConnected=false;f.context.rebindBlockSlashMenu();assert.equal(closed,0);assert.equal(placements,1);assert.equal(f.editor.attributes.role,'textbox');assert.equal(g.editor.attributes.role,'combobox');
  g.editor.textContent='remote replacement';f.context.rebindBlockSlashMenu();assert.equal(closed,1);assert.equal(g.editor.textContent,'remote replacement');
});

test('UI-SLASH-PRESENTATION Escape from the menu footer restores the source caret and preserves the slash text',()=>{
  const f=fixture(),focus=[],menu={hidden:false,querySelector:()=>null};
  Object.assign(f.context,{$:selector=>selector==='#blockMenu'?menu:null,State:{slashSourceIndex:0,slashToken:{end:8},pageData:{blocks:[{content:f.editor.textContent}]},menuQuery:'제목'},focusBlock:(...args)=>focus.push(args)});
  vm.runInContext('let BlockMenuReturnFocus = null;'+source.slice(source.indexOf('function closeBlockMenu('),source.indexOf('function conflictStatusText(')),f.context);
  f.context.setBlockSlashCombobox(f.editor);
  let prevented=0,stopped=0;
  const key={key:'Escape',preventDefault:()=>prevented++,stopPropagation:()=>stopped++};
  for(const override of [{isComposing:true},{keyCode:229},{defaultPrevented:true},{key:'ArrowDown'}])f.context.handleBlockMenuFooterKeydown({...key,...override});
  assert.equal(menu.hidden,false);assert.equal(focus.length,0);
  f.context.handleBlockMenuFooterKeydown(key);
  assert.equal(menu.hidden,true);assert.equal(prevented,1);assert.equal(stopped,1);assert.deepEqual(focus,[[0,8]]);
  assert.equal(f.editor.textContent,'앞 문장 /제목');assert.equal(f.editor.attributes.role,'textbox');assert.equal(f.context.State.slashToken,null);
});

test('UI-SLASH-PRESENTATION remote insertion keeps the menu on its source block and page or access changes dismiss it',()=>{
  const f=fixture(),opened=[];let closed=0;
  const menu={hidden:false};
  const row={querySelector:()=>f.editor},state={activePageId:'p',pageData:{blocks:[{id:'remote'},{id:'b'}]},menuMode:'slash',slashSourceIndex:0,menuSelectedIndex:1,slashToken:{blockId:'b',start:5,end:8,text:'/제목',query:'제목'}};
  Object.assign(f.context,{$:()=>menu,State:state,activePageCanEdit:()=>true,closeBlockMenu:()=>closed++,refreshBlockMenuSelection:()=>{},visibleBlockMenuButtons:()=>[{id:'first'},{id:'selected'}],openBlockMenu:(index,anchor,mode,query)=>{opened.push([index,anchor,mode,query]);state.slashSourceIndex=index;state.menuSelectedIndex=0;}});
  f.context.document.querySelector=selector=>{assert.equal(selector,'#blocks .block-row[data-index="1"]');return row;};
  vm.runInContext("BlockSlashPageId='p'",f.context);f.context.rebindBlockSlashMenu();
  assert.deepEqual(opened,[[1,row,'slash','제목']]);assert.equal(state.menuSelectedIndex,1);assert.equal(closed,0);
  state.activePageId='another';f.context.rebindBlockSlashMenu();assert.equal(closed,1);assert.equal(opened.length,1);
  state.activePageId='p';f.context.activePageCanEdit=()=>false;f.context.rebindBlockSlashMenu();assert.equal(closed,2);assert.equal(opened.length,1);
});
