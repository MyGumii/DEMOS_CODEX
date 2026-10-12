"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture(columns = false, count = 3) {
  const events = {document: new Map(), window: new Map()};
  const frames = new Map(), overlays = new Set(), classes = new Set(), lifecycleListeners = new Set();
  const resizeObservers = [];
  let frameId = 0, selected = [], page = "page-a";
  let scrollTop = 0;
  const scroller = {
    clientHeight: 500, scrollHeight: 1200,
    get scrollTop() { return scrollTop; },
    set scrollTop(value) { scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  };
  const surface = {
    getBoundingClientRect: () => ({left: 200, right: 1000, top: 0, bottom: 500}),
    querySelector: () => ({getBoundingClientRect: () => ({bottom: 44})}),
  };
  const canvas = {
    isConnected: true, hidden: false, scrollHeight: 1000, clientHeight: 1000,
    parentElement: scroller, setPointerCapture() {}, releasePointerCapture() {},
    getBoundingClientRect: () => ({left: 380, right: 820, top: 44, bottom: 1200}),
  };
  const specs = columns
    ? [["left-a",400,570,100],["left-b",400,570,150],["right-a",620,800,100],["right-b",620,800,150]]
    : Array.from({length: count}, (_,index)=>[["a", "b", "c"][index] || `row-${index}`,400,800,100+index*50]);
  const rows = specs.map(([id,left,right,top]) => ({
    dataset: {blockId: id}, hidden: false,
    getClientRects: () => [{}], querySelector() { return this; },
    getBoundingClientRect() { return {left, right, top: top + (this.offset || 0) - scroller.scrollTop, bottom: top + 40 + (this.offset || 0) - scroller.scrollTop}; },
  }));
  const add = kind => (name, fn) => {
    if (!events[kind].has(name)) events[kind].set(name, []);
    events[kind].get(name).push(fn);
  };
  const document = {
    documentElement: scroller, scrollingElement: scroller,
    activeElement: {blur() {}}, addEventListener: add("document"),
    querySelector: selector => selector === "#pageView" ? canvas : selector === "#blocks" ? {getBoundingClientRect: () => ({left: 380, right: 820, top: 100, bottom: 300})} : null,
    createElement: () => ({style: {}, setAttribute() {}, remove() { overlays.delete(this); }}),
    body: {appendChild: el => overlays.add(el), classList: {add: name => classes.add(name), remove: name => classes.delete(name)}},
  };
  const context = {
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; this.observed = new Set(); resizeObservers.push(this); }
      observe(element) { this.observed.add(element); }
      unobserve(element) { this.observed.delete(element); }
      disconnect() { this.observed.clear(); }
    },
    document, window: {innerWidth: 1000, innerHeight: 500, addEventListener: add("window"), getSelection: () => ({removeAllRanges() {}})},
    requestAnimationFrame(fn) { const id = ++frameId; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    getComputedStyle: () => ({overflowY: "visible"}), performance: {now: () => 1000},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../notion_static/notion_ui_interaction.js"), "utf8"), context);
  context.NotionUiInteraction.bindGutterSelection({rows: () => rows, selected: () => [...selected], pageKey: () => page, select: ids => {selected = Array.from(ids);},
    subscribe: listener => { lifecycleListeners.add(listener); return () => lifecycleListeners.delete(listener); }});
  const emit = (name, values = {}, kind = "document") => {
    const event = {button: 0, isPrimary: true, pointerId: 1, pointerType: "mouse", clientX: 900, clientY: 105, preventDefault() {this.prevented = true;}, stopImmediatePropagation() {},
      target: {closest: selector => selector === "#workspaceMain" ? surface : null}, ...values};
    for (const fn of events[kind].get(name) || []) fn(event);
    return event;
  };
  return {emit, frames, overlays, classes, scroller, canvas, document, rows, resizeObservers,
    lifecycle: type => { for (const listener of lifecycleListeners) listener({type}); },
    get selected() {return selected;}, setSelected: ids => {selected = ids;}, navigate: () => {page = "page-b";},
    tick() { const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn()); }};
}

