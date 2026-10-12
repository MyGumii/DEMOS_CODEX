const test=require('node:test');
const assert=require('node:assert/strict');
const windows=require('../notion_static/notion_timeline_hour_window.js');

test('UI-DATABASE-HOUR sliding window keeps the visible time while moving in either direction',()=>{
  assert.equal(windows.start('owned-hour','2026-09-26'),'2026-09-26');
  assert.deepEqual(windows.shift('owned-hour','2026-09-26','right',12000),
    {start:'2026-09-27',left:12000-windows.DAY_WIDTH});
  assert.equal(windows.start('owned-hour','2026-09-26'),'2026-09-27');
  assert.deepEqual(windows.shift('owned-hour','2026-09-26','left',0),
    {start:'2026-09-26',left:windows.DAY_WIDTH});
  assert.equal(windows.start('owned-hour','2026-09-28'),'2026-09-28');
  windows.reset('owned-hour');
  assert.equal(windows.start('owned-hour','2026-09-26'),'2026-09-26');
});
