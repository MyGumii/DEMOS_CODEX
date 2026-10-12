'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
function harness(){
  class HTMLElement {
    constructor(id,{tabindex=null,hidden=false,display='block',visibility='visible',rects=1}={}){
      Object.assign(this,{id,tabindex,tabIndex:tabindex??0,hidden,display,visibility,rects});
    }
    hasAttribute(key){return key==='tabindex'&&this.tabindex!==null;}
    getClientRects(){return Array(this.rects).fill({});}
    focus(){document.activeElement=this;}
    closest(){return dialog;}
  }
  const document={activeElement:null};
  const dialog=new HTMLElement('dialog');let children=[];
  dialog.querySelectorAll=()=>children;
  const context={HTMLElement,Element:HTMLElement,document,getComputedStyle:el=>el};vm.createContext(context);
  const start=source.indexOf('function visibleModalFocusables(dialog) {'),end=source.indexOf('function showModalWithFocus()',start);
  assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
  return {context,dialog,document,make:(id,options)=>new HTMLElement(id,options),set:items=>children=items};
}
test('UI-MODAL-FOCUS excludes negative tabindex controls even when they are buttons',()=>{
  const h=harness(),selected=h.make('selected',{tabindex:0}),other=h.make('other',{tabindex:-1}),manual=h.make('manual',{tabindex:-2}),search=h.make('search');
  h.set([search,selected,other,manual]);assert.deepEqual(Array.from(h.context.visibleModalFocusables(h.dialog),el=>el.id),['search','selected']);
});
test('UI-MODAL-FOCUS retains ordinary editable controls and excludes invisible descendants',()=>{
  const h=harness(),editable=h.make('editable');editable.tabIndex=-1;
  h.set([editable,h.make('hidden',{hidden:true}),h.make('css-hidden',{display:'none'}),h.make('invisible',{visibility:'hidden'}),h.make('ancestor-hidden',{rects:0})]);
  assert.deepEqual(Array.from(h.context.visibleModalFocusables(h.dialog),el=>el.id),['editable']);
});
test('UI-MODAL-FOCUS Tab leaves a roving palette and wraps within the modal',()=>{
  const h=harness(),toggle=h.make('toggle'),blue=h.make('blue',{tabindex:0}),purple=h.make('purple',{tabindex:-1}),result=h.make('result',{tabindex:0});
  h.set([toggle,blue,purple,result]);h.document.activeElement=blue;
  const press=shiftKey=>h.context.trapVisibleModalFocus({key:'Tab',shiftKey,preventDefault(){}});
  assert.equal(press(false),true);assert.equal(h.document.activeElement,result);
  press(false);assert.equal(h.document.activeElement,toggle);
  press(true);assert.equal(h.document.activeElement,result);
});
