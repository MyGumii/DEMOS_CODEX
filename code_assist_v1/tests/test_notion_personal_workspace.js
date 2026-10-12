"use strict";
const assert = require('node:assert/strict');
const {create} = require('../notion_static/notion_personal_workspace');
const icons = require('../notion_static/notion_icons');
const fs = require('node:fs'), vm = require('node:vm');

function fixture() {
  const state = {owner:'owner',subject:'owner',scope:'own',active:true};
  const current = {workspace:{owner_user_id:'owner',version:1,icon:'',access:{role:'owner'}},capabilities:{workspace_appearance:true,workspace_image:true}};
  const calls = [], receipts = new Map(), hooks = {}; let serial=0, changed=0;
  const api = async (path, options) => {
    if (path.endsWith('personal-workspaces')) return hooks.list ? hooks.list() : {items:[structuredClone(current)]};
    if (!options) { if (hooks.read) await hooks.read(); return structuredClone(current); }
    const request = options.body; calls.push(structuredClone(request));
    if (hooks.write) await hooks.write();
    if (!receipts.has(request.operation_id)) {
      assert.equal(request.base_version,current.workspace.version);
      current.workspace.version++; current.workspace.icon=request.icon;
      receipts.set(request.operation_id,structuredClone(current));
    }
    if (hooks.afterWrite) await hooks.afterWrite();
    return receipts.get(request.operation_id);
  };
  const controller = create({active:()=>state.active,scope:()=>state.scope,subject:()=>state.subject,owner:()=>state.owner,
    api, operationId:()=>String(++serial), changed:()=>changed++,status:()=>{},
    validateImage:async file=>{if(hooks.validate)await hooks.validate();return 'image/png';},
    fetch:async (path,options)=>{calls.push(options);if(hooks.image)await hooks.image();current.workspace.icon='image';current.workspace.version++;return {ok:true};}});
  return {state,current,calls,hooks,controller,changed:()=>changed};
}
(async()=>{
  {
    const f=fixture(); await f.controller.refresh();
    f.hooks.afterWrite=()=>{throw Error('response lost');};
    await assert.rejects(f.controller.saveIcon('owner','🌱'),/response lost/);
    f.current.workspace.icon='newer'; f.current.workspace.version++;
    delete f.hooks.afterWrite; await f.controller.saveIcon('owner','🌱');
    assert.deepEqual(f.calls[0],f.calls[1]);
    assert.equal(f.controller.get('owner').workspace.icon,'newer','replay displays a fresh read, not its old receipt');
  }
  {
    const f=fixture(); let resolve;
    f.hooks.list=()=>new Promise(r=>resolve=r);
    const pending=f.controller.refresh(); const old=structuredClone(f.current);
    await f.controller.saveIcon('owner','🌱'); resolve({items:[old]}); await pending;
    assert.equal(f.controller.get('owner').workspace.icon,'🌱','late list responses cannot undo the displayed committed icon');
  }
  {
    const f=fixture(); f.hooks.read=()=>{f.state.scope='guest';f.state.owner='someone';};
    await assert.rejects(f.controller.saveIcon('owner','🌱'),/워크스페이스/); assert.equal(f.calls.length,0);
    await assert.rejects(f.controller.saveIcon('someone','🌱'),/내 워크스페이스/);
  }
  {
    const f=fixture(); f.hooks.afterWrite=()=>{f.state.scope='elsewhere';};
    await f.controller.saveIcon('owner','🌱');
    assert.equal(f.changed(),0,'a saved icon cannot repaint a page that changed scope');
  }
  {
    const f=fixture(); f.hooks.write=()=>{throw Object.assign(Error('conflict'),{status:409});};
    await assert.rejects(f.controller.saveIcon('owner','🌱'),/다시 적용/);
    delete f.hooks.write; await f.controller.saveIcon('owner','🌱');
    assert.notEqual(f.calls[0].operation_id,f.calls[1].operation_id);
  }
  {
    const f=fixture(); let resolve; f.hooks.write=()=>new Promise(r=>resolve=r);
    const first=f.controller.saveIcon('owner','🌱'), second=f.controller.saveIcon('owner','🌱');
    await new Promise(r=>setImmediate(r)); assert.equal(f.calls.length,1); resolve(); await Promise.all([first,second]);
  }
  {
    const f=fixture(), file={}; f.hooks.validate=()=>{throw Error('invalid file');};
    await assert.rejects(f.controller.saveImage('owner',file),/invalid/); assert.equal(f.calls.length,0);
    delete f.hooks.validate; f.hooks.image=()=>{throw Error('uncertain image response');};
    await assert.rejects(f.controller.saveImage('owner',file),/uncertain/);
    delete f.hooks.image; await f.controller.saveImage('owner',file);
    assert.equal(f.calls[0].body,file); assert.deepEqual(f.calls[0].headers,f.calls[1].headers);
  }
  {
    const f=fixture();f.current.capabilities.workspace_image=false;
    await assert.rejects(f.controller.saveImage('owner',{}),/변경할 수 없습니다/);assert.equal(f.calls.length,0);
    f.state.active=false; await f.controller.refresh();assert.equal(f.controller.get('owner'),null);
  }
  const ref='np:owner:pi_'+'a'.repeat(40);
  assert.equal(icons.parse(ref).kind,'personal-workspace-file');
  assert.match(icons.html(ref),/\/personal-workspaces\/owner\/images\/pi_/);
  for(const bad of ['np:../owner:pi_'+'a'.repeat(40),'np:owner:bad'])assert.equal(icons.parse(bad).kind,'default');
  const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  for(const name of ['openPersonalWorkspaceSettings','openPersonalWorkspaceIconPicker']){
    let resolve,opens=0;
    const nodes={'#modalBg':{hidden:false},'#personalWorkspaceIconBtn':{},'#workspaceName':{}};
    const context={State:{user:{id:'owner'},navigation:{enabled:false,authoritative:false},workspaceOwnerFilter:''},
      personalWorkspaceScope:()=> 'own', $:selector=>nodes[selector], ModalReturnFocus:null,
      PersonalWorkspaceAppearance:{refresh:()=>new Promise(r=>resolve=r),get:()=>({capabilities:{workspace_appearance:true}})},
      openModal(){opens++;nodes['#modalBg'].hidden=false;nodes['#personalSettingsLoading']={};},setStatus(){}};
    const start=main.indexOf('async function '+name+'(');
    const end=name==='openPersonalWorkspaceSettings'?main.indexOf('\nasync function openPersonalWorkspaceIconPicker(',start):main.indexOf('\nfunction handleWorkspaceHeaderKeydown(',start);
    vm.createContext(context);vm.runInContext(main.slice(start,end),context);
    const pending=context[name]('owner');
    const before=opens;nodes['#modalBg'].hidden=true;resolve();await pending;
    assert.equal(opens,before,'closing settings while metadata loads must cancel the late dialog');
  }
  console.log('Personal workspace: current reads, lost-response retries, list/write races, scope, conflicts, deduplication and image validation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
