"use strict";
const {CollaborationEditor, loadPinnedYjs} = require('./notion_collaboration_test_runtime.js');
const Core = require('../notion_static/notion_editor_core.js');
const Y = loadPinnedYjs();
const clone = value => JSON.parse(JSON.stringify(value));
let state = {page_id:'page-e1', title:'Collaborative editing', can_edit:true,
  blocks:[{id:'block-a',type:'paragraph',content:'A',props:{}},{id:'block-b',type:'paragraph',content:'B',props:{}}]};
const events = [];
const host = Core.createEditorHostAdapter({readDocument:()=>clone(state),
  applyProjection:projection=>{state={...state,...clone(projection)}; return {applied:true};}});
const bridge = CollaborationEditor.createEditorBridge({Y,host});
bridge.subscribe(event=>{if(event.type==='local-update')events.push({
  update:Buffer.from(event.update).toString('base64'), commands:event.structure_commands, snapshot:event.snapshot});});
bridge.configureDocument(state); bridge.finishReplay();
state.blocks.shift(); host.notifyLocalMutation('document'); bridge.stopCapturing();
bridge.undo(); bridge.redo(); bridge.undo();
state.blocks.push({id:'block-c',type:'paragraph',content:'C',props:{rich_text_html:'<strong>C</strong>'}});
host.notifyLocalMutation('document'); bridge.stopCapturing();
bridge.undo(); bridge.redo(); bridge.undo();
state.blocks.reverse(); host.notifyLocalMutation('document'); bridge.stopCapturing();
bridge.undo(); bridge.redo(); bridge.undo();
bridge.dispose(); host.dispose();
process.stdout.write(JSON.stringify(events));
