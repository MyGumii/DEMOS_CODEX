"""Forward canonical layout writer: one authoring stage, exact source replay.

Tables are literal native cells. Visual readings are model interpretations,
never verified measurements. Historical writing policies remain unchanged.
"""
from dataclasses import asdict,fields,replace
from hashlib import sha256
from html import escape
import hmac
import json
import secrets
from types import SimpleNamespace

from . import pdf_layout_wiki as writer
from .pdf_layout_source import checked_layout
from .pdf_section_writing import supported_markdown, quantity_diagnostics
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError, _hash, _json
from ..llm_images import prepare_image_messages, validate_image_inputs
from ..llm_wire import chat_payload, encode_chat_payload

VERSION = 'wiki-pdf-layout-retention-v1'
DECODER_VERSION = 'pdf-layout-retention-v1'
LEDGER_VERSION = 'pdf-layout-retention-ledger-v1'
PROMPT = writer.STRUCTURED_PROMPT_V5
_PLAN_SEAL_KEY=secrets.token_bytes(32)


def _contract(plan):
    if plan.policy_version == 'wiki-pdf-layout-retention-v21':
        from . import pdf_layout_retention_v21
        return pdf_layout_retention_v21
    if plan.policy_version == 'wiki-pdf-layout-retention-v20':
        from . import pdf_layout_retention_v20
        return pdf_layout_retention_v20
    if plan.policy_version == 'wiki-pdf-layout-retention-v19':
        from . import pdf_layout_retention_v19
        return pdf_layout_retention_v19
    if plan.policy_version == 'wiki-pdf-layout-retention-v18':
        from . import pdf_layout_retention_v18
        return pdf_layout_retention_v18
    if plan.policy_version == 'wiki-pdf-layout-retention-v17':
        from . import pdf_layout_retention_v17
        return pdf_layout_retention_v17
    if plan.policy_version == 'wiki-pdf-layout-retention-v16':
        from . import pdf_layout_retention_v16
        return pdf_layout_retention_v16
    if plan.policy_version == 'wiki-pdf-layout-retention-v15':
        from . import pdf_layout_retention_v15
        return pdf_layout_retention_v15
    if plan.policy_version == 'wiki-pdf-layout-retention-v14':
        from . import pdf_layout_retention_v14
        return pdf_layout_retention_v14
    if plan.policy_version == 'wiki-pdf-layout-retention-v13':
        from . import pdf_layout_retention_v13
        return pdf_layout_retention_v13
    if plan.policy_version == 'wiki-pdf-layout-retention-v12':
        from . import pdf_layout_retention_v12
        return pdf_layout_retention_v12
    if plan.policy_version == 'wiki-pdf-layout-retention-v11':
        from . import pdf_layout_retention_v11
        return pdf_layout_retention_v11
    if plan.policy_version == 'wiki-pdf-layout-retention-v10':
        from . import pdf_layout_retention_v10
        return pdf_layout_retention_v10
    if plan.policy_version == 'wiki-pdf-layout-retention-v9':
        from . import pdf_layout_retention_v9
        return pdf_layout_retention_v9
    if plan.policy_version == 'wiki-pdf-layout-retention-v8':
        from . import pdf_layout_retention_v8
        return pdf_layout_retention_v8
    if plan.policy_version == 'wiki-pdf-layout-retention-v7':
        from . import pdf_layout_retention_v7
        return pdf_layout_retention_v7
    if plan.policy_version == 'wiki-pdf-layout-retention-v6':
        from . import pdf_layout_retention_v6
        return pdf_layout_retention_v6
    if plan.policy_version == 'wiki-pdf-layout-retention-v5':
        from . import pdf_layout_retention_v5
        return pdf_layout_retention_v5
    if plan.policy_version == 'wiki-pdf-layout-retention-v4':
        from . import pdf_layout_retention_v4
        return pdf_layout_retention_v4
    if plan.policy_version == 'wiki-pdf-layout-retention-v3':
        from . import pdf_layout_retention_v3
        return pdf_layout_retention_v3
    if plan.policy_version == 'wiki-pdf-layout-retention-v2':
        from . import pdf_layout_retention_v2
        return pdf_layout_retention_v2
    return SimpleNamespace(VERSION=VERSION, DECODER_VERSION=DECODER_VERSION)


