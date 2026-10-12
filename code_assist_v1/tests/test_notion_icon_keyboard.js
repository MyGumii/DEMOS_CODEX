'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const icons=require('../notion_static/notion_icons.js');
const catalog=require('../notion_static/assets/icons/catalog.json');
icons.configure(catalog);
const source=fs.readFileSync(require.resolve('../notion_static/notion_icons.js'),'utf8');
const rect=(left,top,width=30)=>({left,top,width});

test('UI-ICON-KEYBOARD follows visual rows across short category rows and section gaps',()=>{
  const boxes=[rect(0,0),rect(32,0),rect(64,0),rect(0,34),rect(0,94),rect(32,94),rect(64,94)];
  assert.equal(icons.nextIconIndex(boxes,2,'ArrowDown'),3);
  assert.equal(icons.nextIconIndex(boxes,3,'ArrowDown'),4);
  assert.equal(icons.nextIconIndex(boxes,6,'ArrowUp'),3);
  assert.equal(icons.nextIconIndex(boxes,3,'ArrowUp'),0);
  assert.equal(icons.nextIconIndex(boxes,0,'ArrowUp'),0);
  assert.equal(icons.nextIconIndex(boxes,6,'ArrowDown'),6);
});
test('UI-ICON-KEYBOARD uses current responsive geometry and bounded horizontal navigation',()=>{
  for(const columns of [8,12]){
    const boxes=Array.from({length:23},(_,i)=>rect((i%columns)*34,Math.floor(i/columns)*34));
    assert.equal(icons.nextIconIndex(boxes,2,'ArrowDown'),columns+2);
    assert.equal(icons.nextIconIndex(boxes,columns+2,'ArrowUp'),2);
    assert.equal(icons.nextIconIndex(boxes,0,'ArrowLeft'),0);
    assert.equal(icons.nextIconIndex(boxes,22,'ArrowRight'),22);
    assert.equal(icons.nextIconIndex(boxes,4,'Home'),0);
    assert.equal(icons.nextIconIndex(boxes,4,'End'),22);
  }
  assert.equal(icons.nextIconIndex([],0,'ArrowDown'),-1);
});

