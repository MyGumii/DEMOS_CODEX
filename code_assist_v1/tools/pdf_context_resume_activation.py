"""A10541-only successor activation retaining exact v95/v96 provenance.

Pure artifact constructors and validators. No migration, model call, old marker
rewrite, credential recreation, or runtime override is performed here.
"""
from hashlib import sha256
import json
from pathlib import Path

ACTIVATION_NAME = 'clean-second-brain-10541-v97-enabled.json'
KIND = 'second-brain-clean-10541-v97'
CONTRACT = 'pdf-source-continuation-v1'
_PREVIOUS_FIELDS = frozenset({'previous_binding_document','previous_binding_sha256'})
_MARKER_FIELDS = frozenset({'kind','port','data_root','source_data_root','schema_version',
    'schema_sha256','catalog_checksum_sha256','predecessor_catalog_checksum_sha256',
    'predecessor_activation_sha256','previous_binding_sha256','continuation_contract',
    'pdf_upload_policy_sha256','pdf_context_window_tokens','pdf_output_token_reservation',
    'additional_llm_review_calls','registry_sha256','provider','model_id','wire_model','codex_reasoning_effort'})


def _catalog():
    from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
    return PDF_CONTEXT_RESUME_MIGRATION_CATALOG


def previous_binding(value):
    from code_assist_v1.tools import recovered_preview_binding as binding
    raw=value.get('previous_binding_document')
    if type(raw) is not str or not 0<len(raw.encode('utf-8'))<=131072:
        raise binding.RecoveryBindingError('Missing immutable v96 predecessor document.')
    if sha256(raw.encode('utf-8')).hexdigest()!=binding._digest(value.get('previous_binding_sha256')):
        raise binding.RecoveryBindingError('The v96 predecessor document differs from its pin.')
    try:
        prior=json.loads(raw,object_pairs_hook=binding._unique_object)
    except (ValueError,UnicodeError) as exc:
        raise binding.RecoveryBindingError('Invalid v96 predecessor document.') from exc
    if type(prior) is not dict or prior.get('schema_version')!=2:
        raise binding.RecoveryBindingError('The continuation activation requires a v96 predecessor.')
    return prior


def context_source_view(value, *, legacy_forward_view):
    from code_assist_v1.tools import recovered_preview_binding as binding
    if (type(value) is not dict or set(value)!=binding._FORWARD_FIELDS|_PREVIOUS_FIELDS
            or type(value.get('schema_version')) is not int or value['schema_version']!=3
            or value.get('schema_catalog_checksum')!=_catalog().checksum_sha256):
        raise binding.RecoveryBindingError('Invalid isolated v97 operator binding.')
    prior=previous_binding(value)
    legacy=legacy_forward_view(prior)
    for name in binding._FORWARD_FIELDS-{'schema_version','schema_catalog_checksum',
            'current_marker_sha256','activation_sha256','data_root','code_root'}:
        if value[name]!=prior[name]:
            raise binding.RecoveryBindingError('The continuation binding changed prior provenance.')
    hashes,previous=value['activation_sha256'],prior['activation_sha256']
    if (type(hashes) is not dict or set(hashes)!=set(previous)|{ACTIVATION_NAME}
            or any(hashes[name]!=digest for name,digest in previous.items())
            or hashes[ACTIVATION_NAME]!=binding._digest(value['current_marker_sha256'])):
        raise binding.RecoveryBindingError('The continuation inventory must append one activation.')
    return {**legacy,'data_root':value['data_root'],'code_root':value['code_root']}