def _admitted_plan_seal(plan):
    """Fresh bounded typed hash; never rerender, recrop or reconstruct a plan.

    All declared fields, including mutable caches/nested jobs/source pixels,
    are bound. Only this self-referential seal field is excluded. The shared
    typed hasher's memo is local to each invocation, not a trusted mutable
    identity cache. Import lazily to avoid a service/plan import cycle.
    """
    from .services.pdf_authoring_source import _typed_plan_digest
    projection={f.name:getattr(plan,f.name) for f in fields(plan) if f.name!='_layout_validation_seal'}
    digest=_typed_plan_digest(projection,PROMPT,expected_type=dict,deduplicate=True,
        maximum_hashed_bytes=335_544_320,maximum_text_bytes=268_435_456)
    record={'version':_contract(plan).VERSION,'plan_type':type(plan).__module__+'.'+type(plan).__qualname__,
            'private_fields_sha256':digest}
    return hmac.digest(_PLAN_SEAL_KEY,_json(record).encode(),'sha256').hex()


def seal_admitted_plan(plan):
    """Creation-only attestation after complete admission, never on first use."""
    from .pdf_visual_authoring import PdfVisualPlan,VERSION_V12,_require
    _require(type(plan) is PdfVisualPlan and plan.policy_version in (VERSION_V12,'wiki-pdf-layout-retention-v2','wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21')
        and plan._layout_validation_seal=='','A fresh forward plan is required.')
    object.__setattr__(plan,'_layout_validation_seal',_admitted_plan_seal(plan))


def checked_admitted_plan(plan):
    """Return the unchanged, already admitted plan only after fresh validation."""
    from .pdf_visual_authoring import PdfVisualPlan,VERSION_V12,_require
    _require(type(plan) is PdfVisualPlan and plan.policy_version in (VERSION_V12,'wiki-pdf-layout-retention-v2','wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'),
        'A forward visual PDF plan is required.')
    try:
        seal=plan._layout_validation_seal
        layout=plan.layout_source
        forward=plan._layout_plan
        aliases=(layout.source is plan.source and forward.layout is layout
            and forward.figure_rois is plan.figure_rois
            and (plan.figure_rois is None or all(roi.layout is layout for roi in plan.figure_rois))
            and forward.base.source is plan.source
            and (layout.native_figures is None or layout.native_figures.source is plan.source)
            and (getattr(forward,'context_base',None) is None or forward.context_base.source is plan.source))
        valid=aliases and type(seal) is str and len(seal)==64 and hmac.compare_digest(seal,_admitted_plan_seal(plan))
    except (ValueError,TypeError,AttributeError,UnicodeError,RecursionError):
        valid=False
    _require(valid,'Forward visual PDF plan fields or admitted source changed.')
    return plan


