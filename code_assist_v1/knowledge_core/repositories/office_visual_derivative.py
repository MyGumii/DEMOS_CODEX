"""Retain an authorized Office original / rendered-PDF identity in v53.

Only the immutable identity is stored. The caller owns a verified original-CAS
lease and must keep it until its transaction completes. This repository does
not publish a Wiki or replace the later publication-time rights check.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256

from ..db import ManagedConnection, db_now_ms
from ..office_hybrid_source import PreparedOfficeHybridSource
from ..office_visual_source import PreparedOfficeVisualSource, OfficeVisualSourceError


class OfficeVisualDerivativeRepository:
    def __init__(self, *, export_authorizer: Callable[[str, str, str], bool],
            authorization_method: str):
        if not callable(export_authorizer):
            raise TypeError("A server-owned Office export authorizer is required.")
        if (type(authorization_method) is not str
                or authorization_method != authorization_method.strip()
                or not 1 <= len(authorization_method) <= 120):
            raise ValueError("An exact Office authorization method is required.")
        self._export_authorizer = export_authorizer
        self._authorization_method = authorization_method

    def retain(self, connection: ManagedConnection, *, preview_id: str,
            artifact_id: str, extraction_snapshot_id: str,
            actor_user_id: str, original_filename: str, original: bytes,
            prepared: PreparedOfficeVisualSource | PreparedOfficeHybridSource,
            created_at_ms: int | None = None) -> dict[str, object]:
        if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
            raise TypeError("Office derivative retention requires a managed writer transaction.")
        if type(prepared) is PreparedOfficeVisualSource:
            prepared.checked(actor_user_id=actor_user_id,
                original_filename=original_filename, original=original)
        elif type(prepared) is PreparedOfficeHybridSource:
            if prepared.render.original_filename != original_filename:
                raise OfficeVisualSourceError("The Office source filename changed.")
            prepared.checked(actor_user_id=actor_user_id, original=original)
        else:
            raise OfficeVisualSourceError("A process-local Office visual source is required.")
        digest = sha256(original).hexdigest()
        if self._export_authorizer(actor_user_id, original_filename, digest) is not True:
            raise PermissionError("Office export permission changed before retention.")
        render = prepared.render
        row: dict[str, object] = {
            "preview_id": preview_id,
            "artifact_id": artifact_id,
            "extraction_snapshot_id": extraction_snapshot_id,
            "actor_user_id": actor_user_id,
            "original_sha256": digest,
            "original_format": render.original_format,
            "derivative_sha256": render.derivative_sha256,
            "derivative_size_bytes": len(render.derivative_pdf),
            "expected_pages": render.expected_pages,
            "render_method": render.render_method,
            "authorization_method": self._authorization_method,
            "created_at": (db_now_ms(connection) if created_at_ms is None else created_at_ms),
        }
        columns = tuple(row)
        connection.execute(
            "INSERT INTO office_visual_derivative(" + ",".join(columns) + ") VALUES("
            + ",".join("?" for _ in columns) + ")",
            tuple(row[column] for column in columns),
        )
        return row


__all__ = ["OfficeVisualDerivativeRepository"]
