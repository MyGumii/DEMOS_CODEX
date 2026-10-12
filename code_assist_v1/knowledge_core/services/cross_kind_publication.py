"""Explicit all-upload composer; no live startup or feature activation here."""
from contextlib import ExitStack
from contextvars import ContextVar

from ..db import db_now_ms,transaction,TransactionMode
from ..repositories.cross_kind_publication import (CrossKindPublicationCommitRepository,
    selection_row,selected_result_fields)
from ..repositories.publication_commit import (_SourceBinding,publication_operation_id,
    PublicationCommitConflictError,PublicationCommitIntegrityError)
from ..repositories.publication_body_request import requested_publication_body
from ..repositories.published_upload_history import PublishedBodyIdentity
from .native_wiki_publication import NativeWikiPublicationService,NativeWikiPublicationSelector
from .publication import PublicationPreviewAuthorizationError,PublicationPreviewServiceError
from .published_upload_body import CanonicalPublishedUploadBodyService


class CrossKindPublicationSelector(NativeWikiPublicationSelector):
    @staticmethod
    def has_request(connection,preview_id):
        return NativeWikiPublicationSelector.has_request(connection,preview_id) or connection.execute(
            'SELECT 1 FROM publication_body_selection WHERE preview_id=?',(preview_id,)).fetchone() is not None

    def replay_options(self,connection,*,actor_id,operation_id,stored,requested_authored_snapshot_id):
        operation=publication_operation_id(actor_id,operation_id)
        row=selection_row(connection,operation)
        if row is None:
            return super().replay_options(connection,actor_id=actor_id,operation_id=operation_id,
                stored=stored,requested_authored_snapshot_id=requested_authored_snapshot_id)
        fields=selected_result_fields(row)
        if (stored.publication_operation_id!=operation or stored.preview_id!=row['preview_id']
                or stored.body_source!=row['selected_kind']
                or any(getattr(stored,key)!=value for key,value in fields.items())
                or requested_authored_snapshot_id is not None
                   and requested_authored_snapshot_id!=row['requested_authored_wiki_id']):
            raise PublicationCommitIntegrityError('The saved result disagrees with its requested/selected body receipt.')
        return {'body_selection':True}


class CrossKindPublicationService(NativeWikiPublicationService):
    def __init__(self,factory,scope_resolver,stager,**kwargs):
        super().__init__(factory,scope_resolver,stager,**kwargs)
        self._public_bodies=CanonicalPublishedUploadBodyService(factory,scope_resolver,stager)
        self._commits=CrossKindPublicationCommitRepository(self._commits.native_source,self._public_bodies)
        self._wiki_publication=CrossKindPublicationSelector(factory)
        # Each simultaneous call owns a separate stack/capability. A shared
        # service instance never stores one caller's active lease in another's.
        self._body_context=ContextVar('publication_body_context',default=None)

    def publish(self,command):
        with ExitStack() as stack:
            token=self._body_context.set(stack)
            try:
                return super().publish(command)
            finally:
                self._body_context.reset(token)

    def _freeze_new_publication_source(self,**fields):
        own=super()._freeze_new_publication_source(**fields)
        if own is None or own.origin_type!='upload':
            return own  # Exact committed replay never opens a public body lease.
        try:
            connection=self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    preview=self._previews.require_for_actor(connection,preview_id=fields['preview_id'],
                        actor_user_id=fields['actor_id'])
                    targets=self._authored_preparation_targets(connection,actor_id=fields['actor_id'],
                        epoch=fields['epoch'],preview=preview,target_mode=fields['target_mode'],
                        target=fields['target'],personal_space_id=fields['personal_space_id'])
                    binding=_SourceBinding(fields['actor_id'],preview.preview_id,preview.artifact_id,
                        preview.extraction_snapshot_id,fields['source_snapshot_sha256'],db_now_ms(connection))
                    requested=requested_publication_body(connection,actor_id=fields['actor_id'],
                        preview_id=preview.preview_id,authored_snapshot_id=own.authored_snapshot_id)
                    identity=connection.execute('''SELECT raw_sha256,normalized_sha256,size_bytes,
                        normalizer_version,manifest_sha256,manifest_size_bytes,policy_version
                        FROM publication_body_identity WHERE kind=? AND snapshot_id=?''',
                        (requested.kind,requested.snapshot_id)).fetchone()
                    if identity is None or tuple(identity)[:4]!=(own.raw_sha256,own.normalized_sha256,
                            own.size_bytes,own.normalizer_version):
                        raise PublicationCommitIntegrityError('Own completed candidate identity changed before selection.')
                    own_identity=PublishedBodyIdentity(requested.kind,requested.snapshot_id,*tuple(identity),requested.policy_sha256)
            finally:
                connection.close()
            stack=self._body_context.get()
            if stack is None:
                raise PublicationCommitIntegrityError('Publication body preparation needs its owning call context.')
            prepared=stack.enter_context(self._public_bodies.prepare(binding,targets))
            return self._commits.compose(own,requested,own_identity,prepared,targets)
        except PublicationCommitConflictError as exc:
            raise PublicationPreviewAuthorizationError(str(exc)) from exc
        except PublicationCommitIntegrityError as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc
