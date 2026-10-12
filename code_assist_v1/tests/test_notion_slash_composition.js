const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../notion_static/notion.js'), 'utf8');
const slashMenu = require('../notion_static/notion_slash_menu.js');
const functions = source.slice(source.indexOf('function refreshBlockSlashMenu('), source.indexOf('function commitBlockEditorInput('));

function setup() {
  const state = {activePageId:'p',menuMode:'slash',slashSourceIndex:0};
  const menu = {hidden:false}, search = {value:''}, tasks = [];
  const block = {id:'b'}, row = {};
  let anchor = 1, focus = 3, selected = null, committed = '', ended = 0;
  const editor = {
    innerHTML:'/제목',textContent:'/제목',dataset:{imeComposing:'true'},isConnected:true,
    contains:()=>true,closest:()=>row,
    setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
    blur() { this.innerHTML='/';this.textContent='/'; },
  };
  const selection = {
    rangeCount:1,anchorNode:{},focusNode:{},
    get focusOffset(){return focus;},
    getRangeAt:()=>({cloneRange:()=>({selectNodeContents(){},setEnd(node,offset){this.end=offset;},toString(){return 'x'.repeat(this.end);}})}),
  };
  const context = {
    State:state,window:{NotionSlashMenu:slashMenu,getSelection:()=>selection},
    $:selector=>selector.includes('menu-search')?search:menu,
    caretOffset:()=>anchor,selectionIsCollapsed:()=>anchor===focus,
    filterBlockMenu:query=>{state.menuQuery=query;},
    openBlockMenu:(index,node,mode,query)=>{menu.hidden=false;state.menuQuery=query;},
    closeBlockMenu:()=>{menu.hidden=true;},
    activePageCanEdit:()=>true,
    EditorHost:{endComposition:()=>ended++},
    focusBlock:(index,offset)=>{anchor=focus=offset;},
    setTimeout:callback=>tasks.push(callback),
    commitBlockEditorInput:()=>{editor.textContent=editor.innerHTML;committed=editor.innerHTML;context.refreshBlockSlashMenu(editor,block,0,row);},
    activateBlockMenuSelection:()=>{selected=state.menuQuery;},
  };
  vm.createContext(context);vm.runInContext(functions,context);
  return {context,editor,block,row,state,menu,search,tasks,get selected(){return selected;},get committed(){return committed;},get ended(){return ended;}};
}
const live=setup();
live.context.refreshBlockSlashMenu(live.editor,live.block,0,live.row,true);
assert.equal(live.state.menuQuery,'제목');
assert.equal(live.search.value,'제목');
assert.equal(live.state.slashToken.end,3);

for(const key of ['Enter','Escape']) {
  for(const processKey of [false,true]) {
    const test=setup();let prevented=false,stopped=false;
    const event={key:processKey?'Process':key,code:key,isComposing:true,keyCode:229,
      preventDefault(){prevented=true;},stopPropagation(){stopped=true;}};
    assert.equal(test.context.finishComposingSlash(event,test.editor,test.block,0),true);
    assert.ok(prevented&&stopped);
    assert.equal(test.selected,null,'menu command must wait for preedit commit');
    test.tasks.shift()();
    assert.equal(test.committed,'/제목','visible Hangul survives native preedit cancellation');
    assert.equal(test.ended,1);
    assert.equal(test.selected,key==='Enter'?'제목':null,'the filtered command receives Enter');
    assert.equal(test.menu.hidden,key==='Escape');
  }
}
const detached=setup();
detached.context.finishComposingSlash({key:'Enter',preventDefault(){},stopPropagation(){}},detached.editor,detached.block,0);
detached.editor.isConnected=false;detached.tasks.shift()();assert.equal(detached.selected,null);

// Global shortcuts must not process keys that belong to an IME or were handled by the editor.
const newlineSource=source.replace(/\r\n/g,'\n');
const startNormalized=newlineSource.indexOf('document.addEventListener("keydown", e => {',newlineSource.indexOf('function initEvents'));
const globalListener=newlineSource.slice(startNormalized,newlineSource.indexOf('  window.addEventListener("popstate"',startNormalized));
let handler;
vm.runInNewContext(globalListener,{document:{addEventListener:(name,callback)=>handler=callback},trapVisibleModalFocus:()=>{throw Error('shortcut reached during composition');}});
for(const event of [{isComposing:true},{keyCode:229},{defaultPrevented:true},{target:{closest:()=>true}}])handler(event);
console.log('UI-C7: live IME search range, Enter/Process ordering, Escape text preservation, detached editor and global shortcut guards passed');
