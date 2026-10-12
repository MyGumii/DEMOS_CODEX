"""Text edits through explicit references: dual authority, one original commit."""
import base64,json,os,shutil,subprocess,time
from pathlib import Path

from . import synced_blocks as synced, synced_database_edit, page_move
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _operation_id, _request_sha256, _sha256, _stable_id
from .durable_transport import YJS_UPDATE_ENCODING
from .selected_runtime_contract import runtime_binding_sha256

CONTRACT='code_assist.notion.synced_text_edit.v1'
DELTA_CONTRACT='code_assist.notion.synced_text_delta.v1'
ACTION='collaboration.synced_text_edit'
TEXT_TYPES=frozenset({'text','paragraph','synced_block','heading1','heading2','heading3','heading4','bullet','numbered','todo','toggle','toggle_heading1','toggle_heading2','toggle_heading3','toggle_heading4','callout','quote','code','equation'})
WITNESS_FIELDS={'document_id','workspace_id','workspace_epoch','document_sequence','document_epoch','generation'}


def _updates(value):
    if not isinstance(value,list) or not 1<=len(value)<=100:raise ValueError('one to 100 text edits required')
    result=[];seen=set()
    for update in value:
        if not isinstance(update,dict) or set(update)!={'id','content','rich_text_html'}:raise ValueError('exact text edit fields required')
        identity=synced._exact_id(update['id'],'block_id')
        if identity in seen:raise ValueError('duplicate text edit')
        if not isinstance(update['content'],str) or not (update['rich_text_html'] is None or isinstance(update['rich_text_html'],str)):raise ValueError('text edit strings required')
        seen.add(identity);result.append(dict(update))
    if len(_canonical_json(result).encode('utf-8'))>262144:raise ValueError('text edit exceeds 256 KiB')
    return result


def _witnesses(value):
    if not isinstance(value,list) or not 1<=len(value)<=2:raise ValueError('exact synced witnesses required')
    result={}
    for item in value:
        if not isinstance(item,dict) or set(item)!=WITNESS_FIELDS:raise ValueError('exact synced witness fields required')
        for key in ('document_id','workspace_id'):synced._exact_id(item[key],key)
        if item['document_id'] in result:raise ValueError('duplicate synced witness')
        if any(type(item[k]) is not int or item[k]<1 for k in WITNESS_FIELDS-{'document_id','workspace_id'}):raise ValueError('positive synced witness required')
        result[item['document_id']]=dict(item)
    return result


