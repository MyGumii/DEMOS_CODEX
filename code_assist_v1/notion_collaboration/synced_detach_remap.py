"""Map captured detach resources into one existing destination document.

Resource identities and owned links become independent; ordinary page links
and uncopied source references stay put. Child pages become links to their
existing pages, matching Notion's unsync behavior. No writer is provided.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from urllib.parse import unquote, urlsplit

from . import synced_detach_plan as detach, synced_detach_resources as resources, page_copy
from . import page_copy_remap as copying
from .page_copy_source import CopySourceDocument, PreparedPageCopy, MAX_SOURCE_BYTES
from .canonical_authority import _canonical_json, _sha256, _stable_id
from .synced_tree import content_tree

CONTRACT = 'code_assist.notion.synced_detach_content.v1'
STRUCTURAL = frozenset((*detach.PLACEMENT,'tabs','active_tab_id','synced_from','sync_id'))
TYPED = STRUCTURAL | frozenset({'children','file_id','url','src','rich_text_html','page_id',
    'database_id','linked_database','active_view_id','views','properties','rows','templates','_canonical_version',
    *copying.VIEW_PROPERTIES,'property_visibility','form_required_properties','property_order','group_sort','group_order','filters','filter_group','sorts'})


@dataclass(frozen=True)
class PreparedDetachedContent:
    resources: resources.PreparedSyncedResources
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)


class _DetachRemapper(copying._Remapper):
    def __init__(self, captured):
        self.prepared = captured.detach
        self.initial = self.prepared.value()
        self.origin_document = self.prepared.origin[0]
        data = captured.value()
        source = next(s for s in self.prepared.sources if s.document_id == self.origin_document)
        self.original_tree = content_tree(detach.source_body(source, self.prepared.origin[1])[1], self.prepared.origin[1])
        flat = []
        def visit(node):
            if not (self.initial['unwrap_empty_root'] and node['id'] == self.prepared.origin[1]):
                block = {k:deepcopy(node[k]) for k in ('id','type','content','props')}
                if node['id'] == self.prepared.origin[1]:
                    block['type'] = 'paragraph'
                    block['props'].pop('synced_from',None);block['props'].pop('sync_id',None)
                flat.append(block)
            for child in node['children']:visit(child)
        visit(self.original_tree)
        descriptor = {'title':'','icon':'','cover':'','settings':{},'blocks':flat,'databases':data['databases'],'files':[]}
        document = CopySourceDocument(self.origin_document,None,0,source.stamp,_canonical_json(descriptor),_canonical_json(data['files']))
        synthetic = PreparedPageCopy(self.prepared.actor,self.prepared.workspace_id,self.origin_document,(),(document,),None,None)
        super().__init__(synthetic,self.prepared.operation_id,self.initial['application_origin'],partial_documents=True)
        # The removed outer wrapper remains a valid link alias to its promoted
        # first child even though it is absent from the returned body.
        self.ids[('block',self.origin_document,self.prepared.origin[1])] = self.prepared.block_id

    def allocate(self, kind, scope, old):
        if not isinstance(old,str) or not old:
            raise copying.CopyShapeUnavailable('detach resource identity is missing')
        key = (kind,scope,old)
        p = self.prepared
        if kind == 'p' and old == self.origin_document:
            identity = p.document_id
        elif kind == 'block' and scope == self.origin_document and old in self.initial['block_ids']:
            identity = self.initial['block_ids'][old]
        elif kind in {'db','file'}:
            identity = _stable_id(kind,detach.CONTRACT,p.actor,p.workspace_id,p.document_id,p.block_id,p.operation_id,self.origin_document,old)
        else:
            identity = _stable_id(kind,CONTRACT,p.actor,p.workspace_id,p.document_id,p.block_id,p.operation_id,scope,old)
        self.ids.setdefault(key,identity)
        return self.ids[key]

    def url(self, value, scope):
        if not isinstance(value,str) or not value:return value
        try:parts = urlsplit(value)
        except ValueError:return value
        if (parts.scheme or parts.netloc) and self.origin != (parts.scheme.lower(),parts.netloc.lower()):
            return value
        # A whole-page copy moves page links; detach must preserve them. The
        # base remapper still owns exact database-item and file URL semantics.
        if parts.path.startswith('/page/') and unquote(parts.path[6:]) == self.origin_document:
            keys = {unquote(part.partition('=')[0]) for part in parts.query.split('&')}
            item = bool(keys & {'item_db','item_row'})
            owned = parts.fragment.startswith('block-') and unquote(parts.fragment[6:]) in self.initial['block_ids']
            if not item and not owned:return value
        return super().url(value,scope)

    def extra(self, value, scope):
        if isinstance(value,list):return [self.extra(item,scope) for item in value]
        if not isinstance(value,dict):return deepcopy(value)
        result = {}
        for key,item in value.items():
            if key == 'file_id':result[key] = self.ref('file','',item)
            elif key in {'url','src','cover'}:result[key] = self.url(item,scope)
            elif key == 'icon':result[key] = self.image(item,scope,icon=True)
            elif key == 'rich_text_html' and isinstance(item,str):result[key] = copying._RichLinks(item,lambda value:self.url(value,scope)).result()
            else:result[key] = self.extra(item,scope)
        return result

    def blocks(self, blocks, scope):
        result = super().blocks(blocks,scope)
        for original, mapped in zip(blocks,result):
            props = original.get('props',{})
            if original['type'] in {'child_page','link_to_page'} and 'page_id' in props:
                mapped['props']['page_id'] = props['page_id']
                if original['type'] == 'child_page':
                    # A child page cannot acquire a second parent through
                    # unsync. Notion leaves an ordinary link to the same page.
                    mapped['type'] = 'link_to_page'
            if 'linked_database' in props:
                source = props['linked_database']
                if source['database_id'] not in self.databases:
                    mapped['props']['linked_database'] = deepcopy(source)
                    if 'database_id' in props:mapped['props']['database_id'] = props['database_id']
            for key in props:
                if key not in TYPED and key in mapped['props']:
                    mapped['props'][key] = self.extra({key:props[key]},scope)[key]
        return result


def remap_synced_resources(captured):
    if _sha256(captured.payload_json) != captured.payload_sha256 or _sha256(captured.detach.plan_json) != captured.detach.plan_sha256:
        raise ValueError('detach preparation was modified')
    mapper = _DetachRemapper(captured)
    # Reuse canonical validation, including row-body/appearance limits, formula
    # aliases, view settings and initial versions. This creates no page.
    copied = mapper.build()
    document = copied.documents[0]
    descriptor = page_copy._contents(copied)[document.document_id]
    mapped = {block['id']:block for block in descriptor['blocks']}
    def forest(node):
        block = deepcopy(mapped[node['id']])
        block.pop('position',None)
        # Placement and nested sync endpoints were resolved by the owned-tree
        # planner, not by whole-page copy identity rules.
        for key in STRUCTURAL:
            if key in node['props']:block['props'][key] = deepcopy(node['props'][key])
            else:block['props'].pop(key,None)
        block['children'] = [forest(child) for child in node['children']]
        return block
    initial = mapper.initial
    originals = {}
    def index(node):
        originals[node['id']] = node
        for child in node['children']:index(child)
    index(mapper.original_tree)
    reference_plan = []
    for item in captured.value()['references']:
        destination, status = deepcopy(item), 'preserved'
        if item['kind'] == 'child_page':
            destination, status = {'kind':'page_link','document_id':item['document_id']}, 'linked_existing_page'
        elif item['kind'] == 'linked_database' and item['document_id'] == mapper.origin_document and item['database_id'] in mapper.databases:
            destination.update(document_id=captured.detach.document_id,database_id=mapper.ref('db','',item['database_id']))
            status = 'internal'
        elif item['kind'] == 'synced_reference' and item['document_id'] == mapper.origin_document:
            target = originals.get(item['block_id'])
            if target and target['id'] != captured.detach.origin[1] and target['type'] == 'synced_block' and target['props'].get('synced_from',False) is None:
                destination.update(document_id=captured.detach.document_id,block_id=initial['block_ids'][target['id']])
                status = 'internal'
        reference_plan.append({'source':item,'destination':destination,'status':status})
    wrapper = None
    if initial['unwrap_empty_root']:
        # Always rewrite source properties once. A pre-rewritten same-page
        # anchor can also be a source block ID and must not be rewritten twice.
        wrapper = mapper.extra({k:v for k,v in mapper.original_tree['props'].items()
            if k not in {'synced_from','sync_id','children'}},mapper.origin_document)
    value = {'contract':CONTRACT,'commit_ready':False,
        'blocks':[forest(block) for block in initial['blocks']],
        'block_ids':initial['block_ids'],'databases':descriptor['databases'],
        'files':json.loads(document.files_json),'reference_plan':reference_plan,
        'source_placement':initial['source_placement'],'destination_placement':initial['destination_placement'],
        'unwrap_empty_root':initial['unwrap_empty_root'],
        'wrapper_props':wrapper}
    encoded = _canonical_json(value)
    if len(encoded.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise ValueError('detach content exceeds preparation budget')
    return PreparedDetachedContent(captured,encoded,_sha256(encoded))


def revalidate_detached_content(authority,con,prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached content was modified')
    resources.revalidate_synced_resources(authority,con,prepared.resources)


def prepare_detached_content(authority,captured):
    result = remap_synced_resources(captured)
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_detached_content(authority,con,result)
    return result