{
  const f = fixture();
  f.emit("pointerdown"); f.emit("pointermove", {clientX: 898, clientY: 106}); f.tick();
  assert.equal(f.overlays.size, 0, "a click/jitter must not draw a selection box");
  f.emit("pointerup"); assert.equal(f.overlays.size, 0);
}
{
  const f = fixture(); f.emit("pointerdown");
  f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  assert.deepEqual(f.selected, ["a", "b"]);
  const box = [...f.overlays][0];
  assert.deepEqual({...box.style}, {left: "350px", top: "105px", width: "550px", height: "75px"});
  f.emit("pointerup", {clientX: 350, clientY: 235});
  assert.deepEqual(f.selected, ["a", "b", "c"], "release coordinates must be included even without another move");
  assert.equal(f.overlays.size, 0); assert.equal(f.frames.size, 0); assert.equal(f.classes.size, 0);
}
{
  const f = fixture();
  f.emit("pointerdown", {clientX: 960, clientY: 105});
  f.emit("pointermove", {clientX: 850, clientY: 180}); f.tick();
  assert.deepEqual(f.selected, [], "a rectangle wholly in the gutter selects no blocks");
  assert.deepEqual({...([...f.overlays][0].style)}, {left: "850px", top: "105px", width: "110px", height: "75px"}, "the gutter still shows the drag rectangle outside the narrow document");
  f.emit("pointermove", {clientX: 100, clientY: 20}); f.tick();
  const box = [...f.overlays][0].style;
  assert.equal(box.left, "200px", "the rectangle must not paint into the sidebar");
  assert.equal(box.top, "44px", "the rectangle must not paint over the toolbar");
  f.emit("pointerup", {clientX: 100, clientY: 20});
  assert.equal(f.overlays.size, 0);
}
{
  const f = fixture(); f.setSelected(["c"]);
  f.emit("pointerdown", {clientX: 350, clientY: 180, shiftKey: true});
  f.emit("pointermove", {clientX: 900, clientY: 105}); f.tick();
  assert.deepEqual(f.selected, ["c", "a", "b"]);
  assert.equal(f.emit("keydown", {key: "Escape"}).prevented, true);
  assert.deepEqual(f.selected, ["c"]); assert.equal(f.overlays.size, 0);
}
{
  const f = fixture(); f.emit("pointerdown"); f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  f.scroller.scrollTop = 40; f.emit("scroll", {}, "window"); f.tick();
  assert.equal([...f.overlays][0].style.top, "65px");
  assert.deepEqual(f.selected, ["a", "b", "c"]);
  f.emit("pointermove", {clientX: 350, clientY: 495}); f.tick();
  assert.ok(f.scroller.scrollTop > 40, "edge drag scrolls the document");
  f.emit("pointercancel"); assert.equal(f.overlays.size, 0); assert.equal(f.frames.size, 0);
}
for (const [startScroll, edgeY, returnY] of [[0, 5, 495], [700, 495, 5]]) {
  const f = fixture(); f.scroller.scrollTop = startScroll;
  f.emit("pointerdown"); f.emit("pointermove", {clientX: 350, clientY: edgeY}); f.tick();
  assert.equal(f.scroller.scrollTop, startScroll);
  assert.equal(f.frames.size, 0, "holding at a clamped document edge must not keep repainting");
  assert.equal(f.overlays.size, 1, "stopping edge scrolling must preserve drag feedback");
  f.emit("pointermove", {clientX: 350, clientY: returnY}); f.tick();
  assert.notEqual(f.scroller.scrollTop, startScroll, "moving away from the boundary can resume scrolling");
  assert.equal(f.frames.size, 1);
  f.emit("pointercancel"); assert.equal(f.frames.size, 0); assert.equal(f.overlays.size, 0);
}
for (const [event, kind] of [["lostpointercapture", "document"], ["blur", "window"], ["resize", "window"]]) {
  const f = fixture(); f.setSelected(["c"]); f.emit("pointerdown"); f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  f.emit(event, {}, kind); assert.deepEqual(f.selected, ["c"]); assert.equal(f.overlays.size, 0);
}
{
  const f = fixture(); f.emit("pointerdown"); f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  f.navigate(); f.emit("pointermove", {clientX: 350, clientY: 200}); f.tick();
  assert.equal(f.overlays.size, 0); assert.equal(f.classes.size, 0);
}
{
  const f = fixture();
  f.emit("pointerdown", {target: {closest: () => ({})}});
  f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  assert.equal(f.overlays.size, 0, "content/button gestures must remain owned by the editor");
}
for (const [startX, endX] of [[900, 350], [350, 900]]) {
  const f = fixture();
  f.emit("pointerdown", {clientX: startX, clientY: 420});
  f.emit("pointermove", {clientX: endX, clientY: 180}); f.tick();
  assert.deepEqual(f.selected, ["b", "c"], "a gutter drag can begin well below the final block");
  assert.equal(f.overlays.size, 1, "the rectangle is visible before release from the lower gutter");
  f.emit("pointerup", {clientX: endX, clientY: 180});
  assert.equal(f.overlays.size, 0);
}
{
  const f = fixture(); f.setSelected(["c"]);
  f.emit("pointerdown"); f.emit("pointermove", {clientX: 350, clientY: 180}); f.tick();
  f.emit("pointercancel", {pointerId: 2});
  assert.deepEqual(f.selected, ["a", "b"], "another pointer must not cancel the active mouse gesture");
  assert.equal(f.overlays.size, 1);
  f.emit("pointercancel", {pointerId: 1});
  assert.deepEqual(f.selected, ["c"]);
  assert.equal(f.overlays.size, 0);
}
const ui = require("../notion_static/notion_ui_interaction.js");
for (const [startX, endX] of [[250, 900], [950, 350]]) {
  const f = fixture();
  const down = f.emit("pointerdown", {clientX: startX, clientY: 60});
  f.emit("pointermove", {clientX: endX, clientY: 260}); f.tick();
  assert.equal(down.prevented, true, "blank page margins above the first block start a block gesture, not browser text selection");
  assert.deepEqual(f.selected, ["a", "b", "c"], "a rectangle crossing the title area still selects every body block in its range");
  assert.equal([...f.overlays][0].style.top, "60px", "feedback begins in the page margin instead of being clipped to the first block");
  f.emit("pointerup", {clientX: endX, clientY: 260});
  assert.equal(f.overlays.size, 0);
  assert.deepEqual(f.selected, ["a", "b", "c"]);
}
for (const startY of [125,420]) {
  const f = fixture(true);
  f.emit("pointerdown", {clientX:600,clientY:startY});
  f.emit("pointermove", {clientX:700,clientY:startY===125?185:105}); f.tick();
  assert.deepEqual(f.selected,["right-a","right-b"],"empty space between/below columns starts a rectangle without selecting the other column");
  assert.equal(f.overlays.size,1);
  f.emit("pointerup",{clientX:700,clientY:startY===125?185:105});
  assert.equal(f.overlays.size,0);
}
{
  const f = fixture(true);
  f.emit("pointerdown",{clientX:350,clientY:105});
  f.emit("pointermove",{clientX:850,clientY:185}); f.tick();
  assert.deepEqual(f.selected,["left-a","left-b","right-a","right-b"],"cross-column rectangles include only intersected visible blocks");
}
assert.deepEqual(ui.selectionRectangle({x: 900, y: -100}, {x: 100, y: 700}, {left: 200, right: 1000, top: 44, bottom: 500}), {left: 200, top: 44, width: 700, height: 456});
console.log("UI-EDITOR-001 R1: selection marquee, reverse/additive, release, scroll, cancellation and clipping passed");

