'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const icons = require('../notion_static/notion_icons.js');

const source = fs.readFileSync(require.resolve('../notion_static/notion_icons.js'), 'utf8');
const image = () => new File([new Uint8Array([137,80,78,71,13,10,26,10])], 'team.png', {type:'image/png'});

function picker() {
  const nodes = new Map(), messages = [], selected = [], calls = [];
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, {value:'',disabled:false,focus(){this.focused=true;}});
    return nodes.get(key);
  };
  const icon = `ne:we_${'a'.repeat(40)}`;
  const context = {busy:false,disposed:false,customOpen:false,customFile:null,customRequestToken:0,
    customItems:[],query:'house',group:'',abort:new AbortController(),validateImage:icons.validateImage,
    host:{customEmoji:{create:async(name,file,token)=>{calls.push([name,file,token]);return {name,icon};}}},
    find:node,render(){},status:(message,error=false)=>messages.push({message,error}),
    setBusy(value){context.busy=value;},setActive:(choice,focus)=>selected.push({choice,focus}),
    root:{querySelectorAll:()=>[{dataset:{iconValue:icon}}]}};
  vm.createContext(context);
  const start = source.indexOf('    function customSaveEnabled()');
  const end = source.indexOf('    function render() {', start);
  assert(start >= 0 && end > start);
  vm.runInContext(source.slice(start,end), context);
  return {context,node,messages,selected,calls,icon};
}

test('custom emoji form validates the image, creates a reusable item, and leaves it ready to select', async () => {
  const h = picker();
  h.context.showCustomForm(true);
  assert(h.context.customOpen);
  assert(h.node('[data-icon-custom-save]').disabled);
  h.node('[data-icon-custom-name]').value = 'Team';
  await h.context.selectCustomFile(image());
  assert.equal(h.node('[data-icon-custom-save]').disabled,false);
  await h.context.saveCustomEmoji();
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0][0],'Team');
  assert.equal(h.context.customOpen,false);
  assert.equal(h.context.customItems[0].value,h.icon);
  assert.equal(h.context.group,'사용자 지정');
  assert.equal(h.selected.length,1);
  assert.equal(h.selected[0].choice.dataset.iconValue,h.icon);
  assert.equal(h.messages.at(-1).error,false);
});

test('closing the custom emoji form discards a pending image validation', async () => {
  const h = picker();
  let release;
  h.context.validateImage = () => new Promise(resolve => {release=resolve;});
  h.context.showCustomForm(true);
  const pending = h.context.selectCustomFile(image());
  h.context.showCustomForm(false);
  release('image/png');
  await pending;
  assert.equal(h.context.customFile,null);
  assert(h.node('[data-icon-custom-save]').disabled);
  assert.equal(h.calls.length,0);
});
