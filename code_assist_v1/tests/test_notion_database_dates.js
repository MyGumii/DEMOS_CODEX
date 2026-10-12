'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const dates=require('../notion_static/notion_database_dates.js');
const at=value=>new Date(`${value}T12:00:00`);
test('UI-DATABASE-DATES invalid dates never normalize to another day and date ranges include their end',()=>{
  for(const value of ['2026-02-29','2026-13-01','2026-00-01','2026-04-31','garbage',''])assert.equal(dates.parse(value),null);
  assert.equal(dates.iso(dates.parse('2024-02-29T09:00:00+09:00')),'2024-02-29');
  assert.deepEqual(dates.dateRange({date:{start:'2024-02-28',end:'2024-03-01'}},'date'),{start:'2024-02-28',end:'2024-03-01'});
  assert.deepEqual(dates.dateRange({date:'2026-02-28',date_end:'2026-02-20'},'date'),{start:'2026-02-28',end:'2026-02-28'});
  assert.ok(Number.isNaN(dates.dayNumber('2026-02-30')));
});
test('UI-DATABASE-DATES local today and leap/month-end navigation do not skip February',()=>{
  assert.equal(dates.shiftMonth('2026-01-31',1),'2026-02-28');
  assert.equal(dates.shiftMonth('2024-01-31',1),'2024-02-29');
  assert.equal(dates.shiftMonth('2024-02-29',12),'2025-02-28');
  assert.deepEqual(dates.navigate({month:'2026-12'},'calendar',1),{month:'2027-01'});
  assert.deepEqual(dates.navigate({timeline_anchor:'2026-01-31'},'timeline',1),{timeline_anchor:'2026-02-28'});
  const today=new Date(2026,8,1,0,1);assert.deepEqual(dates.navigate({},'calendar','today',today),{month:'2026-09'});
  assert.deepEqual(dates.navigate({},'timeline','today',today),{timeline_anchor:'2026-09-01'});
});
test('UI-DATABASE-DATES calendars render exactly the intersecting weeks',()=>{
  const sep=dates.calendar('2026-09',at('2026-09-16'));
  assert.equal(sep.days.length,35);assert.equal(sep.days[0].iso,'2026-08-30');assert.equal(sep.days.at(-1).iso,'2026-10-03');assert.equal(sep.days.filter(d=>d.today).length,1);
  assert.equal(dates.calendar('2026-05').days.length,42);
  assert.equal(dates.calendar('2026-02').days.length,28);
  assert.equal(dates.calendar('2026-02',at('2026-02-16'),true).days.length,42);
  assert.equal(dates.calendar('2026-09',at('2026-09-16'),true).days.at(-1).iso,'2026-10-10');
  assert.equal(dates.calendar('2026-13',at('2026-09-16')).month,'2026-09');
});
test('UI-DATABASE-DATES formatted time and range round-trip through one date value',()=>{
  const raw={start:'2026-09-16',end:'2026-09-18',time:'09:00',end_time:'10:30',date_format:'short',time_format:'24h'};
  assert.deepEqual(dates.serializeDateValue(dates.dateValue(raw)),raw);
  assert.equal(dates.displayDateValue(raw,'',at('2026-09-16')),'9월 16일 09:00 → 9월 18일 10:30');
  assert.equal(dates.displayDateValue({start:'2026-09-16',time:'09:00'},'',at('2026-09-16')),'2026년 9월 16일 오전 9:00');
  const hidden={...raw,time_format:'hidden'};
  assert.deepEqual(dates.serializeDateValue(dates.dateValue(hidden)),hidden);
  assert.equal(dates.displayDateValue(hidden,'',at('2026-09-16')),'9월 16일 → 9월 18일');
  assert.equal(dates.TIME_FORMATS.hidden,'숨김');
  assert.equal(dates.serializeDateValue(dates.dateValue('2026-09-16')),'2026-09-16');
  assert.equal(dates.serializeDateValue(dates.dateValue(null)),null);
  assert.deepEqual(dates.serializeDateValue(dates.dateValue({start:'2026-09-16',date_format:'mdy'})),
    {start:'2026-09-16',date_format:'mdy'});
});
test('UI-DATABASE-DATES reminder presets survive date-cell serialization and use local calendar days',()=>{
  const allDay={start:'2027-01-01'};
  const prior=dates.buildReminder(allDay,'day_1','owner');
  assert.deepEqual(prior,{at:new Date(2026,11,31,9).toISOString(),lead:0,user_id:'owner',preset:'day_1',zone:dates.localTimeZone()});
  assert.equal(dates.reminderOption({...allDay,reminder:prior}),'day_1');
  assert.deepEqual(dates.serializeDateValue(dates.dateValue({...allDay,reminder:prior})),
    {...allDay,reminder:prior});
  const timed={start:'2027-01-01',time:'10:30'};
  assert.equal(dates.REMINDER_OPTIONS.timed.find(([key])=>key==='minute_0')[1],'이벤트 정각');
  const fifteen=dates.buildReminder(timed,'minute_15','owner');
  assert.deepEqual(fifteen,{at:new Date(2027,0,1,10,30).toISOString(),lead:15,user_id:'owner',preset:'minute_15',zone:dates.localTimeZone()});
  assert.equal(dates.reminderOption({...timed,reminder:fifteen}),'minute_15');
  assert.equal(dates.buildReminder(timed,'day_7','owner'),null);
});
test('UI-DATABASE-DATES tracked reminders keep recipient and zone when another editor moves a date',()=>{
  const original={start:'2027-01-01',time:'09:00',time_zone:'Asia/Seoul'};
  const reminder=dates.buildReminder(original,'minute_15','content-editor');
  const moved={...original,start:'2027-01-02',reminder};
  assert.equal(dates.reminderOption({...original,reminder}),'minute_15');
  assert.deepEqual(dates.buildReminder(moved,'minute_15',reminder.user_id),{
    at:'2027-01-02T00:00:00.000Z',lead:15,user_id:'content-editor',preset:'minute_15',zone:'Asia/Seoul'
  });
  assert.deepEqual(dates.serializeDateValue(dates.dateValue(moved)),moved);
});
test('UI-DATABASE-DATES selected time zone determines the instant and rejects missing DST hours',()=>{
  const zone='America/New_York',value={start:'2026-09-16',time:'09:00',time_zone:zone};
  assert.equal(dates.zoneWallTime(value.start,value.time,zone).toISOString(),'2026-09-16T13:00:00.000Z');
  assert.equal(dates.zoneWallTime('2026-03-08','02:30',zone),null);
  assert.equal(dates.zoneWallTime('2026-11-01','01:30',zone).toISOString(),'2026-11-01T05:30:00.000Z');
  assert.equal(dates.buildReminder(value,'minute_15','owner').at,'2026-09-16T13:00:00.000Z');
  assert.equal(dates.buildReminder({...value,time_zone:'Asia/Seoul'},'minute_15','owner').at,'2026-09-16T00:00:00.000Z');
  assert.deepEqual(dates.serializeDateValue(dates.dateValue(value)),value);
  if(dates.localTimeZone()==='Asia/Seoul')
    assert.equal(dates.displayDateValue(value),'2026년 9월 16일 오후 10:00');
});
test('UI-DATABASE-DATES searchable zone catalog includes valid IANA aliases omitted by Intl.supportedValuesOf',()=>{
  const listed=dates.timeZones(),alias='America/Argentina/Buenos_Aires';
  assert.ok(listed.length>=550,listed.length);
  assert.ok(listed.includes(alias));
  assert.equal(dates.zoneWallTime('2026-09-16','09:00',alias).toISOString(),
    dates.zoneWallTime('2026-09-16','09:00','America/Buenos_Aires').toISOString());
});
test('UI-DATABASE-DATES localized date input resolves exact days without rollover',()=>{
  const today=at('2026-09-24');
  assert.equal(dates.parseInput('2026. 9. 16.',today),'2026-09-16');
  assert.equal(dates.parseInput('2026년 9월 16일',today),'2026-09-16');
  assert.equal(dates.parseInput('9월 18일',today,'2025-09-16'),'2025-09-18');
  assert.equal(dates.parseInput('9/18',today),'2026-09-18');
  assert.equal(dates.parseInput('내일',today),'2026-09-25');
  assert.equal(dates.parseInput('모레',today),'2026-09-26');
  assert.equal(dates.parseInput('3일 전',today),'2026-09-21');
  assert.equal(dates.parseInput('다음 주 월요일',today),'2026-09-28');
  assert.equal(dates.editorDate('2026-09-16'),'2026. 9. 16.');
  for(const invalid of ['2026. 2. 29.','2026-13-01','2/30','9999일 뒤','0. 9. 16.','무작위'])
    assert.equal(dates.parseInput(invalid,today),null,invalid);
});
test('UI-DATABASE-DATES each timeline scale has coherent slots, boundaries and navigation',()=>{
  const expected={hour:192,day:1,week:7,biweek:14,month:30,quarter:14,year:12,five_year:60};
  for(const [scale,count] of Object.entries(expected)){
    const config={timeline_anchor:'2026-09-16',timeline_scale:scale},range=dates.timeline(config);
    assert.equal(range.slots.length,count,scale);assert.equal(range.slotFor(range.start),0,scale);
    assert.ok(dates.dayNumber(range.end)>dates.dayNumber(range.start));
    assert.ok(dates.dayNumber(dates.navigate(config,'timeline',1).timeline_anchor)>dates.dayNumber(config.timeline_anchor));
  }
  assert.equal(dates.timeline({timeline_anchor:'2026-09-16',timeline_scale:'quarter'}).end,'2026-10-01');
  assert.equal(dates.timeline({timeline_anchor:'2026-12-31',timeline_scale:'week'}).end,'2027-01-03');
  const hourly=dates.timeline({timeline_anchor:'2026-09-16',timeline_scale:'hour'});
  assert.equal(hourly.end,'2026-09-18');
  assert.equal(hourly.slots[0].label,'오전 12:00');
  assert.equal(hourly.slots[95].label,'오후 11:45');
  assert.equal(hourly.slots[96].label,'오전 12:00');
  assert.equal(hourly.slots[191].label,'오후 11:45');
  assert.deepEqual(hourly.days.map(day=>day.startSlot),[0,96]);
  assert.equal(hourly.slotFor('2026-09-16','17:00'),68);
  assert.equal(hourly.slotFor('2026-09-16','17:30'),70);
  assert.equal(hourly.slotFor('2026-09-17','00:00'),96);
  const shifted=dates.timeline({timeline_scale:'hour',timeline_anchor:'2026-09-16'},
    at('2026-09-16'),{hourStart:'2026-09-17'});
  assert.equal(shifted.start,'2026-09-17');
  assert.equal(shifted.end,'2026-09-19');
  assert.equal(shifted.slotFor('2026-09-17','08:15'),33);
  assert.equal(shifted.slotFor('2026-09-18','00:15'),97);
});
test('UI-LINKED-DATES view navigation changes only active instance config and does not mutate inputs',()=>{
  const block={id:'linked',props:{linked_database:{document_id:'source',database_id:'db'},active_view_id:'a',views:[{id:'a',config:{filters:[{property:'title',value:'A'}]}},{id:'b',config:{month:'2024-02'}}]}};
  const snapshot=structuredClone(block),next=dates.withViewConfig(block,{month:'2026-10',calendar_date_property:'date'});
  assert.deepEqual(block,snapshot);assert.deepEqual(next.props.views[1],block.props.views[1]);assert.deepEqual(next.props.views[0].config.filters,block.props.views[0].config.filters);
  assert.equal(next.props.views[0].config.month,'2026-10');assert.deepEqual(next.props.linked_database,block.props.linked_database);assert.equal(next.props.rows,undefined);
});
test('UI-LINKED-DATES assigning today targets the selected empty source cell and retains its CAS version',()=>{
  const block={props:{properties:[{id:'date',type:'date'},{id:'title',type:'title'}],rows:[{_canonical_row_id:'a',_canonical_cell_versions:{date:4},date:''},{_canonical_row_id:'b',date:'2026-09-16'}]}};
  const snapshot=structuredClone(block);
  assert.deepEqual(dates.assignment(block,0,'date',at('2026-09-16')),{row_id:'a',property_id:'date',value:'2026-09-16',expected_target_version:4});
  assert.equal(dates.assignment(block,1,'date'),null);assert.equal(dates.assignment(block,0,'title'),null);assert.equal(dates.assignment(block,7,'date'),null);assert.deepEqual(block,snapshot);
});
test('UI-DATABASE-DATES month timeline undated assignment spans five local days; week and calendar assign one day',()=>{
  const today=at('2026-09-26');
  assert.deepEqual(dates.undatedAssignmentValue('timeline','month',today),{start:'2026-09-26',end:'2026-09-30'});
  assert.deepEqual(dates.undatedAssignmentValue('timeline','month',at('2026-10-30')),{start:'2026-10-30',end:'2026-11-03'});
  assert.equal(dates.undatedAssignmentValue('timeline','week',today),'2026-09-26');
  assert.equal(dates.undatedAssignmentValue('calendar','month',today),'2026-09-26');
  const block={type:'timeline',props:{timeline_scale:'month',properties:[{id:'date',type:'date'}],rows:[{_canonical_row_id:'row',_canonical_cell_versions:{date:3}}]}};
  assert.deepEqual(dates.assignment(block,0,'date',today),{row_id:'row',property_id:'date',value:{start:'2026-09-26',end:'2026-09-30'},expected_target_version:3});
  block.props.timeline_scale='week';
  assert.equal(dates.assignment(block,0,'date',today).value,'2026-09-26');
});
test('UI-DATABASE-DATES hour timeline undated assignment follows the visible quarter-hour window',()=>{
  const today=at('2026-09-26'),context={hourAnchor:'2026-09-26',hourVisibleSlot:29};
  const expected={start:'2026-09-26',end:'2026-09-26',time:'08:15',end_time:'09:15'};
  assert.deepEqual(dates.undatedAssignmentValue('timeline','hour',today,context),expected);
  assert.deepEqual(dates.undatedAssignmentValue('timeline','hour',today,
    {hourAnchor:'2026-09-26',hourVisibleSlot:92}),
    {start:'2026-09-27',end:'2026-09-27',time:'00:00',end_time:'01:00'});
  const block={type:'timeline',props:{timeline_scale:'hour',properties:[{id:'date',type:'date'}],
    rows:[{_canonical_row_id:'row',_canonical_cell_versions:{date:7}}]}};
  assert.deepEqual(dates.assignment(block,0,'date',today,'timeline',context),
    {row_id:'row',property_id:'date',value:expected,expected_target_version:7});
});
