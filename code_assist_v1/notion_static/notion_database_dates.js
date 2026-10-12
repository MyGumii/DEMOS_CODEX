(function(root,factory){const catalog=typeof module==='object'&&module.exports?require('./notion_time_zones.js'):root.NotionTimeZones;const api=factory(catalog);if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionDatabaseDates=api;})(typeof globalThis==='object'?globalThis:this,function(catalog){
  'use strict';
  const SCALES={hour:'시간',day:'일',week:'주',biweek:'2주',month:'월',quarter:'분기',year:'년',five_year:'5년'};
  const iso=date=>`${String(date.getFullYear()).padStart(4,'0')}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  function parse(value){
    const match=/^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(String(value||''));if(!match)return null;
    const [,year,month,day]=match.map(Number),date=new Date(0);
    date.setFullYear(year,month-1,day);date.setHours(12,0,0,0);
    return date.getFullYear()===year&&date.getMonth()===month-1&&date.getDate()===day?date:null;
  }
  function parseInput(raw,today=new Date(),anchor=''){
    const value=String(raw||'').normalize('NFKC').trim();
    if(!value)return null;
    const base=parse(iso(today))||today;
    const relative={오늘:0,내일:1,모레:2,어제:-1};
    if(Object.hasOwn(relative,value)){
      const date=new Date(base);date.setDate(date.getDate()+relative[value]);return iso(date);
    }
    const offset=/^(\d{1,4})\s*일\s*(뒤|후|전)$/.exec(value);
    if(offset){const days=Number(offset[1]);if(days>3650)return null;
      const date=new Date(base);date.setDate(date.getDate()+(offset[2]==='전'?-days:days));return iso(date);}
    const weekday=/^(이번|다음)\s*주\s*(월|화|수|목|금|토|일)요일$/.exec(value);
    if(weekday){const date=new Date(base),target='월화수목금토일'.indexOf(weekday[2]);
      date.setDate(date.getDate()-((date.getDay()+6)%7)+target+(weekday[1]==='다음'?7:0));return iso(date);}
    const yearDate=/^(\d{4})\s*(?:[.\/-]|년)\s*(\d{1,2})\s*(?:[.\/-]|월)\s*(\d{1,2})\s*(?:\.|일)?$/.exec(value);
    const monthDate=/^(\d{1,2})\s*(?:[.\/-]|월)\s*(\d{1,2})\s*(?:\.|일)?$/.exec(value);
    const parts=yearDate?[Number(yearDate[1]),Number(yearDate[2]),Number(yearDate[3])]:
      monthDate?[(parse(anchor)||base).getFullYear(),Number(monthDate[1]),Number(monthDate[2])]:null;
    if(!parts)return null;
    if(parts[0]<1||parts[0]>9999)return null;
    const candidate=`${String(parts[0]).padStart(4,'0')}-${String(parts[1]).padStart(2,'0')}-${String(parts[2]).padStart(2,'0')}`;
    return parse(candidate)?candidate:null;
  }
  function editorDate(value){const parsed=parse(value);return parsed?`${parsed.getFullYear()}. ${parsed.getMonth()+1}. ${parsed.getDate()}.`:'';}
  function dayNumber(value){const date=parse(value);if(!date)return NaN;const utc=new Date(0);utc.setUTCFullYear(date.getFullYear(),date.getMonth(),date.getDate());utc.setUTCHours(0,0,0,0);return utc.getTime()/86400000;}
  function month(value,today=new Date()){return parse(`${value}-01`)?String(value):iso(today).slice(0,7);}
  function shiftMonth(value,delta,today=new Date()){
    const date=parse(value)||parse(iso(today)),day=date.getDate();date.setDate(1);date.setMonth(date.getMonth()+delta);
    const end=new Date(date);end.setMonth(end.getMonth()+1,0);date.setDate(Math.min(day,end.getDate()));return iso(date);
  }
  function navigate(config,type,action,today=new Date()){
    if(type==='calendar')return {month:action==='today'?iso(today).slice(0,7):shiftMonth(`${month(config.month,today)}-01`,Number(action),today).slice(0,7)};
    const anchor=parse(config.timeline_anchor)||parse(iso(today)),scale=SCALES[config.timeline_scale]?config.timeline_scale:'month';
    if(action==='today')return {timeline_anchor:iso(today)};
    const days={hour:1,day:1,week:7,biweek:14}[scale];
    if(days){anchor.setDate(anchor.getDate()+Number(action)*days);return {timeline_anchor:iso(anchor)};}
    return {timeline_anchor:shiftMonth(iso(anchor),Number(action)*({quarter:3,year:12,five_year:60}[scale]||1),today)};
  }
  function calendar(value,today=new Date(),sixWeeks=false){
    const selected=month(value,today),first=parse(`${selected}-01`),end=new Date(first);end.setMonth(end.getMonth()+1,0);
    const count=sixWeeks?42:Math.ceil((first.getDay()+end.getDate())/7)*7,start=new Date(first);start.setDate(1-first.getDay());
    return {month:selected,year:first.getFullYear(),monthIndex:first.getMonth(),days:Array.from({length:count},(_,index)=>{const date=new Date(start);date.setDate(start.getDate()+index);return {iso:iso(date),day:date.getDate(),outside:date.getMonth()!==first.getMonth(),today:iso(date)===iso(today)};})};
  }
  function dateRange(item,field){
    const raw=item?.[field],start=parse(raw&&typeof raw==='object'?raw.start:raw),end=parse(raw&&typeof raw==='object'?raw.end:item?.[`${field}_end`]);
    if(!start)return {start:'',end:''};
    return {start:iso(start),end:end&&end>=start?iso(end):iso(start)};
  }
  const DATE_FORMATS={full:'전체 날짜',short:'날짜 간단히 표기',mdy:'월/일/년',dmy:'일/월/년',ymd:'년/월/일',relative:'상대'};
  const TIME_FORMATS={hidden:'숨김','12h':'12시간','24h':'24시간'};
  const REMINDER_OPTIONS={
    all_day:[['','알림 없음'],['day_0','당일 (오전 9:00)'],['day_1','(오전 9:00) 1일 전'],['day_2','(오전 9:00) 2일 전'],['day_7','(오전 9:00) 1주 전']],
    timed:[['','알림 없음'],['minute_0','이벤트 정각'],['minute_5','5분 전'],['minute_10','10분 전'],['minute_15','15분 전'],['minute_30','30분 전'],['minute_60','1시간 전'],['minute_120','2시간 전'],['day_1','(오전 9:00) 1일 전'],['day_2','(오전 9:00) 2일 전']]
  };
  const localTimeZone=()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
  let cachedTimeZones;
  const timeZones=()=>{
    if(cachedTimeZones)return cachedTimeZones;
    const supported=Array.isArray(catalog)?catalog:(typeof Intl.supportedValuesOf==='function'?Intl.supportedValuesOf('timeZone'):[]);
    cachedTimeZones=[...new Set([localTimeZone(),'UTC',...supported])].filter(zone=>{
      try{new Intl.DateTimeFormat('en-US',{timeZone:zone});return true;}catch{return false;}
    });
    return cachedTimeZones;
  };
  function zoneWallTime(day,time,zone=''){
    if(!parse(day)||!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time||''))return null;
    const [year,month,date]=day.split('-').map(Number),[hour,minute]=time.split(':').map(Number);
    if(!zone){const local=new Date(0);local.setFullYear(year,month-1,date);local.setHours(hour,minute,0,0);
      return local.getFullYear()===year&&local.getMonth()+1===month&&local.getDate()===date&&
        local.getHours()===hour&&local.getMinutes()===minute?local:null;}
    let formatter;
    try{formatter=new Intl.DateTimeFormat('en-US',{timeZone:zone,hourCycle:'h23',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
    catch{return null;}
    const utc=(y,m,d,h,mm)=>{const result=new Date(0);result.setUTCFullYear(y,m-1,d);result.setUTCHours(h,mm,0,0);return result.getTime();};
    const wall=timestamp=>{const parts=Object.fromEntries(formatter.formatToParts(timestamp).filter(part=>part.type!=='literal').map(part=>[part.type,Number(part.value)]));
      return utc(parts.year,parts.month,parts.day,parts.hour,parts.minute);};
    const target=utc(year,month,date,hour,minute),offsets=new Set([-86400000,0,86400000].map(delta=>wall(target+delta)-(target+delta)));
    const matches=[...offsets].map(offset=>target-offset).filter(timestamp=>wall(timestamp)===target);
    return matches.length?new Date(Math.min(...matches)):null;
  }
  function reminderOptions(value){return value?.time?REMINDER_OPTIONS.timed:REMINDER_OPTIONS.all_day;}
  function buildReminder(value,option,userId){
    const start=parse(value?.start),user=String(userId||'').trim();
    if(!start||!user||!reminderOptions(value).some(([key])=>key===option)||!option)return null;
    const zone=value.time_zone||value.reminder?.zone||localTimeZone(),day=option.startsWith('day_')?(()=>{const date=new Date(0);
      date.setUTCFullYear(start.getFullYear(),start.getMonth(),start.getDate());date.setUTCDate(date.getUTCDate()-Number(option.slice(4)));
      return `${String(date.getUTCFullYear()).padStart(4,'0')}-${String(date.getUTCMonth()+1).padStart(2,'0')}-${String(date.getUTCDate()).padStart(2,'0')}`;})():value.start;
    if(option.startsWith('day_')){const moment=zoneWallTime(day,'09:00',zone);
      return moment?{at:moment.toISOString(),lead:0,user_id:user,preset:option,zone}:null;}
    if(!value.time)return null;
    const moment=zoneWallTime(day,value.time,zone);
    return moment?{at:moment.toISOString(),lead:Number(option.slice(7)),user_id:user,preset:option,zone}:null;
  }
  function reminderOption(value){
    const reminder=value?.reminder;
    if(!reminder||!parse(value.start))return '';
    return reminderOptions(value).find(([key])=>key&&
      (()=>{const candidate=buildReminder(value,key,reminder.user_id);
        return candidate?.at===reminder.at&&candidate.lead===reminder.lead;})())?.[0]||'';
  }
  function dateValue(raw,fallbackEnd=''){
    const value=raw&&typeof raw==='object'?raw:{start:raw,end:fallbackEnd};
    const start=parse(value?.start),end=parse(value?.end);
    return {start:start?iso(start):'',end:end&&start&&end>=start?iso(end):'',
      time:/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value?.time||'')?value.time:'',
      end_time:/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value?.end_time||'')?value.end_time:'',
      date_format:Object.hasOwn(DATE_FORMATS,value?.date_format)?value.date_format:'full',
      time_format:Object.hasOwn(TIME_FORMATS,value?.time_format)?value.time_format:'12h',
      time_zone:typeof value?.time_zone==='string'?value.time_zone:'',
      reminder:value?.reminder&&typeof value.reminder==='object'?value.reminder:null};
  }
  function serializeDateValue(value){
    const start=parse(value.start),end=parse(value.end);
    if(!start)return null;
    const clean={start:iso(start)};
    if(end&&end>=start)clean.end=iso(end);
    if(value.time)clean.time=value.time;
    if(clean.end&&clean.time&&value.end_time)clean.end_time=value.end_time;
    if(Object.hasOwn(DATE_FORMATS,value.date_format)&&value.date_format!=='full')clean.date_format=value.date_format;
    if(clean.time&&Object.hasOwn(TIME_FORMATS,value.time_format)&&value.time_format!=='12h')clean.time_format=value.time_format;
    if(clean.time&&value.time_zone)clean.time_zone=value.time_zone;
    if(value.reminder)clean.reminder=value.reminder;
    return Object.keys(clean).length===1?clean.start:clean;
  }
  function displayDateValue(raw,fallbackEnd='',today=new Date()){
    const value=dateValue(raw,fallbackEnd);
    if(!value.start)return '';
    const formatDate=day=>{
      const parsed=parse(day),[year,month,date]=[parsed.getFullYear(),parsed.getMonth()+1,parsed.getDate()];
      if(value.date_format==='short')return `${month}월 ${date}일`;
      if(value.date_format==='mdy')return `${month}/${date}/${year}`;
      if(value.date_format==='dmy')return `${date}/${month}/${year}`;
      if(value.date_format==='ymd')return `${year}/${month}/${date}`;
      if(value.date_format==='relative'){
        const difference=dayNumber(day)-dayNumber(iso(today));
        if(Math.abs(difference)<=7)return new Intl.RelativeTimeFormat('ko-KR',{numeric:'auto'}).format(difference,'day');
      }
      return new Intl.DateTimeFormat('ko-KR',{year:'numeric',month:'long',day:'numeric'}).format(parsed);
    };
    const formatTime=time=>{
      if(!time||value.time_format==='hidden')return '';
      const [hour,minute]=time.split(':').map(Number);
      const marker=hour<12?'오전':'오후';
      return value.time_format==='24h'?time:`${marker} ${hour%12||12}:${String(minute).padStart(2,'0')}`;
    };
    const viewerTime=(day,time)=>{if(!time||!value.time_zone)return {day,time};
      const instant=zoneWallTime(day,time,value.time_zone);
      return instant?{day:iso(instant),time:`${String(instant.getHours()).padStart(2,'0')}:${String(instant.getMinutes()).padStart(2,'0')}`}:{day,time};};
    const first=viewerTime(value.start,value.time);
    const showTime=Boolean(value.time&&value.time_format!=='hidden');
    const start=`${formatDate(first.day)}${showTime?' '+formatTime(first.time):''}`;
    if(!value.end)return start;
    const last=viewerTime(value.end,value.end_time||value.time);
    const end=`${formatDate(last.day)}${showTime?' '+formatTime(last.time):''}`;
    return `${start} → ${end}`;
  }
  function timeline(config,today=new Date(),options={}){
    const scale=SCALES[config.timeline_scale]?config.timeline_scale:'month',anchor=parse(config.timeline_anchor)||parse(iso(today));
    const start=new Date(scale==='hour'?(parse(options.hourStart)||anchor):anchor);let step='day',stride=1,count,label;
    if(scale==='hour'){
      step='quarter_hour';count=192;
      label=`${start.getFullYear()}년 ${start.getMonth()+1}월 ${start.getDate()}일`;
    }else if(['day','week','biweek'].includes(scale)){
      if(scale!=='day')start.setDate(start.getDate()-start.getDay());count={day:1,week:7,biweek:14}[scale];
      label=`${start.getFullYear()}년 ${start.getMonth()+1}월 ${start.getDate()}일`;
    }else if(scale==='year'||scale==='five_year'){
      start.setMonth(0,1);step='month';count=scale==='year'?12:60;
      label=scale==='year'?`${start.getFullYear()}년`:`${start.getFullYear()}–${start.getFullYear()+4}년`;
    }else{
      start.setDate(1);if(scale==='quarter'){start.setMonth(Math.floor(start.getMonth()/3)*3);stride=7;}
      const end=new Date(start);end.setMonth(end.getMonth()+(scale==='quarter'?3:1));
      count=Math.ceil((dayNumber(iso(end))-dayNumber(iso(start)))/stride);
      label=scale==='quarter'?`${start.getFullYear()}년 ${Math.floor(start.getMonth()/3)+1}분기`:`${start.getFullYear()}년 ${start.getMonth()+1}월`;
    }
    const end=new Date(start);if(step==='month')end.setMonth(end.getMonth()+count);else if(scale==='quarter')end.setMonth(end.getMonth()+3);else end.setDate(end.getDate()+(step==='quarter_hour'?2:count));
    const slots=Array.from({length:count},(_,index)=>{
      if(step==='quarter_hour'){
        const hour=Math.floor(index%96/4),minute=index%4*15;
        return {label:`${hour<12?'오전':'오후'} ${hour%12||12}:${String(minute).padStart(2,'0')}`,weekend:false};
      }
      const date=new Date(start);if(step==='month')date.setMonth(date.getMonth()+index);else date.setDate(date.getDate()+index*stride);
      return {label:step==='month'?`${scale==='five_year'&&date.getMonth()===0?date.getFullYear()+'년 ':''}${date.getMonth()+1}월`:stride===7?`${date.getMonth()+1}/${date.getDate()}`:String(date.getDate()),weekend:step==='day'&&stride===1&&[0,6].includes(date.getDay())};});
    const slotFor=(value,time='')=>{const date=parse(value);if(!date)return NaN;
      if(step==='quarter_hour'){
        const minutes=/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time)?Number(time.slice(0,2))*60+Number(time.slice(3)):0;
        return (dayNumber(iso(date))-dayNumber(iso(start)))*96+Math.floor(minutes/15);
      }
      return step==='month'?(date.getFullYear()-start.getFullYear())*12+date.getMonth()-start.getMonth():Math.floor((dayNumber(iso(date))-dayNumber(iso(start)))/stride);};
    const days=step==='quarter_hour'?Array.from({length:2},(_,index)=>{
      const date=new Date(start);date.setDate(date.getDate()+index);
      return {label:`${date.getFullYear()}년 ${date.getMonth()+1}월 ${date.getDate()}일`,startSlot:index*96};
    }):[];
    return {scale,start:iso(start),end:iso(end),label,slots,slotFor,days};
  }
  // The caller persists only the instance view; source rows/schema stay untouched.
  function withViewConfig(block,patch){const next=structuredClone(block),view=next.props.views.find(v=>v.id===next.props.active_view_id);if(!view)return null;view.config={...view.config,...patch};return next;}
  function undatedAssignmentValue(viewType,scale,today=new Date(),context={}){
    const start=iso(today);
    if(viewType==='timeline'&&scale==='hour'){
      const anchor=parse(context.hourAnchor)||parse(start);
      const visible=Number.isFinite(context.hourVisibleSlot)
        ?Math.max(0,Math.floor(context.hourVisibleSlot))
        :Math.max(0,Math.floor((today.getHours()*60+today.getMinutes())/15)-8);
      anchor.setHours(0,0,0,0);
      anchor.setMinutes((visible+4)*15);
      const end=new Date(anchor);end.setHours(end.getHours()+1);
      const time=value=>`${String(value.getHours()).padStart(2,'0')}:${String(value.getMinutes()).padStart(2,'0')}`;
      return {start:iso(anchor),end:iso(end),time:time(anchor),end_time:time(end)};
    }
    if(viewType!=='timeline'||scale!=='month')return start;
    const end=parse(start);end.setDate(end.getDate()+4);
    return {start,end:iso(end)};
  }
  function assignment(block,rowIndex,field,today=new Date(),viewType=block?.type,context={}){
    const row=block.props?.rows?.[rowIndex],property=block.props?.properties?.find(p=>p.id===field);
    if(!row||property?.type!=='date'||dateRange(row,field).start)return null;
    return {row_id:String(row._canonical_row_id||''),property_id:field,value:undatedAssignmentValue(viewType,block.props?.timeline_scale,today,context),expected_target_version:Number(row._canonical_cell_versions?.[field]||0)};
  }
  return {SCALES,DATE_FORMATS,TIME_FORMATS,REMINDER_OPTIONS,localTimeZone,timeZones,zoneWallTime,reminderOptions,buildReminder,reminderOption,iso,parse,parseInput,editorDate,dayNumber,month,shiftMonth,navigate,calendar,dateRange,dateValue,serializeDateValue,displayDateValue,timeline,withViewConfig,undatedAssignmentValue,assignment};
});