def initialize(plan):
    from .pdf_visual_authoring import PdfVisualPlan, VERSION_V10, VisualBatch, _require
    _require(plan.layout_source is not None and plan.layout_source.source is plan.source,
             'Forward layout writing requires the same sealed source.')
    checked_layout(plan.layout_source)
    # V22 exposes an8192 ceiling but preserves the existing4096 source-job
    # planning reservation. Actual job-local caps are independently admitted.
    base_model = (replace(plan.model_policy, max_tokens=4096)
        if plan.policy_version in ('wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else plan.model_policy)
    bounded_text = plan.policy_version in ('wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18')
    contract = _contract(plan)
    configured = plan.chunk_bytes
    effective = min(configured, contract.TEXT_CHUNK_BYTES) if bounded_text else configured
    base = PdfVisualPlan(plan.source, effective, base_model, plan.work_policy,
                         plan.execution_policy, policy_version=VERSION_V10,
                         text_partition_policy=contract.TEXT_PARTITION_VERSION if bounded_text else None)
    context_base = (PdfVisualPlan(plan.source, configured, base_model, plan.work_policy,
        plan.execution_policy, policy_version=VERSION_V10) if bounded_text else None)
    if plan.policy_version in ('wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        from .pdf_figure_roi_source import figure_roi_group_identity
        if type(plan.figure_rois) is not tuple:
            raise AuthoringIncompleteError('Forward ROI writing requires an explicit prepared tuple.')
        figure_roi_group_identity(plan.layout_source, plan.figure_rois)
    forward = writer.PdfLayoutWikiPlan(base, plan.layout_source, structured=True,
        figure_rois=plan.figure_rois, context_base=context_base,
        **({'native_compiler_prompt':contract.NATIVE_PROMPT_VERSION}
            if plan.policy_version == 'wiki-pdf-layout-retention-v21' else {}),
        packing_policy=(contract.TEXT_PARTITION_VERSION
            if plan.policy_version in ('wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else None),
        owned_chunk_limit=1 if plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18') else None)
    for name in ('chunks','_doc','_catalog','_tables','_outline','_topic_outline'):
        object.__setattr__(plan,name,getattr(base,name))
    object.__setattr__(plan,'_layout_plan',forward)
    contract = _contract(plan)
    descriptor = {'version':contract.VERSION,'decoder_version':contract.DECODER_VERSION,'prompt_sha256':_hash(PROMPT),
        'base_plan_sha256':base.plan_sha256,'layout_plan_sha256':forward.plan_sha256,
        'source':plan.source.identity(),'layout':plan.layout_source.identity(),
        'image_directive_order':'before_source_images',
        'model_policy':asdict(plan.model_policy),'work_policy':asdict(plan.work_policy),
        'execution_policy':asdict(plan.execution_policy)}
    if plan.policy_version in ('wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['figure_roi_inputs'] = figure_roi_group_identity(plan.layout_source, plan.figure_rois)
    if plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['curve_domain_version'] = contract.CURVE_DOMAIN_VERSION
    if plan.policy_version in ('wiki-pdf-layout-retention-v2','wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        from .pdf_layout_math_format import VERSION as math_format_version
        descriptor['math_format_version'] = math_format_version
        descriptor['image_scope_version'] = contract.IMAGE_SCOPE_VERSION
        descriptor['term_context_version'] = contract.TERM_CONTEXT_VERSION
        descriptor['supplemental_budget_version'] = contract.SUPPLEMENTAL_BUDGET_VERSION
        if plan.policy_version in ('wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
            descriptor['literal_appendix_version'] = contract.LITERAL_APPENDIX_VERSION
        if plan.policy_version in ('wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
            descriptor['subject_binding_version'] = contract.SUBJECT_BINDING_VERSION
            descriptor['subject_bindings_sha256'] = _hash(_json([
                contract.checked_subject_bindings(json.loads(job.scope_json), job.images)
                for job in forward.jobs]))
    if plan.policy_version in ('wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['authoring_request_mode_version'] = contract.REQUEST_MODE_VERSION
        descriptor['authoring_request_mode'] = contract.request_options(plan)
    if plan.policy_version in ('wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['output_token_allocation'] = {
            'version': contract.ALLOCATION_VERSION,
            'planning_floor': contract.OUTPUT_FLOOR,
            'output_ceiling': contract.OUTPUT_CEILING,
            'job_caps': [contract.output_tokens(plan, json.loads(job.scope_json), job)
                for job in forward.jobs]}
    if bounded_text:
        descriptor['text_partition'] = contract.text_partition(plan)
    elif plan.policy_version in ('wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['text_partition'] = contract.text_partition(plan)
        descriptor['text_response_contract'] = contract.text_response_contract_identity()
    if plan.policy_version in ('wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['native_prompt'] = contract.native_prompt_identity()
    if plan.policy_version in ('wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['structured_prompt'] = contract.structured_prompt_identity()
    if plan.policy_version in ('wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        descriptor['text_prompt'] = contract.text_prompt_identity()
    object.__setattr__(plan,'plan_sha256',_hash(_json(descriptor)))
    batches=[]
    for ordinal,job in enumerate(forward.jobs,1):
        pages=sorted({base.chunks[n-1].page for n in job.chunk_ordinals}|set(job.visual_pages))
        identity=[plan.plan_sha256,ordinal,job.key,job.request_sha256]
        batches.append(VisualBatch(ordinal,pages[0],job.chunk_ordinals,bool(job.visual_pages),
            'PVB_'+_hash(_json(identity)),visual_pages=job.visual_pages))
    object.__setattr__(plan,'batches',tuple(batches))
    # The constructor above admitted the complete forward plan. During this
    # creation-only stage, preflight its exact jobs directly; no reusable
    # capability is exposed until every reservation passes and we seal it.
    reservations=[_request_for_job(plan,b,forward.jobs[b.ordinal-1])[3] for b in batches]
    total=sum(r['wire_bytes']+r['image_bytes'] for r in reservations)
    _require(total<=plan.work_policy.logical_input_bytes,
             'Complete layout wire work exceeds its budget.',AuthoringWorkLimitError)
    object.__setattr__(plan,'reservation_json',_json({'model_attempts':len(batches),
        'wire_plus_image_bytes':total,'response_bytes':len(batches)*65536,
        'batch_reservations':reservations,'layout_identity':plan.layout_source.identity()}))
    seal_admitted_plan(plan)


def job_for(plan,batch):
    # The fresh typed seal covers the entire nested forward/base plan too.
    # Reconstructing the historical base for every image/request/receipt would
    # repeat extraction and cropping even though no input changed.
    checked_admitted_plan(plan)
    if type(batch.ordinal) is not int or not 1<=batch.ordinal<=len(plan.batches) or plan.batches[batch.ordinal-1]!=batch:
        raise AuthoringIncompleteError('Unknown forward writing batch.')
    return plan._layout_plan.jobs[batch.ordinal-1]


def images(plan,batch):
    return job_for(plan,batch).images


def response_format(scope):
    return writer._response_format(scope)


def request(plan,batch):
    return _request_for_job(plan,batch,job_for(plan,batch))


def _checked_batch_rows(plan):
    """Compile the complete V24/V25/V26/V27/V28 inventory between fresh plan checks.

    This synchronous private path accepts no selected job, callback or cached
    authority. It returns only canonical primitive rows, never live payloads,
    images or jobs. Public selected-request/replay checks remain unchanged.
    """
    from .pdf_visual_authoring import PdfVisualPlan, VisualBatch, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, _require
    from .pdf_layout_wiki import LayoutWikiJob
    _require(type(plan) is PdfVisualPlan and plan.policy_version in (VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32),
        'Bulk inventory requires the exact bounded text writing policy.')
    checked_admitted_plan(plan)
    entry_seal=plan._layout_validation_seal
    batches,jobs=plan.batches,plan._layout_plan.jobs
    _require(type(batches) is tuple and type(jobs) is tuple and 0<len(batches)==len(jobs),
        'Complete forward writing batches and jobs are required.')
    rows=[]
    for ordinal,(batch,job) in enumerate(zip(batches,jobs,strict=True),1):
        _require(type(batch) is VisualBatch and type(job) is LayoutWikiJob and type(batch.ordinal) is int
            and batch.ordinal==ordinal and type(batch.page) is int and batch.page>0
            and type(batch.visual) is bool and type(batch.batch_id) is str
            and type(batch.chunk_ordinals) is tuple and batch.chunk_ordinals==job.chunk_ordinals
            and batch.visual_pages==job.visual_pages
            and all(type(i) is int and 1<=i<=len(plan.chunks) for i in batch.chunk_ordinals),
            'The complete forward writing roster differs.')
        _,_,digest,reserved=_request_for_job(plan,batch,job)
        wire_bytes,image_bytes=reserved['wire_bytes'],reserved['image_bytes']
        _require(type(wire_bytes) is int and 0<wire_bytes<=plan.model_policy.request_budget_bytes
            and type(image_bytes) is int and image_bytes>=0,
            'The complete forward request reservation differs.')
        rows.append((ordinal,batch.batch_id,
            _json([plan.chunks[i-1].chunk_id for i in batch.chunk_ordinals]),digest,
            wire_bytes+image_bytes,_json(list(batch.chunk_ordinals)),batch.page,
            int(batch.visual),wire_bytes,image_bytes))
    result=tuple(rows)
    _require(len({row[1] for row in result})==len(result)
        and all(type(row) is tuple and len(row)==10
            and all(type(row[i]) is int for i in (0,4,6,7,8,9))
            and all(type(row[i]) is str for i in (1,2,3,5)) for row in result),
        'The complete forward inventory must contain only primitive rows.')
    checked_admitted_plan(plan)
    _require(hmac.compare_digest(entry_seal,plan._layout_validation_seal),
        'The admitted forward plan changed during inventory compilation.')
    return result


def _request_for_job(plan,batch,job):
    from .pdf_visual_authoring import _evidence_catalog
    scope=json.loads(job.scope_json)
    contract = _contract(plan)
    forward_compact = plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21')
    final_guidance = contract.final_guidance(scope) if forward_compact else writer._final_guidance(scope)
    baseline_tokens = contract.baseline_reserved_tokens(scope, job) if forward_compact else job.estimated_tokens
    messages=(contract.request_messages(scope, job)
        if plan.policy_version in ('wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21')
        else json.loads(job.messages_json))+[{'role':'user','content':final_guidance}]
    image_inputs=job.images
    if plan.policy_version in ('wiki-pdf-layout-retention-v2','wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        extra_guidance = contract.supplemental_guidance(plan, scope, job)
        schema_delta = contract.additional_schema_tokens(scope) if plan.policy_version in ('wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else 0
        if baseline_tokens + schema_delta + writer.estimate_text_tokens(extra_guidance) > writer.CONTEXT_TOKENS:
            raise AuthoringWorkLimitError('Complete forward supplemental input exceeds context reservation.')
        messages[-1]['content'] += extra_guidance
    # This exact ordering is owned by the ordinary image adapter, replayed on
    # receipt verification; never inject already prepared image parts twice.
    prepared=prepare_image_messages(messages,image_inputs,inline=True) if image_inputs else messages
    policy=plan.model_policy
    if policy.provider_order!=('hynix_api',) or not policy.wire_models or not policy.model_ids:
        raise AuthoringIncompleteError('Layout writer needs a frozen OpenAI-compatible model binding.')
    model=dict(policy.wire_models)[policy.model_ids[0]]
    form = contract.response_format(scope) if plan.policy_version in ('wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else response_format(scope)
    output_tokens = (contract.output_tokens(plan, scope, job)
        if plan.policy_version in ('wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else policy.max_tokens)
    if plan.policy_version in ('wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        from .pdf_layout_retention_v4 import _image_label_tokens
        complete_reserved = (baseline_tokens + schema_delta
            + writer.estimate_text_tokens(extra_guidance) + _image_label_tokens(image_inputs)
            + output_tokens - contract.OUTPUT_FLOOR)
        if complete_reserved > writer.CONTEXT_TOKENS:
            raise AuthoringWorkLimitError('Complete job-local output/input exceeds context reservation.')
    encoded=encode_chat_payload(chat_payload(model,prepared,max_tokens=output_tokens,
        temperature=.1,response_format=form,
        **(contract.request_options(plan) if plan.policy_version in ('wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21') else {})))
    if len(encoded)>policy.request_budget_bytes:
        raise AuthoringWorkLimitError('Complete forward request exceeds its byte reservation.')
    metadata=validate_image_inputs(image_inputs,plan.source.render_policy.image_policy(len(image_inputs))) if image_inputs else ()
    reservation={'page':batch.page,'wire_bytes':len(encoded),
        'image_bytes':sum(len(i.data) for i in image_inputs),
        'pixels':sum(row['width']*row['height'] for row in metadata)}
    if plan.policy_version in ('wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
        reservation.update(output_tokens=output_tokens,
            complete_reserved_tokens=complete_reserved,
            output_allocation_version=contract.ALLOCATION_VERSION)
    digest=_hash(_json({'plan':plan.plan_sha256,'batch':asdict(batch),'task':_contract(plan).VERSION,
        'messages':messages,'wire_sha256':sha256(encoded).hexdigest(),'reservation':reservation}))
    evidence=_evidence_catalog(plan,batch)
    return {**scope,'schema_version':2,'batch_id':batch.batch_id,
            'evidence_catalog_sha256':evidence.sha256},messages,digest,reservation


def decode(text,scope):
    # Strict candidate decoder rejects duplicate keys/nonfinite values and
    # checks every owned visual alias before this parse can be used.
    writer.decode_job(SimpleNamespace(scope_json=_json(scope)),text)
    value=json.loads(text)
    return {'schema_version':scope['schema_version'],'batch_id':scope['batch_id'],
        'evidence_catalog_sha256':scope['evidence_catalog_sha256'],
        'provenance_level':'batch_input_context','markdown':value['wiki_md'],'figures':value['figures']}


def assemble(plan,by_id):
    from .pdf_visual_authoring import _replay,_chunk_row,_missing_quantity_lines,_evidence_catalog,_literal
    from .pdf_evidence_units import EVIDENCE_VERSION
    from .. import document_pdf_geometry as geometry
    lines,chunks,legacy_tables,page_visuals,batches=[],{},[],{},[]
    layout_tables,figures=[],[]
    figure_parents,caption_seen,visual_notice_seen={},set(),set()
    table_ranges,seen={},set()
    size=0
    def emit(value):
        nonlocal size
        start=size
        size+=len(value.encode())+2
        if size>plan.work_policy.markdown_bytes:
            raise AuthoringWorkLimitError('No partial layout Wiki publication.')
        lines.append(value)
        return [start,start+len(value.encode())]
    emit('# '+_literal(plan.source.geometry.filename)+' — 지식 Wiki')
    for batch in plan.batches:
        receipt=by_id[batch.batch_id]
        document,_=_replay(plan,batch.ordinal,receipt)
        job=job_for(plan,batch)
        scope=json.loads(job.scope_json)
        prose=supported_markdown(document['markdown'])
        prose_range=emit(prose)
        pages=sorted({plan.chunks[n-1].page for n in batch.chunk_ordinals}|set(batch.visual_pages))
        batches.append({'batch_id':batch.batch_id,'response_sha256':receipt.response_sha256,
            'source_pages':pages,'source_chunk_ids':[plan.chunks[n-1].chunk_id for n in batch.chunk_ordinals],
            'output_byte_range':prose_range})
        for n in batch.chunk_ordinals:
            chunk=plan.chunks[n-1]
            chunks[n]={**_chunk_row(chunk),'claims':[],'batch_id':batch.batch_id,
                'output_byte_ranges':[prose_range],
                'quantity_diagnostics':quantity_diagnostics(chunk.text,document['markdown'],_missing_quantity_lines)}
        for unit in scope['visual_units']:
            # Compile exactly this raw record under its owned source alias.
            notice_ranges=[]
            if unit['unit_id'] not in visual_notice_seen:
                visual_notice_seen.add(unit['unit_id'])
                notice_ranges.append(emit('그림의 설명과 곡선 값은 모델 판독입니다. '
                    '곡선 값은 원시 측정값이 아닌 근삿값이며, 의미·수치 정확성을 자동 검증하지 않았습니다.'))
                detail=unit.get('native_detail')
                if detail and detail.get('status') in ('unavailable','unavailable_in_writer','not_uniquely_associated'):
                    notice_ranges.append(emit('이 그림은 원본 확대 판독을 적용할 수 없어 '
                        '보존된 페이지 이미지의 해상도로 분석했습니다. 작은 문자나 겹친 곡선은 원본과 대조해야 합니다.'))
            local_scope={**scope,'writing_mode':'visual_only','visual_units':[unit]}
            record=_json({'wiki_md':'','figures':{unit['alias']:document['figures'][unit['alias']]}})
            visual_body=(_contract(plan).render_visual_record(record,local_scope)
                if plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21')
                else writer._decode_structured(record,local_scope))
            if plan.policy_version in ('wiki-pdf-layout-retention-v2','wiki-pdf-layout-retention-v3','wiki-pdf-layout-retention-v4','wiki-pdf-layout-retention-v5','wiki-pdf-layout-retention-v6','wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
                from .pdf_layout_math_format import normalize_markdown
                visual_body=normalize_markdown(visual_body)
            visual_range=emit(visual_body)
            ranges=[*notice_ranges,visual_range]
            if unit['caption'] and unit['unit_id'] not in caption_seen:
                caption_seen.add(unit['unit_id'])
                ranges.append(emit('원문 그림 설명\n\n<pre>'+escape(unit['caption'])+'</pre>'))
            record={'unit_id':unit['unit_id'],'page':unit['page'],
                'image_source_id':job.images[unit['image_index']].source_id,
                'image_sha256':sha256(job.images[unit['image_index']].data).hexdigest(),
                'output_byte_ranges':list(ranges),'analysis':'unreviewed_model_reading','accuracy_verified':False}
            if 'figure_roi' in unit:
                record['figure_roi'] = unit['figure_roi']
            if plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
                # Exact provider groups survive in the source-bound ledger.
                # Domain consistency is not a source-reading accuracy proof.
                record['curve_domain_readings'] = document['figures'][unit['alias']]['estimated_values']
            if 'view_id' in unit:
                parent=figure_parents.get(unit['unit_id'])
                if parent is None:
                    parent={**record,'output_byte_ranges':[],'views':[]}
                    figure_parents[unit['unit_id']]=parent
                    figures.append(parent)
                parent['output_byte_ranges'].extend(ranges)
                context=job.images[unit['source_view']['context_image_index']]
                view_record={'view_id':unit['view_id'],
                    'image_source_id':record['image_source_id'],'image_sha256':record['image_sha256'],
                    'context_source_id':context.source_id,'context_sha256':sha256(context.data).hexdigest(),
                    'output_byte_ranges':list(ranges)}
                if plan.policy_version in ('wiki-pdf-layout-retention-v7','wiki-pdf-layout-retention-v8','wiki-pdf-layout-retention-v9','wiki-pdf-layout-retention-v10','wiki-pdf-layout-retention-v11','wiki-pdf-layout-retention-v12','wiki-pdf-layout-retention-v13','wiki-pdf-layout-retention-v14','wiki-pdf-layout-retention-v15','wiki-pdf-layout-retention-v16','wiki-pdf-layout-retention-v17','wiki-pdf-layout-retention-v18','wiki-pdf-layout-retention-v19','wiki-pdf-layout-retention-v20','wiki-pdf-layout-retention-v21'):
                    view_record['curve_domain_readings'] = record['curve_domain_readings']
                parent['views'].append(view_record)
            else:
                figures.append(record)
            page_visuals.setdefault(unit['page'],[]).extend(ranges)
        for table in scope['tables']:
            if table['unit_id'] in seen:
                continue
            seen.add(table['unit_id'])
            emit('### 원문 표 '+table['label']+' · p. '+str(table['page']))
            emit('표 구조는 원문 좌표 기반 추정입니다. 빈 셀을 0이나 해당 없음으로 해석하지 않습니다.')
            emit('<pre>'+escape(table.get('caption_context',table['text']))+'</pre>')
            if table['status']=='extracted':
                literal=writer._literal_table(table)
                output_range=emit(literal)
                layout_tables.append({'unit_id':table['unit_id'],'page':table['page'],
                    'table_sha256':_hash(literal),'cells':sum(len(r['cells']) for r in table['rows']),
                    'output_byte_range':output_range})
                if 'native_grid_block_id' in table:
                    table_ranges[table['native_grid_block_id']]=output_range
            else:
                emit('표 구조 판독 한계: '+table['reason']+'. 원문 이미지 판독 결과와 함께 확인해야 합니다.')
        emit('근거 범위: '+_literal(plan.source.geometry.filename)+' · p. '+', '.join(map(str,pages)))
    for page in plan._doc.pages:
        for block in page.blocks:
            if type(block) is geometry.PdfTableBlock:
                fragment=plan._tables[block.block_id]
                output_range=table_ranges.get(block.block_id)
                if output_range is None:
                    emit('### 원문 표 · p. '+str(page.number))
                    output_range=emit(fragment)
                legacy_tables.append({'block_id':block.block_id,'page':page.number,'rendered_sha256':_hash(fragment),
                    'cells':[{'row':c.row,'column':c.column,'text_sha256':_hash(c.text)} for c in block.cells],
                    'output_byte_range':output_range})
    markdown='\n\n'.join(lines)+'\n'
    visuals=[{'page':p.page,'image':p.identity(),
        'analysis':'model_reading' if p.page in page_visuals else 'text_sufficient',
        'output_byte_ranges':page_visuals.get(p.page,[])} for p in plan.source.pages]
    manifest={'schema_version':1,'version':_contract(plan).VERSION,'source':plan.source.identity(),
        'plan_sha256':plan.plan_sha256,'prompt_sha256':_hash(PROMPT),'reservation_json':plan.reservation_json,
        'chunks':[chunks[n] for n in sorted(chunks)],'tables':legacy_tables,'visuals':visuals,
        'receipts':[asdict(by_id[b.batch_id]) for b in plan.batches],
        'all_nonblank_ranges_authored':False,'all_nonblank_ranges_delivered':True,
        'all_detected_grid_cells_rendered':True,'all_pages_rendered':True,'all_visual_tasks_returned':False,
        'semantic_review_performed':False,'semantic_completeness_proven':False,'visual_accuracy_verified':False,
        'caption_ownership_inferred':False,'canonical_publication_ready':False,'output_range_unit':'utf8_bytes',
        'wiki_sha256':_hash(markdown),'wiki_bytes':len(markdown.encode()),'evidence_version':EVIDENCE_VERSION,
        'evidence_catalogs':[{'sha256':c.sha256,'catalog':json.loads(c.canonical_json)} for c in
            (_evidence_catalog(plan,b) for b in plan.batches)],
        'provenance_level':'batch_input_context','batch_prose':batches,
        'layout_details':{'version':LEDGER_VERSION,'source_identity':plan.layout_source.identity(),
            'tables':layout_tables,'figures':figures}}
    encoded=_json(manifest)
    if len(markdown.encode())>plan.work_policy.markdown_bytes or len(encoded.encode())>plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete layout result exceeds its reservation.')
    return AuthoredWiki(markdown,encoded)