function harness(){
  const applied=[],focus=[],scrolled=[],search={attributes:{},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}};
  const control=(dataset,name,i=0)=>({dataset,attributes:{},setAttribute(k,v){this.attributes[k]=v;},focus:()=>focus.push(name),getBoundingClientRect:()=>rect((i%5)*30,Math.floor(i/5)*30)});
  const colors=catalog.colors.map((c,i)=>control({color:c.id},c.id,i));
  const tones=Array.from({length:6},(_,i)=>control({skinTone:String(i)},'tone:'+i,i));
  const categories=['','work','people'].map(c=>control({iconCategory:c},'category:'+c));
  const toggle=control({},'toggle'),popover={hidden:true},skinToggle=control({},'skin-toggle'),skinMenu={hidden:true},results={scrollTop:20};
  let buttons=Array.from({length:12},(_,i)=>({id:'icon-'+i,dataset:{iconValue:'value-'+i},attributes:{},setAttribute(k,v){this.attributes[k]=v;},getBoundingClientRect:()=>rect((i%10)*34,Math.floor(i/10)*34),focus:()=>focus.push(i),scrollIntoView:()=>scrolled.push(i)}));
  let keydown;
  const root={querySelectorAll:selector=>({'[data-icon-value]':buttons,'[data-color]':colors,'[data-skin-tone]':tones,'[data-icon-category]':categories}[selector]||[]),addEventListener:(_name,handler)=>keydown=handler};
  const find=selector=>({'input[type="search"]':search,'[data-icon-color-toggle]':toggle,'.icon-picker-color-popover':popover,'[data-icon-skin-toggle]':skinToggle,'.icon-picker-skin-menu':skinMenu,'.icon-picker-results':results}[selector]
    ||colors.find(c=>selector===`[data-color="${c.dataset.color}"]`)||tones.find(t=>selector===`[data-skin-tone="${t.dataset.skinTone}"]`));
  const savedTones=[];
  const context={root,find,activeValue:'value-0',busy:false,disposed:false,colorsOpen:false,skinOpen:false,skinTone:0,recentColorOverride:false,tab:'vector',color:'default',group:'',catalog,
    skinNames:['기본','밝은 피부','약간 밝은 피부','중간 피부','약간 어두운 피부','어두운 피부'],skinModifiers:['','🏻','🏼','🏽','🏾','🏿'],host:{recentScope:'test'},
    content:icons.content,pickerContent:icons.content,saveSkinTone:(_scope,tone)=>savedTones.push(tone),parse:icons.parse,vectorValue:icons.vectorValue,nextIconIndex:icons.nextIconIndex,
    apply:value=>applied.push(value),render:()=>{context.renderColors();context.renderSkin();}};
  vm.createContext(context);
  const start=source.indexOf('    function setActive('),end=source.indexOf('    function render()',start);
  assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
  const eventStart=source.indexOf('    root.addEventListener("keydown", event => {');
  const eventEnd=source.indexOf('    render(); find(\'input[type="search"]\').focus();',eventStart);
  assert.ok(eventStart>=0&&eventEnd>eventStart);vm.runInContext(source.slice(eventStart,eventEnd),context);
  context.setActive(buttons[0]);
  const press=(key,extra={})=>{let prevented=0,stopped=0;keydown({target:search,key,preventDefault:()=>prevented++,stopPropagation:()=>stopped++,...extra});return {prevented,stopped};};
  return {context,search,applied,focus,scrolled,press,buttons,colors,tones,categories,toggle,popover,skinToggle,skinMenu,savedTones,results,clear:()=>buttons=[]};
}
test('UI-ICON-KEYBOARD search arrows retain input focus and Enter applies the selected icon',()=>{
  const h=harness();assert.deepEqual(h.press('ArrowDown'),{prevented:1,stopped:1});
  assert.equal(h.context.activeValue,'value-10');assert.equal(h.search.attributes['aria-activedescendant'],'icon-10');
  assert.equal(h.buttons[10].attributes['aria-selected'],'true');assert.equal(h.buttons[0].tabIndex,-1);assert.equal(h.buttons[10].tabIndex,0);
  assert.deepEqual(h.focus,[]);assert.deepEqual(h.scrolled,[10]);h.press('Enter');assert.deepEqual(h.applied,['value-10']);
});
test('UI-ICON-KEYBOARD text caret keys, composition, shortcuts and pending saves are untouched',()=>{
  const h=harness();
  for(const key of ['ArrowLeft','ArrowRight','Home','End'])assert.equal(h.press(key).prevented,0);
  for(const override of [{isComposing:true},{keyCode:229},{defaultPrevented:true},{ctrlKey:true},{altKey:true},{metaKey:true}])assert.equal(h.press('Enter',override).prevented,0);
  h.context.busy=true;assert.equal(h.press('Enter').prevented,0);h.context.busy=false;
  h.context.disposed=true;assert.equal(h.press('Enter').prevented,0);
  assert.deepEqual(h.applied,[]);assert.equal(h.context.activeValue,'value-0');
});
test('UI-ICON-KEYBOARD focused options use the same selection and geometry',()=>{
  const h=harness();h.press('ArrowDown',{target:h.buttons[3]});
  assert.equal(h.context.activeValue,'value-11');assert.deepEqual(h.focus,[11]);
  assert.equal(h.buttons.filter(button=>button.tabIndex===0).length,1);
});
test('UI-ICON-KEYBOARD empty results clear stale active descendants and cannot save',()=>{
  const h=harness();h.clear();h.context.setActive(undefined);
  assert.equal(h.search.attributes['aria-activedescendant'],undefined);
  assert.equal(h.context.activeValue,'');h.press('Enter');h.press('ArrowDown');assert.deepEqual(h.applied,[]);
});
test('UI-ICON-PALETTE arrows follow the two-row palette without saving the document',()=>{
  const h=harness();h.context.activeValue='ni:doc:default';
  h.press('ArrowDown',{target:h.toggle});assert.equal(h.context.colorsOpen,true);assert.equal(h.popover.hidden,false);
  assert.deepEqual(h.focus,['default']);
  h.press('ArrowDown',{target:h.colors[0]});assert.equal(h.context.color,'green');assert.equal(h.context.activeValue,'ni:doc:green');
  h.press('ArrowRight',{target:h.colors[5]});assert.equal(h.context.color,'blue');assert.equal(h.context.activeValue,'ni:doc:blue');
  assert.equal(h.colors[6].attributes['aria-checked'],'true');assert.equal(h.colors.filter(c=>c.tabIndex===0).length,1);
  assert.deepEqual(h.applied,[]);assert.equal(h.toggle.attributes['aria-label'],'아이콘 색상 선택, 파랑 선택됨');
});
test('UI-ICON-PALETTE Escape and Enter close only the palette and restore the color button',()=>{
  for(const key of ['Escape','Enter',' ']){
    const h=harness();h.context.setColorMenu(true,true);
    assert.deepEqual(h.press(key,{target:h.colors[0]}),{prevented:1,stopped:1});
    assert.equal(h.context.colorsOpen,false);assert.equal(h.popover.hidden,true);assert.equal(h.focus.at(-1),'toggle');assert.deepEqual(h.applied,[]);
  }
  const h=harness();assert.equal(h.press('Escape').prevented,0);
  h.context.tab='emoji';h.context.setColorMenu(true);assert.equal(h.context.colorsOpen,false);
});
test('UI-ICON-SKIN-TONE keyboard selection changes only the picker preference',()=>{
  const h=harness();h.context.tab='emoji';
  assert.deepEqual(h.press('ArrowDown',{target:h.skinToggle}),{prevented:1,stopped:1});
  assert.equal(h.context.skinOpen,true);assert.equal(h.skinMenu.hidden,false);assert.equal(h.focus.at(-1),'tone:0');
  h.press('ArrowRight',{target:h.tones[0]});assert.equal(h.focus.at(-1),'tone:1');
  h.press('Escape',{target:h.tones[1]});assert.equal(h.context.skinOpen,false);assert.equal(h.focus.at(-1),'skin-toggle');
  h.press('ArrowDown',{target:h.skinToggle});
  h.press('Enter',{target:h.tones[3]});assert.equal(h.context.skinTone,3);
  assert.equal(h.skinMenu.hidden,true);assert.deepEqual(h.savedTones,[3]);assert.deepEqual(h.applied,[]);
});
test('UI-ICON-CATEGORY arrow and boundary keys change the filter with stable focus',()=>{
  const h=harness();
  assert.deepEqual(h.press('ArrowRight',{target:h.categories[0]}),{prevented:1,stopped:1});
  assert.equal(h.context.group,'work');assert.equal(h.focus.at(-1),'category:work');assert.equal(h.results.scrollTop,0);
  h.press('End',{target:h.categories[1]});assert.equal(h.context.group,'people');
  h.press('ArrowRight',{target:h.categories[2]});assert.equal(h.context.group,'');
  h.press('ArrowLeft',{target:h.categories[0]});assert.equal(h.context.group,'people');
  h.press('Home',{target:h.categories[2]});assert.equal(h.context.group,'');assert.deepEqual(h.applied,[]);
});
