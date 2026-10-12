"""Authorized public reuse-body preparation for the successor composer.

Filesystem verification happens after closing the read transaction. The context
keeps the Windows no-replace/no-delete lease through the caller's commit/rollback.
Writer revalidation uses metadata and a process-bound immutable-string seal, not
file I/O or full-body hashing. No prior private candidate/manifest is read.

This does not select a new requested candidate or commit publication. The owning
publisher must still validate its own complete candidate, exact preview target
mode/role episode and immutable reuse receipt before consuming the upload.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import hmac
import secrets

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, db_now_ms, transaction
from ..repositories.artifacts import StagedVerifiedBlob
from ..repositories.publication import PublicationPreviewRepository
from ..repositories.publication_commit import PublicationCommitConflictError, PublicationCommitIntegrityError
from ..repositories.published_upload_history import (
    PreparedPublishedUploadHistory, PublishedUploadHistoryRepository as History,
)
from ..scope import Capability, ScopeResolver, ScopeResolutionError, SessionPrincipal


@dataclass(frozen=True, slots=True)
class PreparedPublishedUploadBody:
    history: PreparedPublishedUploadHistory
    markdown: str | None = field(repr=False)
    authority_fence: tuple
    token: str = field(repr=False)
    seal: str = field(repr=False)


class CanonicalPublishedUploadBodyService:
    def __init__(self,factory,scope_resolver,verifier):
        if not isinstance(factory,ConnectionFactory) or not isinstance(scope_resolver,ScopeResolver):
            raise TypeError('Public body preparation requires canonical storage and scope resolution.')
        if not callable(getattr(verifier,'verify_existing',None)):
            raise TypeError('Public body preparation requires an existing-blob verifier.')
        self._factory,self._scope,self._verifier = factory,scope_resolver,verifier
        self._previews = PublicationPreviewRepository()
        self._key = secrets.token_bytes(32)
        self._active = set()

    def _authority(self,connection,binding,targets):
        preview = self._previews.require_for_actor(connection,preview_id=binding.preview_id,
            actor_user_id=binding.actor_user_id)
        if (preview.status!='ready' or db_now_ms(connection)>=preview.expires_at_ms
                or (preview.artifact_id,preview.extraction_snapshot_id,preview.source_snapshot_sha256)
                   != (binding.artifact_id,binding.extraction_snapshot_id,binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError('The current upload no longer authorizes body preparation.')
        principal = SessionPrincipal(user_id=binding.actor_user_id,
            captured_scope_epoch=preview.captured_scope_epoch)
        fence = []
        try:
            for target in sorted(targets,key=lambda t:t.space_id):
                resolved = self._scope.resolve_in_transaction(connection,principal,target.space_id,Capability.PUBLISH)
                plan = resolved.access_plan
                if (resolved.space.kind!=target.kind or plan is None
                        or plan.native_mutation_space_id!=target.space_id
                        or target.kind=='part' and resolved.current_part_id!=preview.captured_part_id):
                    raise PublicationCommitConflictError('The public reuse target is not currently authorized.')
                fence.append((target.space_id,resolved.scope_epoch,resolved.actor_row_version,
                    resolved.current_part_id,plan.role_assignment_id,plan.role_assignment_generation,
                    plan.corpus_closure.organization_snapshot_version))
        except ScopeResolutionError as exc:
            raise PublicationCommitConflictError('Current scope does not authorize public body reuse.') from exc
        return (preview.row_version,tuple(fence))

    def _seal(self,prepared):
        # The retained str is immutable; its identity avoids re-hashing MB-sized
        # text while holding a writer. This capability is never persisted.
        value = (prepared.history,prepared.authority_fence,prepared.token,id(prepared.markdown))
        return hmac.digest(self._key,repr(value).encode('utf-8'),'sha256').hex()

    @contextmanager
    def prepare(self,binding,authorized_targets):
        targets = tuple(authorized_targets)
        lease = None
        token = secrets.token_hex(24)
        try:
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    authority = self._authority(connection,binding,targets)
                    history = History.prepare(connection,binding,targets)
                    selected = history.selected
                    body = (History.load_selected_body(connection,binding,targets,history)
                        if selected is None or selected.storage.blob_sha256 is None else None)
            finally:
                connection.close()
            if selected is not None and selected.storage.blob_sha256 is not None:
                storage,identity = selected.storage,selected.body
                observation,raw = self._verifier.verify_existing(expected_sha256=identity.raw_sha256,
                    expected_size=identity.size_bytes,media_type=storage.blob_media_type,
                    normalized_content_sha256=identity.normalized_sha256,normalizer_version=identity.normalizer_version)
                lease = getattr(observation,'lease',None)
                blob = getattr(observation,'blob',None)
                if (type(raw) is not bytes or not isinstance(blob,StagedVerifiedBlob)
                        or (blob.sha256,blob.size_bytes,blob.media_type,blob.relative_path)
                           != (identity.raw_sha256,identity.size_bytes,storage.blob_media_type,storage.blob_relative_path)
                        or type(blob.size_bytes) is not int
                        or type(blob.created_at_ms) is not int or blob.created_at_ms!=0
                        or type(blob.verified_at_ms) is not int or blob.verified_at_ms!=0
                        or not callable(getattr(lease,'close',None))):
                    raise PublicationCommitIntegrityError('Public body verifier returned inconsistent evidence.')
                try:
                    body = raw.decode('utf-8')
                except UnicodeError as exc:
                    raise PublicationCommitIntegrityError('The public body is not valid UTF-8.') from exc
                History.validate_body(body,identity)
            self._active.add(token)
            prepared = PreparedPublishedUploadBody(history,body,authority,token,'')
            prepared = replace(prepared,seal=self._seal(prepared))
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    self.revalidate_in_transaction(connection,prepared)
            finally:
                connection.close()
            yield prepared
        finally:
            self._active.discard(token)
            closer = getattr(lease,'close',None)
            if callable(closer):
                closer()

    def revalidate_in_transaction(self,connection,prepared):
        if (not isinstance(connection,ManagedConnection) or not connection.in_transaction
                or connection.database_path!=self._factory.database_path):
            raise PublicationCommitIntegrityError('Public body revalidation requires its canonical transaction.')
        if type(prepared) is not PreparedPublishedUploadBody or prepared.token not in self._active:
            raise PublicationCommitConflictError('Public body preparation is foreign or closed.')
        try:
            valid = type(prepared.seal) is str and hmac.compare_digest(prepared.seal,self._seal(prepared))
        except (AttributeError,TypeError,ValueError,UnicodeError):
            valid = False
        if not valid:
            raise PublicationCommitIntegrityError('Public body preparation changed after verification.')
        history = prepared.history
        if self._authority(connection,history.binding,history.targets)!=prepared.authority_fence:
            raise PublicationCommitConflictError('Public body authority changed after preparation.')
        History.revalidate(connection,history.binding,history.targets,history)
        return prepared