const {test} = require('node:test');
test('UI-EDITOR-001 releasing at the viewport edge does not scroll after mouseup', () => {
  const f = fixture();
  f.emit('pointerdown'); f.emit('pointermove', {clientX:350,clientY:495}); f.tick();
  const before = f.scroller.scrollTop;
  assert.ok(before > 0);
  f.emit('pointerup', {clientX:350,clientY:495});
  assert.equal(f.scroller.scrollTop, before, 'release must select at the final coordinates without an extra scroll step');
  assert.equal(f.frames.size, 0); assert.equal(f.overlays.size, 0);
});
test('UI-EDITOR-001 document replacement clears a stationary marquee immediately', () => {
  for (const type of ['document-loaded', 'document-unloaded']) {
    const f = fixture(); f.setSelected(['c']);
    f.emit('pointerdown'); f.emit('pointermove', {clientX:350,clientY:180}); f.tick();
    assert.equal(f.frames.size, 0, 'the pointer is held away from scroll edges');
    f.navigate(); f.setSelected(['new-page-block']); f.lifecycle(type);
    assert.equal(f.overlays.size, 0, 'no further pointer event is required for cleanup');
    assert.deepEqual(f.selected, ['new-page-block'], 'old selection must not be restored into the new page');
    assert.equal(f.classes.size, 0);
  }
});
test('UI-EDITOR-001 remote layout changes repaint and cannot revive removed additive selections', () => {
  const f = fixture(); f.setSelected(['c']);
  f.emit('pointerdown', {shiftKey:true}); f.emit('pointermove', {clientX:350,clientY:180}); f.tick();
  assert.deepEqual(f.selected, ['c','a','b']);
  f.rows.splice(2, 1); f.lifecycle('remote-projection-applied'); f.tick();
  assert.deepEqual(f.selected, ['a','b']);
  f.emit('pointercancel'); assert.deepEqual(f.selected, []);
});
test('UI-EDITOR-001 hiding the document cancels a held gesture', () => {
  const f = fixture(); f.setSelected(['c']);
  f.emit('pointerdown'); f.emit('pointermove', {clientX:350,clientY:180}); f.tick();
  f.document.hidden = true; f.emit('visibilitychange');
  assert.equal(f.overlays.size, 0); assert.equal(f.frames.size, 0);
  assert.deepEqual(f.selected, ['c']);
});
test('UI-EDITOR-001 another primary pointer cannot replace an active gesture', () => {
  const f = fixture();
  f.emit('pointerdown'); f.emit('pointermove', {clientX:350,clientY:180}); f.tick();
  f.emit('pointerdown', {pointerId:2,pointerType:'pen',clientY:400});
  f.emit('pointermove', {pointerId:2,pointerType:'pen',clientX:350,clientY:400}); f.tick();
  assert.deepEqual(f.selected, ['a','b']);
  f.emit('pointerup', {clientX:350,clientY:180});
  assert.equal(f.overlays.size, 0);
});
test('UI-EDITOR-001 stationary edge drag traverses a long document and stops at both ends', () => {
  const f = fixture(false, 40); f.scroller.scrollHeight = 2400;
  f.emit('pointerdown'); f.emit('pointermove', {clientX:350,clientY:495});
  for(let i=0; i<200 && f.frames.size; i++) f.tick();
  assert.equal(f.scroller.scrollTop, 1900); assert.equal(f.frames.size, 0);
  assert.equal(f.selected.length, 40); assert.equal(f.overlays.size, 1);
  f.emit('pointermove', {clientX:350,clientY:5});
  for(let i=0; i<200 && f.frames.size; i++) f.tick();
  assert.equal(f.scroller.scrollTop, 0); assert.equal(f.frames.size, 0);
  assert.deepEqual(f.selected, ['a']);
  f.emit('pointerup', {clientX:350,clientY:5}); assert.equal(f.overlays.size, 0);
});

