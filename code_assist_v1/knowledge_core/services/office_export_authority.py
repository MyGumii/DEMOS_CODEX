"""Local ordinary-OOXML export authority, not a company DRM decision.

The Windows Office renderer may use this only for unprotected DOCX/PPTX.
It binds actor, exact retained upload, active preview and current publish scope.
Protected files still need their own authorized opener/provider policy.
"""

from __future__ import annotations

import time

from ..db import TransactionMode, db_now_ms, transaction
from ..errors import KnowledgeCoreSchemaMismatchError
from ..scope import Capability, SessionPrincipal


class CanonicalOrdinaryOfficeExportAuthority:
    def __init__(self, factory, scope_resolver):
        if (factory is None or not callable(getattr(factory, "open", None))
                or not callable(getattr(scope_resolver,
                    "resolve_in_transaction", None))):
            raise TypeError("Office export authority needs canonical DB and scope.")
        self.factory = factory
        self.scope_resolver = scope_resolver

    def __call__(self, actor_user_id: str, original_filename: str,
            original_sha256: str) -> bool:
        if (type(actor_user_id) is not str or not actor_user_id
                or type(original_filename) is not str or not original_filename
                or type(original_sha256) is not str
                or len(original_sha256) != 64
                or any(value not in "0123456789abcdef" for value in original_sha256)):
            return False
        # A failed physical-schema observation must never grant export.  One
        # fresh, fully validated connection can distinguish a transient read
        # from an actual denial without retaining the failed WAL snapshot.
        for attempt in range(2):
            if attempt:
                time.sleep(0.05)
            try:
                connection = self.factory.open(read_only=True)
            except KnowledgeCoreSchemaMismatchError:
                if attempt == 0:
                    continue
                return False
            except Exception:
                return False
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    rows = connection.execute("""SELECT p.captured_scope_epoch,
                        parent.space_id
                    FROM publication_preview p
                    JOIN office_hybrid_upload_request q ON q.preview_id=p.id
                    JOIN source_artifact a ON a.id=p.artifact_id
                    JOIN durable_job parent ON parent.id=p.durable_job_id
                    JOIN app_user u ON u.id=p.actor_user_id
                    WHERE p.actor_user_id=? AND a.publisher_user_id=?
                      AND a.original_filename=? AND a.raw_sha256=?
                      AND a.body_retention_status='present'
                      AND p.status IN ('queued','extracting','ready')
                      AND p.expires_at>? AND q.parent_job_id=parent.id
                      AND parent.actor_user_id=p.actor_user_id
                      AND parent.job_type='publication_preview.extract'
                      AND parent.handler_version='v3'
                      AND parent.state NOT IN ('failed','dead','cancelled')
                      AND parent.cancel_requested=0
                      AND u.account_status='active'
                      AND u.scope_epoch=p.captured_scope_epoch""",
                        (actor_user_id, actor_user_id, original_filename,
                            original_sha256, db_now_ms(connection))).fetchall()
                    for row in rows:
                        try:
                            self.scope_resolver.resolve_in_transaction(connection,
                                SessionPrincipal(actor_user_id,
                                    row["captured_scope_epoch"]),
                                row["space_id"], Capability.PUBLISH)
                            return True
                        except KnowledgeCoreSchemaMismatchError:
                            raise
                        except Exception:
                            continue
            except KnowledgeCoreSchemaMismatchError:
                if attempt == 0:
                    continue
                return False
            except Exception:
                return False
            finally:
                connection.close()
            return False
        return False


__all__ = ["CanonicalOrdinaryOfficeExportAuthority"]