def _prepare(source,updates):
    node=shutil.which('node')
    if not node:raise AuthorityUnavailable('synced edits require Node.js')
    data={'contract':DELTA_CONTRACT,'document_id':source.document_id,'generation':source.stamp[-1],
        'seed':json.loads(source.descriptor_json),'frames':[base64.b64encode(f).decode('ascii') for f in source.frames],'updates':updates}
    try:
        result=subprocess.run([node,'--max-old-space-size=256',str(Path(__file__).with_name('synced_edit_delta.cjs'))],
            input=_canonical_json(data).encode('utf-8'),capture_output=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        response=json.loads(result.stdout) if result.returncode==0 else {}
        if response.get('contract')!=DELTA_CONTRACT:raise ValueError('invalid prepared edit')
        payload=base64.b64decode(response['update'],validate=True)
        if not 0<len(payload)<=1048576:raise ValueError('invalid prepared edit size')
        return payload
    except (OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired) as exc:
        raise AuthorityUnavailable('synced text edit could not be prepared') from exc


def _append(authority,con,actor,workspace,page,generation,payload,operation,now):
    doc=page['id'];sequence=page['document_sequence']+1
    update_sequence=1+con.execute('SELECT COALESCE(MAX(update_sequence),0) FROM workspace_collaboration_crdt_updates WHERE document_id=?',(doc,)).fetchone()[0]
    update_operation=_stable_id('syncupd',actor,operation)
    identity=_stable_id('cupd',actor,ACTION,operation);checksum=_sha256(payload);binding=runtime_binding_sha256()
    con.execute('UPDATE workspace_collaboration_documents SET document_sequence=?,row_version=row_version+1,updated_at=? WHERE id=?',(sequence,now,doc))
    con.execute('INSERT INTO workspace_collaboration_crdt_updates(id,workspace_id,document_id,update_sequence,document_sequence,actor_subject_id,client_id,session_binding_sha256,operation_id,client_sequence,payload_encoding,payload_bytes,payload_sha256,payload,workspace_epoch,document_epoch,runtime_binding_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identity,workspace['id'],doc,update_sequence,sequence,actor,'synced-text-service',_sha256(CONTRACT+workspace['id']),update_operation,update_sequence,YJS_UPDATE_ENCODING,len(payload),checksum,payload,workspace['security_epoch'],page['access_epoch'],binding,now))
    con.execute("INSERT INTO workspace_collaboration_crdt_update_bindings(update_id,document_id,generation,rich_text_scope,created_at) VALUES(?,?,?,'document_rich_text_leaves',?)",(identity,doc,generation,now))
    return {'document_sequence':sequence,'update_sequence':update_sequence,'update_operation_id':update_operation,'payload_sha256':checksum,'runtime_binding_sha256':binding}


def edit_synced_text(authority,actor_subject_id,workspace_id,document_id,block_id,*,origin,witnesses,updates,operation_id,fault=None):
    actor=authority._require_active(actor_subject_id)
    workspace=synced._exact_id(workspace_id,'workspace_id');document=synced._exact_id(document_id,'document_id');block=synced._exact_id(block_id,'block_id')
    operation=_operation_id(operation_id);clean=_updates(updates);expected=_witnesses(witnesses)
    if not isinstance(origin,dict) or set(origin)!={'document_id','block_id'}:raise ValueError('exact synced original required')
    origin_document=synced._exact_id(origin['document_id'],'document_id');origin_block=synced._exact_id(origin['block_id'],'block_id')
    if set(expected)!={document,origin_document} or expected[document]['workspace_id']!=workspace:raise ValueError('synced witness scope mismatch')
    request_hash=_request_sha256(actor,ACTION,{'workspace_id':workspace,'document_id':document,'block_id':block,'origin':origin,'witnesses':expected,'updates':clean})
    sources,original,tree,actual_document,actual_block=synced._resolve(
        authority,actor,document,block,workspace)
    if (actual_document,actual_block)!=(origin_document,origin_block):raise synced.SyncedSourceChanged('synced original changed')
    descriptors={item.document_id:json.loads(item.descriptor_json) for item in sources}
    locations={}
    for doc,identity in ((document,block),(origin_document,origin_block)):
        locations.setdefault(doc,[]).append(synced.locate_block(descriptors[doc],identity)[0])
    origin_location=synced.locate_block(descriptors[origin_document],origin_block)[0]
    if origin_location.kind=='page' and any(not item.main_structure_verified for item in sources):
        raise synced.SyncedShapeUnavailable('canonical and collaborative block structures differ')
    database_epochs={}

    def authorize(con,*,versions):
        result={}
        for source in sources:
            page,stamp=synced._authorize(authority,con,actor,source.document_id)
            witness=expected[source.document_id]
            if stamp[0]!=witness['workspace_id']:raise NotFoundOrDenied('not found or permission denied')
            epochs=synced.authorize_edit_locations(authority,con,actor,stamp[0],source.document_id,locations[source.document_id])
            if versions and source.document_id in database_epochs and database_epochs[source.document_id]!=epochs:
                raise synced.SyncedSourceChanged('synced database authority changed during preparation')
            if versions:database_epochs[source.document_id]=epochs
            if versions and stamp!=source.stamp:raise synced.SyncedSourceChanged('synced source changed during edit preparation')
            if versions and (stamp[1],stamp[4],stamp[5],stamp[6])!=tuple(witness[k] for k in ('workspace_epoch','document_sequence','document_epoch','generation')):
                raise synced.SyncedSourceChanged('synced edit witness is stale')
            result[source.document_id]=(authority._workspace(con,stamp[0]),page)
        return result

    def replay(con):
        return authority._replay(con,actor=actor,action=ACTION,operation_id=operation,request_sha256=request_hash,authorize=lambda:authorize(con,versions=False))

    with authority._reader() as con:
        con.execute('BEGIN');prior=replay(con)
        if prior is not None:return prior
        authorize(con,versions=True)
    owned={}
    def collect(node):
        owned[node['id']]=node
        # Another reference is a separate authority boundary, even if a corrupt
        # descriptor tries to hide children underneath it.
        if node['type']=='synced_block' and node.get('props',{}).get('synced_from') is not None:return
        for child in node['children']:collect(child)
    collect(tree)
    for update in clean:
        node=owned.get(update['id'])
        if node is None or node['type'] not in TEXT_TYPES:raise synced.SyncedShapeUnavailable('target is not an editable synced text leaf')
        if node['type']=='synced_block' and node.get('props',{}).get('synced_from') is not None:raise synced.SyncedShapeUnavailable('nested references require their own edit boundary')
        if node['type'] in {'code','equation'} and update['rich_text_html'] is not None:raise ValueError('plain text block cannot contain rich HTML')
    source=next(item for item in sources if item.document_id==origin_document)
    body_plan=None
    if origin_location.kind=='page':
        payload=_prepare(source,clean)
    else:
        body_plan=synced_database_edit.prepare(source,origin_block,clean)
        synced_database_edit.validate(body_plan,source,clean)
    with authority._transaction() as con:
        prior=replay(con)
        if prior is not None:return prior
        authorized=authorize(con,versions=True);source_workspace,page=authorized[origin_document]
        if body_plan is not None:
            appended=synced_database_edit.commit(authority,con,actor,page,body_plan,int(time.time()))
            event_operation=_stable_id('syncbodyupd',actor,ACTION,operation)
            result={'contract':CONTRACT,'instance':{'document_id':document,'block_id':block},'origin':dict(origin),'operation_id':operation,
                'changed_block_ids':[item['id'] for item in clean],'workspace_id':source_workspace['id'],'document_id':origin_document,
                'workspace_epoch':source_workspace['security_epoch'],'document_epoch':page['access_epoch'],'generation':source.stamp[-1],
                'update_operation_id':event_operation,**appended}
            event={**result,'operation_id':event_operation}
            return authority._finish(con,actor=actor,action=ACTION,operation_id=operation,request_sha256=request_hash,
                workspace_id=source_workspace['id'],document_id=origin_document,workspace_epoch=source_workspace['security_epoch'],
                document_epoch=page['access_epoch'],document_sequence=appended['document_sequence'],
                event_type='database.schema.committed' if origin_location.kind=='template' else 'database.surface.committed',
                event_payload=event,result=result,fault=fault,event_operation_id=event_operation)
        appended=_append(authority,con,actor,source_workspace,page,source.stamp[-1],payload,operation,int(time.time()))
        result={'contract':CONTRACT,'instance':{'document_id':document,'block_id':block},'origin':dict(origin),'operation_id':operation,'changed_block_ids':[item['id'] for item in clean],
            'workspace_id':source_workspace['id'],'document_id':origin_document,'workspace_epoch':source_workspace['security_epoch'],'document_epoch':page['access_epoch'],'generation':source.stamp[-1],**appended}
        event={**appended,'workspace_id':source_workspace['id'],'page_id':origin_document,'subject_id':actor,'client_id':'synced-text-service','client_sequence':appended['update_sequence'],
            'operation_id':appended['update_operation_id'],'message_type':'crdt.update','payload_encoding':YJS_UPDATE_ENCODING,'workspace_epoch':source_workspace['security_epoch'],'page_epoch':page['access_epoch']}
        return authority._finish(con,actor=actor,action=ACTION,operation_id=operation,request_sha256=request_hash,workspace_id=source_workspace['id'],document_id=origin_document,
            workspace_epoch=source_workspace['security_epoch'],document_epoch=page['access_epoch'],document_sequence=appended['document_sequence'],event_type='crdt.update',event_payload=event,result=result,fault=fault,
            event_operation_id=appended['update_operation_id'])