test('UI-EDITOR-001 media layout changes refresh a held selection without another pointer event', () => {
  const f=fixture();f.emit('pointerdown');f.emit('pointermove',{clientX:350,clientY:180});f.tick();
  assert.deepEqual(f.selected,['a','b']);assert.equal(f.frames.size,0);
  assert.equal(f.resizeObservers.length,1,'watch the active document while selecting');
  const observer=f.resizeObservers[0];assert.ok(observer.observed.has(f.rows[0]));
  f.rows[1].offset=100;observer.callback([]);observer.callback([]);
  assert.equal(f.frames.size,1,'coalesce a burst of image/row resize notifications');f.tick();
  assert.deepEqual(f.selected,['a'],'a block pushed outside the box must lose selection');
  f.rows[1].offset=0;observer.callback([]);f.tick();assert.deepEqual(f.selected,['a','b']);
  f.emit('pointerup',{clientX:350,clientY:180});assert.equal(observer.observed.size,0);
  observer.callback([]);assert.equal(f.frames.size,0,'queued callbacks cannot revive an ended gesture');
});

test('UI-EDITOR-001 resize observation tracks replacement rows and disconnects on cancellation', () => {
  const f=fixture();f.emit('pointerdown');f.emit('pointermove',{clientX:350,clientY:180});f.tick();
  const observer=f.resizeObservers[0];assert.ok(observer);
  const old=f.rows[1],replacement={...old};f.rows[1]=replacement;
  f.lifecycle('remote-projection-applied');f.tick();
  assert.ok(observer.observed.has(replacement));assert.equal(observer.observed.has(old),false);
  f.emit('pointercancel');assert.equal(observer.observed.size,0);
  f.emit('pointerdown');f.emit('pointermove',{clientX:350,clientY:180});f.tick();
  assert.equal(f.frames.size,0);observer.callback([]);assert.equal(f.frames.size,0,'old callbacks cannot repaint a new drag');
  f.emit('pointercancel');assert.equal(f.resizeObservers.at(-1).observed.size,0);
});

test('UI-EDITOR-001 upward scrolling begins below the fixed toolbar', () => {
  const f=fixture();f.scroller.scrollTop=100;
  f.emit('pointerdown',{clientY:200});f.emit('pointermove',{clientX:350,clientY:70});f.tick();
  assert.ok(f.scroller.scrollTop<100,'the visible content edge is below the toolbar');
  f.emit('pointercancel');
});
