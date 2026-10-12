(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./notion_editor_core'):root.NotionEditorCore);if(typeof module==='object'&&module.exports)module.exports=api;else root.NotionBlockClipboard=api;})(typeof globalThis==='object'?globalThis:this,function(core){
  'use strict';
  const MIME='application/x-notion-local-blocks+json',MAX_BYTES=2000000,MAX_BLOCKS=1000,MAX_DEPTH=32;
  const copy=value=>structuredClone(value),object=value=>value&&typeof value==='object'&&!Array.isArray(value);
  const bytes=value=>new TextEncoder().encode(value).length;
  const offset=value=>Number.isFinite(Number(value))?Math.max(0,Math.min(8,Number(value))):0;
  const escape=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  function check(blocks,allowed){
    if(!Array.isArray(blocks)||!blocks.length)throw Error('복사한 블록이 없습니다.');
    let count=0;const ids=new Set();
    function props(value,depth=0){
      if(depth>64)throw Error('블록 속성의 중첩이 너무 깊습니다.');
      if(value&&typeof value==='object')for(const [key,child] of Object.entries(value)){
        if(['__proto__','prototype','constructor'].includes(key))throw Error('지원하지 않는 블록 속성입니다.');
        props(child,depth+1);
      }
    }
    function walk(list,depth){
      if(depth>MAX_DEPTH)throw Error('블록의 중첩이 너무 깊습니다.');
      for(const block of list){
        if(++count>MAX_BLOCKS)throw Error('블록이 너무 많습니다. 나누어 복사하세요.');
        if(!object(block)||typeof block.id!=='string'||!block.id||ids.has(block.id)||typeof block.type!=='string'||allowed&&!allowed.has(block.type)||typeof block.content!=='string'||block.props!==undefined&&!object(block.props))throw Error('이 화면에서 지원하지 않는 블록 복사 형식입니다.');
        ids.add(block.id);props(block.props||{});
        if(block.props?.children!==undefined){if(!Array.isArray(block.props.children))throw Error('하위 블록 형식이 올바르지 않습니다.');walk(block.props.children,depth+1);}
      }
    }
    walk(blocks,0);return ids;
  }
  function encode(blocks,{shape='flat',source={}}={}){
    if(!['flat','tree'].includes(shape))throw Error('블록 구조가 올바르지 않습니다.');
    if(shape==='flat'){blocks=tree(blocks);shape='tree';}
    check(blocks);
    const encoded=JSON.stringify({version:2,shape,source,blocks});
    if(bytes(encoded)>MAX_BYTES)throw Error('블록이 너무 큽니다. 나누어 복사하세요.');return encoded;
  }
  function tree(blocks){
    const outline=core.blockOutline(blocks),roots=outline.map((e,i)=>e.parentIndex<0?i:-1).filter(i=>i>=0);
    const base=Math.min(...roots.map(i=>outline[i].structuralIndent));
    function build(index){
      const block=copy(blocks[index]),entry=outline[index],parent=outline[entry.parentIndex];block.props??={};
      const step=blocks[entry.parentIndex]?.type==='tabs'||core.isLayoutContainer(blocks[entry.parentIndex])?0:1;
      block.props.indent=(parent?Math.max(0,entry.structuralIndent-parent.structuralIndent-step):entry.structuralIndent-base)+offset(block.props.visual_indent);
      delete block.props.visual_indent;
      const inline=Array.isArray(block.props.children)&&block.props.children.every(child=>object(child))?block.props.children:[];
      const children=inline.concat(entry.childIndices.map(build));
      if(children.length)block.props.children=children;else delete block.props.children;
      return block;
    }
    return roots.map(build);
  }
  function flat(blocks){
    const result=[],parents=new Map(),childrenById=new Map();
    function walk(list,depth,parent=null){for(const original of list){
      const block=copy(original),children=block.props?.children||[];block.props??={};const extra=offset(block.props.indent);
      // Item children express ancestry; item indent is only a local visual gap.
      // Keeping both in one flat indent silently reparents independent siblings.
      block.props.indent=depth;if(extra)block.props.visual_indent=extra;else delete block.props.visual_indent;
      parents.set(block.id,parent);childrenById.set(block.id,children.map(child=>child.id));
      delete block.props.children;
      result.push(block);walk(children,depth+(block.type==='tabs'||core.isLayoutContainer(original)?0:1),block.id);
    }}
    walk(blocks,0);
    // Keep the ordinary indentation-only wire shape when it is sufficient.
    // Cross-scope children need explicit edges to survive columns and tabs.
    const outline=core.blockOutline(result),byId=new Map(result.map(block=>[block.id,block]));
    result.forEach((block,index)=>{
      const parent=parents.get(block.id);
      if(parent!==(result[outline[index].parentIndex]?.id||null)&&parent!==null)byId.get(parent).props.children=childrenById.get(parent).slice();
    });
    return result;
  }
  function decode(encoded,{shape='flat',allowedTypes,createId,sanitize=value=>value,usedIds=[],origin=''}={}){
    if(typeof encoded!=='string'||bytes(encoded)>MAX_BYTES)throw Error('블록이 너무 큽니다. 나누어 복사하세요.');
    let data;try{data=JSON.parse(encoded);}catch{throw Error('블록 복사 형식을 읽지 못했습니다.');}
    if(!object(data)||![1,2].includes(data.version)||data.version===2&&!['flat','tree'].includes(data.shape))throw Error('지원하지 않는 블록 복사 형식입니다.');
    const sourceShape=data.version===1?'flat':data.shape;
    if(sourceShape==='flat')data.blocks=tree(data.blocks);
    const originalIds=check(data.blocks,allowedTypes),ids=new Map(),layouts=new Map(),used=new Set([...usedIds,...originalIds]);
    const fresh=()=>{for(let i=0;i<100;i++){const id=createId();if(typeof id==='string'&&id&&!used.has(id)){used.add(id);return id;}}throw Error('새 블록 번호를 만들지 못했습니다.');};
    for(const id of originalIds)ids.set(id,fresh());
    function rekey(list){return list.map(original=>{
      const block=copy(original);block.id=ids.get(original.id);block.props??={};
      if(block.props.file_id&&data.source?.origin&&origin&&data.source.origin!==origin)throw Error('다른 서비스의 첨부파일은 파일을 다시 올려 주세요.');
      if(['image','file','pdf','audio','video','bookmark','embed'].includes(block.type)&&block.props.url){
        let link;try{link=new URL(block.props.url);}catch{}
        if(!link||!['http:','https:'].includes(link.protocol)||link.username||link.password||/[\u0000-\u0020\\]/.test(block.props.url))throw Error('복사한 자료에 지원하지 않는 주소가 있습니다.');
      }
      if(typeof block.props.rich_text_html==='string')block.props.rich_text_html=sanitize(block.props.rich_text_html);
      if(block.props.layout_group){const key=block.props.layout_group;if(!layouts.has(key))layouts.set(key,fresh());block.props.layout_group=layouts.get(key);}
      if(block.props.tab_group){if(ids.has(block.props.tab_group))block.props.tab_group=ids.get(block.props.tab_group);else{delete block.props.tab_group;delete block.props.tab_id;}}
      if(Array.isArray(block.props.children))block.props.children=rekey(block.props.children);
      return block;
    });}
    let blocks=rekey(data.blocks);
    if(shape==='flat')blocks=flat(blocks);
    const positions=list=>list.forEach((block,index)=>{block.position=index;if(shape==='tree'&&block.props.children)positions(block.props.children);});positions(blocks);
    return blocks;
  }
  function read(clipboard){
    const encoded=clipboard?.getData(MIME);if(encoded)return encoded;
    const html=clipboard?.getData('text/html');if(!html||html.length>MAX_BYTES*2||!html.includes('data-notion-local-blocks='))return '';
    const template=document.createElement('template');template.innerHTML=html;
    return template.content.querySelector('[data-notion-local-blocks]')?.getAttribute('data-notion-local-blocks')||'';
  }
  function html(blocks,{shape='tree',sanitize=value=>escape(value),fileUrl=id=>'',source={}}={}){
    const roots=shape==='flat'?tree(blocks):blocks;
    const safeUrl=value=>{if(typeof value!=='string'||!value.trim())return '';try{const parsed=new URL(value,source.origin);return ['http:','https:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password?parsed.href:'';}catch{return '';}};
    function blockHtml(block){
      const p=block.props||{},content=p.rich_text_html?sanitize(p.rich_text_html):escape(block.content).replaceAll('\n','<br>'),children=(p.children||[]).map(blockHtml).join('');
      if(core.isLayoutContainer(block))return children;
      if(block.type==='simple_table')return '<table>'+((p.rows||[]).map(row=>'<tr>'+row.map(cell=>'<td>'+escape(cell).replaceAll('\n','<br>')+'</td>').join('')+'</tr>').join(''))+'</table>'+children;
      if(block.type==='divider')return '<hr>'+children;
      if(/^heading[123]$/.test(block.type))return `<h${block.type.at(-1)}>${content}</h${block.type.at(-1)}>`+children;
      if(block.type.startsWith('toggle'))return `<details ${p.open===false?'':'open'}><summary>${content}</summary>${children}</details>`;
      if(block.type==='bullet'||block.type==='numbered'){const tag=block.type==='bullet'?'ul':'ol';return `<${tag}><li>${content}${children}</li></${tag}>`;}
      if(block.type==='todo')return `<div>${p.checked?'☑':'☐'} ${content}${children}</div>`;
      if(block.type==='code')return `<pre><code>${escape(block.content)}</code></pre>`+children;
      if(block.type==='quote'||block.type==='callout')return `<blockquote>${block.type==='callout'?escape(p.icon||'💡')+' ':''}${content}${children}</blockquote>`;
      if(['image','file','pdf','audio','video','bookmark','embed'].includes(block.type)){
        const url=safeUrl(p.file_id?fileUrl(p.file_id):p.url),media=url?(block.type==='image'?`<img src="${escape(url)}" alt="${escape(block.content)}">`:`<a href="${escape(url)}">${escape(p.name||p.url||block.type)}</a>`):'';
        return `<figure>${media}<figcaption>${content}</figcaption>${children}</figure>`;
      }
      return `<p>${content}</p>`+children;
    }
    return roots.map(blockHtml).join('');
  }
  function write(clipboard,blocks,options={}){
    if(!clipboard)throw Error('클립보드를 사용할 수 없습니다.');
    const encoded=encode(blocks,options),rich=html(blocks,options);
    clipboard.setData(MIME,encoded);
    clipboard.setData('text/html',`<div data-notion-local-blocks="${escape(encoded)}">${rich}</div>`);
    clipboard.setData('text/plain',options.text??blocks.map(b=>b.content).join('\n'));
  }
  return {MIME,encode,decode,read,write,html,tree,flat};
});
