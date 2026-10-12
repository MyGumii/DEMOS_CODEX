"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
class Element {
  constructor(tag="div",text="",action="",className="") {
    this.tag=tag;this.text=text;this.dataset={};if(action)this.dataset.pageAction=action;
    this.className=className;this.children=[];this.hidden=false;this.attributes={};this.parent=null;
    this.classList={contains:value=>this.className.split(" ").includes(value)};
  }
  get textContent() {return this.text+this.children.map(child=>child.textContent).join("");}
  set textContent(value) {this.text=value;this.children=[];}
  setAttribute(key,value) {this.attributes[key]=value;}
  append(...nodes) {for(const node of nodes){if(node.parent)node.parent.children=node.parent.children.filter(child=>child!==node);node.parent=this;this.children.push(node);}}
  replaceChildren() {this.children.forEach(child=>{child.parent=null;});this.children=[];}
  matches(selector) {return selector==="[data-page-action]"?Boolean(this.dataset.pageAction):selector==="button"?this.tag==="button":selector.startsWith(".")&&this.classList.contains(selector.slice(1));}
  querySelectorAll(selector) {const all=[];for(const child of this.children){if(selector.split(",").some(value=>child.matches(value)))all.push(child);all.push(...child.querySelectorAll(selector));}return all;}
  querySelector(selector) {return this.querySelectorAll(selector)[0]||null;}
}
const context={document:{createElement:tag=>new Element(tag)}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function organizeTopbarPageMenu("),source.indexOf("function openPageMenu(")),context);
const button=(id,label)=>new Element("button",label,id);
const menu=new Element(),fonts=new Element("div","","","page-font-picker"),footer=new Element("div","owner","","menu-foot");
const defaultFont=button("font_default","기본");defaultFont.className="active";
fonts.append(defaultFont,button("font_serif","세리프"),button("font_mono","모노"));
let lockCalls=0;
const lock=button("lock","페이지 잠금 해제");lock.onclick=()=>lockCalls++;
const actions=[button("favorite","즐겨찾기"),button("context","Chat에 추가"),button("copy_link","링크 복사"),button("copy_content","페이지 내용 복사하기"),button("rename","이름 바꾸기"),button("duplicate","복제"),button("customize_page","페이지 사용자 지정"),lock,button("undo","실행 취소"),button("new_tab","새 탭에서 열기"),button("future_action","기타 동작")];
menu.append(...actions,fonts,footer);
context.organizeTopbarPageMenu(menu);
assert.equal(menu.children[0],fonts);
assert.equal(menu.children.at(-1),footer);
assert.equal(defaultFont.attributes["aria-pressed"],"true");
assert.equal(fonts.querySelectorAll("button")[1].attributes["aria-pressed"],"false");
assert.deepEqual(menu.querySelectorAll("[data-page-action]").map(node=>node.dataset.pageAction),["font_default","font_serif","font_mono","copy_link","copy_content","duplicate","customize_page","lock","context","undo","future_action"]);
assert.equal(menu.querySelectorAll("[data-page-action]").find(node=>node.dataset.pageAction==="lock"),lock);
lock.onclick();assert.equal(lockCalls,1,"reordering retains the actual action and its listener");
assert.equal(lock.textContent,"페이지 잠금 해제");
const empty=new Element("div","일치하는 작업이 없습니다.","","menu-empty");menu.append(empty);
context.filterPageMenu(menu,"복제");
assert.equal(fonts.hidden,true);
assert.equal(empty.hidden,true);
assert.deepEqual(menu.querySelectorAll("[data-page-action]").filter(node=>!node.hidden).map(node=>node.dataset.pageAction),["duplicate"]);
context.filterPageMenu(menu,"없는 작업");
assert.equal(empty.hidden,false);
assert.equal(fonts.hidden,true);
context.filterPageMenu(menu,"세리프");
assert.equal(fonts.hidden,false);
assert.equal(empty.hidden,true);
assert.deepEqual(fonts.querySelectorAll("button").filter(node=>!node.hidden).map(node=>node.dataset.pageAction),["font_serif"]);
context.filterPageMenu(menu,"");
assert.equal(fonts.hidden,false);assert.equal(footer.hidden,false);assert.equal(empty.hidden,true);
assert.equal(menu.querySelectorAll("[data-page-action]").filter(node=>!node.hidden).length,11);
const readOnly=new Element();readOnly.append(button("copy_link","링크 복사"),button("new_tab","새 탭"));
context.organizeTopbarPageMenu(readOnly);
assert.deepEqual(readOnly.querySelectorAll("[data-page-action]").map(node=>node.dataset.pageAction),["copy_link"],"full-page menu omits row-context actions");
console.log("UI-SHELL-004: page menu action preservation, layout, search and empty results passed");
