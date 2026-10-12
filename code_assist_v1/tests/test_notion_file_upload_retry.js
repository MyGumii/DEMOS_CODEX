const test = require('node:test');
const assert = require('node:assert/strict');
const {upload} = require('../notion_static/notion_file_upload.js');

test('rejected source fence starts a fresh upload session on retry', async () => {
  const entries = new Map();
  const storage = {
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key),
  };
  const file = Object.assign(new Blob([new Uint8Array([1, 2, 3])], {type: 'image/png'}), {
    name: 'template.png', lastModified: 1,
  });
  let begins = 0;
  const response = (data, status = 200) => ({
    ok: status < 400, status, json: async () => data,
  });
  const fetch = async (url, init = {}) => {
    if (url.endsWith('/uploads') && init.method === 'POST') {
      begins += 1;
      return response({session_id: `upload-${begins}`, chunk_size: 1024});
    }
    if (url.includes('/uploads/upload-') && init.method === 'GET') {
      return response({missing_chunks: [0]});
    }
    if (url.endsWith('/chunks/0')) return response({});
    if (url.endsWith('/finalize')) {
      return begins === 1
        ? response({message: 'upload source fence changed'}, 409)
        : response({file: {id: 'uploaded-file'}});
    }
    throw Error(`Unexpected upload request: ${init.method} ${url}`);
  };
  const options = {file, baseUrl: '/api/notion/pages/page/files',
    subjectId: 'owner', workspaceId: 'workspace', pageId: 'page',
    documentSequence: 1, workspaceEpoch: 1, documentEpoch: 1,
    storage, fetch};
  await assert.rejects(upload(options), error => error.status === 409);
  assert.equal(entries.size, 0);
  const result = await upload({...options, documentSequence: 2});
  assert.equal(result.file.id, 'uploaded-file');
  assert.equal(begins, 2);
});

test('database upload carries its own access epoch and storage identity', async () => {
  const entries = new Map(),requests=[];
  const storage = {
    getItem:key=>entries.get(key)??null,
    setItem:(key,value)=>{assert.ok(key.includes('database%3Adb-only'));entries.set(key,value);},
    removeItem:key=>entries.delete(key),
  };
  const file=Object.assign(new Blob([new Uint8Array([4,5,6])],{type:'image/png'}),
    {name:'shared.png',lastModified:2});
  const response=data=>({ok:true,status:200,json:async()=>data});
  const fetch=async(url,init={})=>{
    requests.push({url,method:init.method,body:init.body});
    if(url.endsWith('/uploads')&&init.method==='POST'){
      assert.equal(JSON.parse(init.body).expected_database_epoch,7);
      assert.equal(JSON.parse(init.body).expected_document_sequence,9);
      return response({session_id:'db-session',database_id:'db-only',chunk_size:1048576});
    }
    if(url.endsWith('/uploads/db-session')&&init.method==='GET')
      return response({database_id:'db-only',state:'prepared',missing_chunks:[0]});
    if(url.endsWith('/chunks/0'))return response({state:'ready'});
    if(url.endsWith('/finalize'))return response({file:{id:'file-one',database_id:'db-only'}});
    throw Error(`Unexpected upload request: ${init.method} ${url}`);
  };
  const result=await upload({file,baseUrl:'/api/notion/workspaces/ws/databases/db-only/files',
    subjectId:'viewer',workspaceId:'ws',databaseId:'db-only',
    documentSequence:9,workspaceEpoch:3,documentEpoch:5,databaseEpoch:7,
    storage,fetch});
  assert.equal(result.file.id,'file-one');
  assert.ok(requests.every(item=>item.url.includes('/databases/db-only/files/')));
  assert.equal(entries.size,0);
});
