const assert = require('node:assert/strict');
const ui = require('../notion_static/notion_ui_interaction.js');
const ids = ['a', 'b', 'c', 'd', 'e'];
let focus = 'b';
for (const expected of [['b','c'], ['b','c','d'], ['b','c','d','e']]) {
  focus = ui.step(ids, focus, 1);
  assert.deepEqual(ui.range(ids, 'b', focus), expected);
}
for (const expected of [['b','c','d'], ['b','c'], ['b'], ['a','b'], ['a','b']]) {
  focus = ui.step(ids, focus, -1);
  assert.deepEqual(ui.range(ids, 'b', focus), expected);
}
assert.deepEqual(ui.range(ids, 'deleted', 'c'), ['c']);
assert.deepEqual(ui.range(ids, 'b', 'deleted'), []);
assert.deepEqual(ui.range(['a','c','e'], 'a', 'e'), ['a','c','e']);
assert.equal(ui.step([], 'a', 1), null);
const rects = [{id:'a',left:100,right:400,top:100,bottom:140},{id:'b',left:100,right:400,top:145,bottom:185},{id:'c',left:500,right:800,top:100,bottom:140}];
assert.deepEqual(ui.intersecting(rects,{x:80,y:110},{x:300,y:175}), ['a','b']);
assert.deepEqual(ui.intersecting(rects,{x:450,y:175},{x:300,y:110}), ['a','b']);
assert.deepEqual(ui.intersecting(rects,{x:450,y:50},{x:490,y:190}), []);
for (const side of ['above','below','center-below','left-center','right','row']) {
  for (const [width,height] of [[1280,720],[400,300],[320,180]]) {
    for (const [left,top] of [[0,0],[width-10,0],[0,height-10],[width-10,height-10]]) {
      const p=ui.placement({left,right:left+10,top,bottom:top+10},{width:340,height:430},{width,height},side);
      assert.ok(p.left>=8 && p.top>=8);
      assert.ok(p.left+p.width<=width-8 && p.top+p.height<=height-8);
    }
  }
}
assert.equal(ui.placement({left:219,right:239,top:140,bottom:160},{width:268,height:300},{width:1280,height:720},'row').left,227);
assert.equal(ui.placement({left:219,right:239,top:134,bottom:154},{width:268,height:300},{width:1280,height:720},'row').top,143);
assert.equal(ui.placement({left:219,right:239,top:140,bottom:160},{width:268,height:300},{width:390,height:720},'row').left,114);
assert.deepEqual(ui.placement({left:229,right:229,top:218,bottom:218},{width:265,height:322},{width:1280,height:720},'pointer'),
  {left:229,top:218,width:265,height:322});
assert.deepEqual(ui.placement({left:0,right:0,top:0,bottom:0},{width:265,height:322},{width:1280,height:720},'pointer'),
  {left:12,top:12,width:265,height:322});
assert.deepEqual(ui.placement({left:415.5,right:493.5,top:140,bottom:218},{width:408,height:380},{width:1280,height:720},'center-below'),
  {left:250.5,top:222,width:408,height:380});
assert.deepEqual(ui.placement({left:707,right:725,top:276,bottom:300},{width:265,height:472.4},{width:1920,height:863},'left-center',8,5),
  {left:437,top:51.80000000000001,width:265,height:472.4});
assert.deepEqual(ui.placement({left:16,right:38,top:229,bottom:251},{width:265,height:472.4},{width:390,height:430},'left-center',8,5),
  {left:43,top:8,width:265,height:414});
console.log('UI-EDITOR-001 / UI-SHELL-002: range, reverse, boundaries, geometry and popup placement passed');
