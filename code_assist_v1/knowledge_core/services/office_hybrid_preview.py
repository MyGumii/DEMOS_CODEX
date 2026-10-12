"""Opt-in v3 Office preview registration; no route or writer activation.

The immutable request separates native-plus-page authoring from historical v2
native-only uploads. Registration occurs in the same transaction as the queued
job and preview. A later v3 worker must consume this exact policy identity.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json

from ..db import db_now_ms
from ..migrations.office_hybrid_preview_catalog import OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG
from ..migrations.v055_office_hybrid_preview import POLICY, RULE
from ..pdf_visual_source import PdfPageRenderPolicy
from ..wiki_authoring import AuthoringModelPolicy
from ..worker import JobExpectedStateConflictError
from .native_authoring_workflow import NativeUploadPolicy
from .publication import PublicationPreviewService


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"))


def _hash(value):
    return sha256(value.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class OfficeHybridUploadPolicy:
    native: NativeUploadPolicy = field(default_factory=NativeUploadPolicy)
    visual_model: AuthoringModelPolicy = field(default_factory=lambda:
        AuthoringModelPolicy(max_attempts=1, request_budget_bytes=1_048_576))
    render: PdfPageRenderPolicy = field(default_factory=PdfPageRenderPolicy)

    def __post_init__(self):
        if (type(self.native) is not NativeUploadPolicy
                or type(self.visual_model) is not AuthoringModelPolicy
                or type(self.render) is not PdfPageRenderPolicy
                or self.visual_model.max_attempts != 1):
            raise ValueError("Office hybrid upload requires bounded server-owned policies.")
        if len(self.encoded().encode()) > 65536:
            raise ValueError("Office hybrid upload policy exceeds its storage budget.")

    def encoded(self):
        return _json({"version": POLICY,
            "native_policy": json.loads(self.native.encoded()),
            "visual_model_policy": asdict(self.visual_model),
            "render_policy": asdict(self.render)})

    @classmethod
    def restore(cls, encoded):
        """Restore only an exact stored policy, never a near-match fallback."""
        try:
            value = json.loads(encoded)
            if (type(value) is not dict
                    or set(value) != {"version", "native_policy",
                        "visual_model_policy", "render_policy"}
                    or value["version"] != POLICY):
                raise ValueError
            model = dict(value["visual_model_policy"])
            for key in ("provider_order", "model_ids"):
                if model.get(key) is not None:
                    model[key] = tuple(model[key])
            if model.get("wire_models") is not None:
                model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
            policy = cls(
                native=NativeUploadPolicy.restore(_json(value["native_policy"])),
                visual_model=AuthoringModelPolicy(**model),
                render=PdfPageRenderPolicy(**value["render_policy"]),
            )
            if policy.encoded() != encoded:
                raise ValueError
            return policy
        except (KeyError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError(
                "Retained Office hybrid policy is invalid.") from exc


class OfficeHybridUploadRegistration:
    def __init__(self, factory, *, policy=None):
        expected = OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid registration requires its verified v55 catalog.")
        self.factory = factory
        self.policy = OfficeHybridUploadPolicy() if policy is None else policy
        if type(self.policy) is not OfficeHybridUploadPolicy:
            raise TypeError("A server-owned Office hybrid policy is required.")

    @property
    def database_path(self):
        return self.factory.database_path

    def register_upload(self, connection, *, preview_id, parent):
        if not connection.in_transaction or connection.database_path != self.factory.database_path:
            raise JobExpectedStateConflictError("Office hybrid registration requires the upload transaction.")
        row = connection.execute("""SELECT p.id,p.actor_user_id,p.artifact_id,
                p.durable_job_id,p.status,p.rule_version,j.state,j.handler_version,j.created_at
            FROM publication_preview p JOIN durable_job j ON j.id=p.durable_job_id
            WHERE p.id=?""", (preview_id,)).fetchone()
        if (row is None or row["durable_job_id"] != parent.id
                or row["actor_user_id"] != parent.actor_user_id
                or row["status"] != "queued" or row["rule_version"] != RULE
                or row["state"] != "queued" or row["handler_version"] != "v3"):
            raise JobExpectedStateConflictError("Office hybrid request differs from its queued parent.")
        encoded = self.policy.encoded()
        connection.execute("""INSERT INTO office_hybrid_upload_request(
            preview_id,parent_job_id,actor_user_id,artifact_id,rule_version,
            policy_version,policy_sha256,policy_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (preview_id, parent.id, parent.actor_user_id, row["artifact_id"],
                RULE, POLICY, _hash(encoded), encoded, row["created_at"]),
        )


class OfficeHybridPreviewService(PublicationPreviewService):
    _upload_handler_version = "v3"
    _upload_rule_version = RULE

    def __init__(self, factory, scope_resolver, stager, *, registration, **kwargs):
        if (type(registration) is not OfficeHybridUploadRegistration
                or registration.factory.database_path != factory.database_path):
            raise TypeError("Office hybrid preview requires its same-database registrar.")
        self._office_registration = registration
        super().__init__(factory, scope_resolver, stager,
            wiki_upload=registration, **kwargs)

    def _upload_request_sha256(self, **fields):
        parent = super()._upload_request_sha256(**fields)
        return _hash(_json({"legacy_request_sha256": parent,
            "handler_version": "v3", "rule_version": RULE,
            "policy_sha256": _hash(self._office_registration.policy.encoded())}))

    def _upload_expected_state(self, state):
        return {**state, "handler_version": "v3", "schema_version": 3}


__all__ = ["OfficeHybridUploadPolicy", "OfficeHybridUploadRegistration",
    "OfficeHybridPreviewService"]