def validate_context_marker(root,value, *, validate_previous):
    from code_assist_v1.tools import recovered_preview_binding as binding
    from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as previous_catalog
    prior=previous_binding(value)
    validate_previous(root,prior)
    raw=binding._read(Path(root)/'.runtime'/ACTIVATION_NAME)
    if sha256(raw).hexdigest()!=value['current_marker_sha256']:
        raise binding.RecoveryBindingError('The v97 activation differs from its pin.')
    try:
        marker=json.loads(raw,object_pairs_hook=binding._unique_object)
    except (ValueError,UnicodeError) as exc:
        raise binding.RecoveryBindingError('Invalid v97 activation JSON.') from exc
    catalog=_catalog()
    if (type(marker) is not dict or set(marker)!=_MARKER_FIELDS
            or marker['kind']!=KIND or type(marker['port']) is not int or marker['port']!=10541
            or type(marker['schema_version']) is not int or marker['schema_version']!=97
            or marker['data_root']!=value['activation_data_root']
            or marker['source_data_root']!=value['source_data_root']
            or marker['catalog_checksum_sha256']!=catalog.checksum_sha256
            or marker['schema_sha256']!=catalog.migration(97).expected_schema_sha256
            or marker['predecessor_catalog_checksum_sha256']!=previous_catalog.checksum_sha256
            or marker['predecessor_activation_sha256']!=prior['current_marker_sha256']
            or marker['previous_binding_sha256']!=value['previous_binding_sha256']
            or marker['continuation_contract']!=CONTRACT
            or type(marker['pdf_context_window_tokens']) is not int or marker['pdf_context_window_tokens']!=16384
            or type(marker['pdf_output_token_reservation']) is not int or marker['pdf_output_token_reservation']!=4096
            or type(marker['additional_llm_review_calls']) is not int or marker['additional_llm_review_calls']!=0):
        raise binding.RecoveryBindingError('The v97 activation differs from its exact predecessor contract.')
    previous_marker=json.loads(binding._read(Path(root)/'.runtime'/binding._PDF_ATOMIC_PARTITION_ACTIVATION),
        object_pairs_hook=binding._unique_object)
    if any(marker[name]!=previous_marker[name] for name in (
            'provider','model_id','wire_model','codex_reasoning_effort','registry_sha256','pdf_upload_policy_sha256')):
        raise binding.RecoveryBindingError('The v97 activation changed its frozen provider or upload policy.')


def context_resume_receipt(root,prior):
    from code_assist_v1.tools import recovered_preview_binding as binding
    from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as old
    if prior is None or prior.forward_partition is not True or prior.forward_continuation:
        raise ValueError('The continuation activation requires the exact v96 selection.')
    root=Path(root).resolve(strict=True)
    current=binding.load_binding(root)
    if current is None or current.port!=10541 or current.provider_mode!='openai-compatible':
        raise ValueError('The continuation activation is restricted to A10541 API mode.')
    origin=current.activation_data_root or root
    if origin.name!='data-root' or origin.parent.parent.name!='wiki-generation-ab-A-10541-20261011-r1':
        raise ValueError('The continuation activation requires its explicit A10541 origin.')
    previous_sha=current.partition_marker_sha256 or current.current_marker_sha256
    if previous_sha!=prior.activation_sha256:
        raise ValueError('The continuation predecessor selection changed.')
    previous_doc_sha=current.previous_binding_sha256 or current.document_sha256
    catalog=_catalog()
    return dict(kind=KIND,port=10541,data_root=str(origin),source_data_root=str(current.source_data_root),
        schema_version=97,schema_sha256=catalog.migration(97).expected_schema_sha256,
        catalog_checksum_sha256=catalog.checksum_sha256,predecessor_catalog_checksum_sha256=old.checksum_sha256,
        predecessor_activation_sha256=previous_sha,previous_binding_sha256=previous_doc_sha,
        continuation_contract=CONTRACT,pdf_upload_policy_sha256=sha256(prior.upload_policy().encoded().encode()).hexdigest(),
        pdf_context_window_tokens=16384,pdf_output_token_reservation=4096,
        additional_llm_review_calls=0,registry_sha256=prior.registry_sha256,
        provider=prior.provider,model_id=prior.model_id,wire_model=prior.wire_model,
        codex_reasoning_effort=prior.codex_reasoning_effort)


def context_resume_10541_binding_document(root, *, source_binding_path,source_binding_sha256):
    from code_assist_v1.tools import recovered_preview_binding as binding
    root=Path(root).resolve(strict=True)
    raw=binding._read(binding._absolute(str(source_binding_path)))
    if sha256(raw).hexdigest()!=binding._digest(source_binding_sha256):
        raise binding.RecoveryBindingError('The current v96 operator document changed.')
    source=json.loads(raw,object_pairs_hook=binding._unique_object)
    verified=binding.load_binding(root,environ={binding.PATH_ENV:str(source_binding_path),binding.SHA_ENV:source_binding_sha256})
    if verified is None or source.get('schema_version')!=2 or verified.current_marker_sha256 is None:
        raise binding.RecoveryBindingError('The continuation constructor requires the exact v96 operator.')
    marker_sha=sha256(binding._read(root/'.runtime'/ACTIVATION_NAME)).hexdigest()
    value={**source,'schema_version':3,'schema_catalog_checksum':_catalog().checksum_sha256,
        'current_marker_sha256':marker_sha,'previous_binding_document':raw.decode('utf-8'),
        'previous_binding_sha256':source_binding_sha256,
        'activation_sha256':{**source['activation_sha256'],ACTIVATION_NAME:marker_sha}}
    context_source_view(value,legacy_forward_view=binding._forward_source_view)
    validate_context_marker(root,value,validate_previous=binding._validate_forward_marker)
    return value
