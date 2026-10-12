"""Opt-in typed publication composer; old publication producers remain unchanged.

Own candidate validation precedes common history selection. Public-body leases
are service-owned and held through commit. No prior private candidate is read.
"""
from dataclasses import dataclass, replace
import hmac
import secrets

from .native_publication_commit import NativePublicationCommitRepository, has_native_request
from .publication_commit import (FrozenPublicationSource, PublicationCommitRepository,
    PublicationCommitConflictError, PublicationCommitIntegrityError, publication_operation_id)
from .publication_body_request import requested_publication_body
from .published_upload_history import PublishedBodyIdentity
from ..migrations.v031_publication_body_selection import _BOUND, _KINDS
from ..services.preserved_publication import has_preservation_request


@dataclass(frozen=True, slots=True)
class FrozenCrossKindPublicationSource(FrozenPublicationSource):
    requested_body: object = None
    selected_body: PublishedBodyIdentity | None = None
    own_candidate: FrozenPublicationSource | None = None
    public_body: object = None
    preparation_seal: str = ''

    @property
    def publication_body_metadata(self):
        body = self.selected_body
        return {'body_kind':body.kind,'selected_snapshot_id':body.snapshot_id,
            'requested_body_kind':self.requested_body.kind,
            'requested_snapshot_id':self.requested_body.snapshot_id,
            'selected_manifest_sha256':body.manifest_sha256}


def selection_row(connection,operation):
    row = connection.execute('SELECT * FROM publication_body_selection WHERE publication_operation_id=?',
        (operation,)).fetchone()
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pdf_continuation_body_selection'").fetchone():
        return row
    continuation = connection.execute("SELECT * FROM pdf_continuation_body_selection WHERE publication_operation_id=?",
        (operation,)).fetchone()
    if row is not None and continuation is not None:
        raise PublicationCommitIntegrityError("Publication has overlapping typed body receipts.")
    if continuation is None:
        return row
    from ..migrations.v097_pdf_context_resume_publication import KINDS
    mapped = dict(continuation)
    mapped['requested_kind'] = 'pdf_continuation_wiki'
    for kind in KINDS:
        mapped['requested_'+kind+'_id'] = mapped['requested_snapshot_id'] if kind == 'pdf_continuation_wiki' else None
        mapped['selected_'+kind+'_id'] = mapped['selected_snapshot_id'] if kind == mapped['selected_kind'] else None
    return mapped


def selected_result_fields(row):
    kind = row['selected_kind']
    selected = row[f'selected_{kind}_id']
    return dict(authored_snapshot_id=selected if kind=='authored_wiki' else None,
        preserved_snapshot_id=selected if kind=='preserved_wiki' else None,
        native_snapshot_id=selected if kind=='native_wiki' else None,
        pptx_role_snapshot_id=selected if kind=='pptx_role_wiki' else None,
        pdf_snapshot_id=selected if kind=='pdf_wiki' else None,
        pdf_visual_snapshot_id=selected if kind=='pdf_visual_wiki' else None,
        office_hybrid_snapshot_id=selected if kind=='office_hybrid_wiki' else None,
        pdf_continuation_snapshot_id=selected if kind=='pdf_continuation_wiki' else None,
        reused_extraction_snapshot_id=selected if kind=='extraction' and row['source_publication_operation_id'] else None)


