'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const clipboard=require('../notion_static/notion_block_clipboard'),ops=require('../notion_static/notion_database_item_blocks');
const source={origin:'http://localhost:14579',pageId:'source-page'};
const block=(id,type,content='',props={})=>({id,type,content,props});
const mixed=()=>[block('h','heading2','제목'),block('t','toggle','부모',{open:false,children:[block('b','bullet','굵은 글자',{rich_text_html:'<b>굵은 글자</b>'}),block('i','image','캡션',{file_id:'file-a',name:'샘플.png'})]}),block('s','simple_table','',{rows:[['첫 줄\n다음 줄','탭\t내용'],['"인용"','']]}),block('c','callout','안내',{icon:'💡',background_color:'yellow'})];
const options=(shape='tree')=>{let n=0;return {shape,origin:source.origin,createId:()=>`fresh-${++n}`,sanitize:value=>value.replace(/<script>.*?<\/script>/g,''),allowedTypes:new Set(['paragraph','heading2','toggle','bullet','image','simple_table','callout'])};};
test('UI-ITEM-CLIPBOARD nested rich blocks and attachments survive independent cloning without changing source',()=>{
  const blocks=mixed(),before=structuredClone(blocks),encoded=clipboard.encode(blocks,{shape:'tree',source}),out=clipboard.decode(encoded,options());
  assert.deepEqual(blocks,before);assert.equal(out[1].props.open,false);assert.equal(out[1].props.children[0].props.rich_text_html,'<b>굵은 글자</b>');
  assert.equal(out[1].props.children[1].props.file_id,'file-a');assert.deepEqual(out[2].props.rows,before[2].props.rows);assert.equal(out[3].props.background_color,'yellow');
  const ids=ops.entries(out).map(e=>e.block.id);assert.equal(ids.length,new Set(ids).size);assert.ok(ids.every(id=>!ops.entries(blocks).some(e=>e.block.id===id)));
});
test('UI-ITEM-CLIPBOARD item hierarchy converts into visible main-page indentation and back',()=>{
  const first=clipboard.decode(clipboard.encode(mixed(),{shape:'tree',source}),options('flat'));
  assert.deepEqual(first.map(b=>b.type),['heading2','toggle','bullet','image','simple_table','callout']);assert.deepEqual(first.map(b=>b.props.indent),[0,0,1,1,0,0]);
  assert.ok(first.every(b=>!b.props.children));
  const out=clipboard.decode(clipboard.encode(first,{shape:'flat',source}),options('tree'));
  assert.equal(out.length,4);assert.deepEqual(out[1].props.children.map(b=>b.type),['bullet','image']);assert.equal(out[1].props.open,false);
});
test('UI-ITEM-CLIPBOARD standalone indented selections do not inherit missing parents or lose relative gaps',()=>{
  const tree=clipboard.tree([block('a','toggle','',{indent:2}),block('b','paragraph','',{indent:4}),block('c','paragraph','',{indent:2})]);
  assert.equal(tree.length,2);assert.equal(tree[0].props.indent,0);assert.equal(tree[0].props.children[0].props.indent,1);
  const flat=clipboard.flat(tree);
  assert.deepEqual(flat.map(b=>b.props.indent),[0,1,0]);
  assert.deepEqual(require('../notion_static/notion_editor_core').blockOutline(flat).map(e=>e.visualIndent),[0,2,0]);
});
test('UI-ITEM-CLIPBOARD insertion replaces only a truly empty paragraph and respects a nested destination',()=>{
  const body=[block('t','toggle','parent',{children:[block('a','paragraph','original'),block('empty','paragraph')]}),block('tail','paragraph','tail')],before=structuredClone(body);
  const out=ops.insert(body,'empty',[block('new','image','caption',{file_id:'f'})]);assert.deepEqual(body,before);
  assert.deepEqual(out.blocks[0].props.children.map(b=>b.id),['a','new']);assert.equal(out.blocks[1].content,'tail');
  const after=ops.insert(body,'a',[block('next','paragraph','next')]);assert.deepEqual(after.blocks[0].props.children.map(b=>b.id),['a','next','empty']);
  assert.equal(ops.insert(body,'missing',[block('n','paragraph')]),null);
});
test('UI-ITEM-CLIPBOARD limits count nested children and UTF-8 bytes, rejecting malformed or duplicate identities',()=>{
  for(const blocks of [[block('a','paragraph'),block('a','paragraph')],[block('a','table')],[{id:'a',type:'paragraph',content:{},props:{}}],[block('a','paragraph','',{children:{}})]])assert.throws(()=>clipboard.decode(JSON.stringify({version:2,shape:'tree',blocks}),options()));
  let deep=block('d0','toggle');for(let n=1;n<35;n++)deep=block('d'+n,'toggle','',{children:[deep]});assert.throws(()=>clipboard.encode([deep]));
  assert.throws(()=>clipboard.encode([block('root','toggle','',{children:Array.from({length:1000},(_,n)=>block('n'+n,'paragraph'))})]));
  assert.throws(()=>clipboard.encode([block('a','paragraph','한'.repeat(700000))]));
  assert.throws(()=>clipboard.decode('{invalid',options()));
  assert.throws(()=>clipboard.decode('{"version":2,"shape":"tree","blocks":[{"id":"a","type":"paragraph","content":"","props":{"__proto__":{"x":1}}}]}',options()));
});
test('UI-ITEM-CLIPBOARD every descendant is sanitized and collision with destination or source IDs is avoided',()=>{
  const blocks=[block('a','toggle','',{children:[block('b','paragraph','text',{rich_text_html:'<b>text</b><script>bad()</script>'})]})];
  let i=0;const sequence=['a','b','existing','new1','new2'];
  const out=clipboard.decode(clipboard.encode(blocks,{shape:'tree'}),{...options(),usedIds:['existing'],createId:()=>sequence[i++]});
  assert.equal(out[0].id,'new1');assert.equal(out[0].props.children[0].id,'new2');assert.equal(out[0].props.children[0].props.rich_text_html,'<b>text</b>');
  assert.throws(()=>clipboard.decode(clipboard.encode(blocks),{...options(),createId:()=> 'a'}));
});
test('UI-ITEM-CLIPBOARD ordinary HTML contains formatted list/table/media and a transport fallback',()=>{
  const data=new Map();clipboard.write({setData:(k,v)=>data.set(k,v)},mixed(),{shape:'tree',source,sanitize:v=>v,fileUrl:id=>'/file/'+id,text:'본문'});
  assert.equal(data.get('text/plain'),'본문');assert.equal(JSON.parse(data.get(clipboard.MIME)).version,2);
  assert.match(data.get('text/html'),/data-notion-local-blocks=/);assert.match(data.get('text/html'),/<details/);assert.match(data.get('text/html'),/<ul><li><b>/);assert.match(data.get('text/html'),/<table>/);assert.match(data.get('text/html'),/http:\/\/localhost:14579\/file\/file-a/);
  assert.equal(clipboard.read({getData:k=>data.get(k)||''}),data.get(clipboard.MIME));
});
test('UI-ITEM-CLIPBOARD legacy payloads work while foreign service file identifiers cannot collide locally',()=>{
  assert.equal(clipboard.decode(JSON.stringify({version:1,blocks:[block('a','paragraph','legacy')]}),options())[0].content,'legacy');
  assert.throws(()=>clipboard.decode(clipboard.encode(mixed(),{shape:'tree',source:{origin:'https://elsewhere.example'}}),options()));
  assert.equal(clipboard.decode(clipboard.encode([block('a','paragraph','portable')],{source:{origin:'https://elsewhere.example'}}),options())[0].content,'portable');
  for(const url of ['javascript:alert(1)','data:text/html,test','https://user:password@example.com/image'])assert.throws(()=>clipboard.decode(clipboard.encode([block('a','image','',{url})]),options()));
});
test('UI-ITEM-CLIPBOARD table text round-trips multiline/quoted cells and empty media has no fabricated URL',()=>{
  const table=mixed()[2],text=ops.text([table],[table.id]);
  assert.deepEqual(require('../notion_static/notion_grid_selection').parseTsv(text),table.props.rows);
  assert.doesNotMatch(clipboard.html([block('a','image')],{source}),/<img|undefined/);
  assert.doesNotMatch(clipboard.html([block('a','image','',{url:'https://user:password@example.com/image'})],{source}),/<img/);
});