class CrossKindPublicationCommitRepository(NativePublicationCommitRepository):
    def __init__(self,native_source,public_bodies):
        super().__init__(native_source)
        self.public_bodies=public_bodies
        self._preparation_key=secrets.token_bytes(32)

    @staticmethod
    def _body_contract(connection):
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='office_hybrid_registered_publication'").fetchone():
            from ..migrations.v058_office_hybrid_publication_body import KINDS, BODY_BOUND
            return KINDS, BODY_BOUND
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pdf_visual_publication_snapshot'").fetchone():
            from ..migrations.v045_pdf_visual_publication import KINDS, BODY_BOUND
            return KINDS, BODY_BOUND
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pdf_publication_snapshot'").fetchone():
            from ..migrations.v041_pdf_publication import KINDS, BODY_BOUND
            return KINDS, BODY_BOUND
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pptx_role_publication_snapshot'").fetchone():
            from ..migrations.v037_pptx_role_publication import KINDS, BODY_BOUND
            return KINDS, BODY_BOUND
        return _KINDS, _BOUND

    def _revalidate_own_candidate(self, connection, draft, source):
        return super()._revalidate_frozen_source(connection, draft, source)

    @staticmethod
    def _prior(*args,**kwargs):
        return ()  # Own preserved candidate validation only; common history owns reuse.

    @staticmethod
    def _native_prior(*args,**kwargs):
        return ()  # Never let requested kind/policy filter out earlier public bodies.

    def freeze_source(self,connection,**kwargs):
        if (kwargs.get('authored_snapshot_id') is not None
                and not has_native_request(connection,kwargs['preview_id'])
                and not has_preservation_request(connection,kwargs['preview_id'])):
            kwargs=dict(kwargs,authorized_targets=())
            return PublicationCommitRepository.freeze_source(self,connection,**kwargs)
        return super().freeze_source(connection,**kwargs)

    def _seal(self,source):
        # Full immutable own/selected strings have already been hashed outside
        # the writer. Bind identities and every small field, never rehash MBs.
        values=[]
        for field in FrozenPublicationSource.__dataclass_fields__:
            value=getattr(source,field)
            values.append((field,id(value) if field in ('content_md','extraction_source') else value))
        values.extend((source.requested_body,source.selected_body,id(source.own_candidate),id(source.public_body)))
        return hmac.digest(self._preparation_key,repr(values).encode(),'sha256').hex()

    def compose(self,own,requested,own_identity,prepared,targets):
        selected=prepared.history.selected
        identity=selected.body if selected else own_identity
        selected_blob=selected.storage.blob_sha256 if selected else None
        content=(None if selected_blob else prepared.markdown) if selected else own.content_md
        fields={name:getattr(own,name) for name in FrozenPublicationSource.__dataclass_fields__}
        extraction=own
        while extraction.extraction_source is not None:
            extraction=extraction.extraction_source
        fields.update(raw_sha256=identity.raw_sha256,normalized_sha256=identity.normalized_sha256,
            size_bytes=identity.size_bytes,normalizer_version=identity.normalizer_version,
            content_md=content,content_blob_hash=selected_blob if selected else own.content_blob_hash,
            content_sha256=identity.raw_sha256,extraction_source=extraction,
            authored_snapshot_id=None,authored_fence=None,authored_preparation=None,
            reused_extraction_snapshot_id=None,reuse_targets=tuple(targets),reuse_fence=prepared.history.reuse_anchors)
        result=FrozenCrossKindPublicationSource(**fields,requested_body=requested,selected_body=identity,
            own_candidate=own,public_body=prepared)
        return replace(result,preparation_seal=self._seal(result))

    def _revalidate_frozen_source(self,connection,draft,source):
        if not isinstance(source,FrozenCrossKindPublicationSource):
            return super()._revalidate_frozen_source(connection,draft,source)
        if not hmac.compare_digest(source.preparation_seal,self._seal(source)):
            raise PublicationCommitIntegrityError('The selected publication preparation changed.')
        metadata=source.publication_body_metadata
        expected_metadata={'body_kind':source.selected_body.kind,
            'selected_snapshot_id':source.selected_body.snapshot_id,
            'requested_body_kind':source.requested_body.kind,
            'requested_snapshot_id':source.requested_body.snapshot_id,
            'selected_manifest_sha256':source.selected_body.manifest_sha256}
        if (type(metadata) is not dict or metadata.keys()!=expected_metadata.keys()
                or any(type(metadata[key]) is not type(value) or metadata[key]!=value
                    for key,value in expected_metadata.items())):
            raise PublicationCommitIntegrityError('Publication body metadata differs from its sealed identities.')
        history=source.public_body.history
        binding=history.binding
        if ((draft.actor_user_id,draft.preview_id,draft.artifact_id,draft.extraction_snapshot_id,draft.source_snapshot_sha256)
                !=(binding.actor_user_id,binding.preview_id,binding.artifact_id,binding.extraction_snapshot_id,binding.source_snapshot_sha256)
                or source.reuse_targets!=draft.targets or binding.created_at_ms>draft.created_at_ms):
            raise PublicationCommitConflictError('The selected body belongs to a different publication request.')
        # Revalidate the exact new completed candidate even if an earlier public
        # body wins. This must not become raw fallback for stopped/pending work.
        self._revalidate_own_candidate(connection,draft,source.own_candidate)
        own=source.own_candidate
        current=requested_publication_body(connection,actor_id=draft.actor_user_id,preview_id=draft.preview_id,
            authored_snapshot_id=own.authored_snapshot_id)
        if current!=source.requested_body:
            raise PublicationCommitConflictError('The requested publication identity changed.')
        self.public_bodies.revalidate_in_transaction(connection,source.public_body)

    def _commit_source_selection(self,connection,draft,source,operation_id,failpoint):
        if not isinstance(source,FrozenCrossKindPublicationSource):
            return super()._commit_source_selection(connection,draft,source,operation_id,failpoint)
        requested,selected=source.requested_body,source.selected_body
        if requested.kind == 'pdf_continuation_wiki':
            witness = source.public_body.history.selected
            connection.execute("""INSERT INTO pdf_continuation_body_selection(
                publication_operation_id,preview_id,request_id,requested_snapshot_id,selected_kind,selected_snapshot_id,
                selected_body_root_operation_id,source_publication_operation_id,source_space_id,source_item_id,source_revision_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (operation_id,draft.preview_id,requested.run_id,requested.snapshot_id,
                    selected.kind,selected.snapshot_id,witness.root_operation_id if witness else operation_id,
                    witness.operation_id if witness else None,witness.space_id if witness else None,
                    witness.item_id if witness else None,witness.current_revision_id if witness else None))
            if failpoint:
                failpoint('publication.after_body_selection')
            return
        kinds, _ = self._body_contract(connection)
        witness=source.public_body.history.selected
        fields=['publication_operation_id','preview_id','requested_kind',
            *(f'requested_{kind}_id' for kind in kinds),'selected_kind',
            *(f'selected_{kind}_id' for kind in kinds),'selected_body_root_operation_id',
            'source_publication_operation_id','source_space_id','source_item_id','source_revision_id']
        values=[operation_id,draft.preview_id,requested.kind,
            *(requested.snapshot_id if kind==requested.kind else None for kind in kinds),selected.kind,
            *(selected.snapshot_id if kind==selected.kind else None for kind in kinds),
            witness.root_operation_id if witness else operation_id,
            witness.operation_id if witness else None,witness.space_id if witness else None,
            witness.item_id if witness else None,witness.current_revision_id if witness else None]
        connection.execute('INSERT INTO publication_body_selection('+','.join(fields)+') VALUES ('+
            ','.join('?' for _ in values)+')',tuple(values))
        if failpoint:
            failpoint('publication.after_body_selection')

    def commit(self,connection,draft,*,failpoint=None):
        result=super().commit(connection,draft,failpoint=failpoint)
        if isinstance(draft.frozen_source,FrozenCrossKindPublicationSource):
            return replace(result,**selected_result_fields(selection_row(connection,result.publication_operation_id)))
        return result

    def require_exact_replay(self,connection,*,body_selection=False,**kwargs):
        if not body_selection:
            return super().require_exact_replay(connection,**kwargs)
        operation=publication_operation_id(kwargs['actor_user_id'],kwargs['operation_id'])
        row=selection_row(connection,operation)
        if row is not None and row['requested_kind'] == 'pdf_continuation_wiki':
            from ..migrations.v097_pdf_context_resume_publication import CONTINUATION_BOUND
            receipt_table, bound = 'pdf_continuation_body_selection', CONTINUATION_BOUND
        else:
            receipt_table = 'publication_body_selection'
            _, bound = self._body_contract(connection)
        if row is None or row['preview_id']!=kwargs['preview_id'] or not connection.execute(
                f'SELECT 1 FROM {receipt_table} g WHERE g.publication_operation_id=? AND EXISTS({bound})',
                (operation,)).fetchone():
            raise PublicationCommitIntegrityError('The typed publication receipt is inconsistent.')
        result=PublicationCommitRepository.require_exact_replay(self,connection,**kwargs)
        return replace(result,**selected_result_fields(row))
