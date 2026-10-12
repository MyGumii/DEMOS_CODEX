"""Immutable server-owned requested body identity, separate from reused output.

Read after actor/preview authorization. This does not establish publishability;
the ordinary selector, complete body validation and writer fences still apply.
No purgeable/private body is read, including on exact committed replay.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from ..wiki_authoring import AUTHORING_POLICY_VERSION
from ..wiki_preservation import AUTHORING_ONLY_VERSIONS, PRESERVATION_VERSION
from .publication_commit import PublicationCommitIntegrityError
from ..migrations.v032_native_display_storage import DISPLAY_POLICY


class RequestedBodyPendingError(PublicationCommitIntegrityError):
    pass


class RequestedBodyUnavailableError(PublicationCommitIntegrityError):
    pass


@dataclass(frozen=True, slots=True)
class RequestedPublicationBody:
    kind: str
    snapshot_id: str
    extraction_snapshot_id: str
    source_sha256: str
    run_id: str | None
    policy_version: str
    policy_sha256: str | None
    plan_sha256: str | None = None

    def request_sha256(self, intent_sha256):
        encoded = json.dumps(dict(schema_version=2,operation_type="publication",
            intent_sha256=intent_sha256,requested_body=asdict(self)),ensure_ascii=False,
            sort_keys=True,separators=(',',':')).encode('utf-8')
        return sha256(encoded).hexdigest()


def has_pptx_role_work(connection, preview_id):
    for table in ('pptx_role_workflow_request', 'pptx_role_authoring_run'):
        if (connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
                and connection.execute(f'SELECT 1 FROM {table} WHERE preview_id=?', (preview_id,)).fetchone()):
            return True
    return False


def has_pdf_work(connection, preview_id):
    # Storage-only v39 runs are also owned. Never adopt one as raw extraction
    # merely because it predates the durable worker/publication successor.
    for table in ('pdf_workflow_request', 'pdf_authoring_run'):
        if (connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
                and connection.execute(f'SELECT 1 FROM {table} WHERE preview_id=?', (preview_id,)).fetchone()):
            return True
    return False


def has_pdf_visual_work(connection, preview_id):
    # Storage-only v43 runs are also owned. Never adopt one as raw extraction
    # merely because it predates the durable worker/publication successor.
    for table in ('pdf_visual_workflow_request', 'pdf_visual_authoring_run'):
        if (connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
                and connection.execute(f'SELECT 1 FROM {table} WHERE preview_id=?', (preview_id,)).fetchone()):
            return True
    return False


def requested_publication_body(connection, *, actor_id, preview_id, authored_snapshot_id=None):
    preview = connection.execute("""SELECT p.artifact_id,p.extraction_snapshot_id,p.source_snapshot_sha256,
        p.durable_job_id,a.origin_type,x.status,x.extracted_sha256,j.job_type,j.handler_version,
        p.status AS preview_status
        FROM publication_preview p JOIN source_artifact a ON a.id=p.artifact_id
        LEFT JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=p.artifact_id
        LEFT JOIN durable_job j ON j.id=p.durable_job_id
        WHERE p.id=? AND p.actor_user_id=?""",(preview_id,actor_id)).fetchone()
    if preview is None:
        raise RequestedBodyUnavailableError("The requested publication preview is unavailable.")
    if preview[4]!='upload':
        return None  # Manual/chat/Notion intent and historical hashes stay intact.
    artifact,extraction,source,parent = tuple(preview)[:4]
    if extraction is None or preview[5]!='ready' or source!=preview[6]:
        raise RequestedBodyPendingError("The upload has no completed extraction identity yet.")
    if has_pdf_continuation_work(connection, preview_id):
        if authored_snapshot_id is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace a PDF continuation.")
        return _continuation_requested(connection, actor_id, preview_id, artifact, extraction, source, preview["preview_status"])
    if tuple(preview)[7:9] == ('publication_preview.extract', 'v3'):
        registered = connection.execute("""SELECT 1 FROM office_hybrid_upload_request
            WHERE preview_id=? AND parent_job_id=? AND actor_user_id=?""",
            (preview_id, parent, actor_id)).fetchone()
        if registered is None:
            raise PublicationCommitIntegrityError(
                "The Office hybrid upload lacks its server-owned request.")
        body_kind_registered = connection.execute("""SELECT 1 FROM sqlite_schema
            WHERE type='view' AND name='office_hybrid_registered_publication'""").fetchone()
        if body_kind_registered is None:
            # v56/v57 retain a complete draft or candidate without owning a
            # public body kind. Never silently publish raw extraction.
            raise RequestedBodyUnavailableError(
                "Office hybrid Wiki is not yet registered for publication.")
        if authored_snapshot_id is not None:
            raise PublicationCommitIntegrityError(
                "A client snapshot cannot replace the Office Wiki candidate.")
        candidate = connection.execute("""SELECT snapshot_id,job_id,actor_user_id,
                artifact_id,extraction_snapshot_id,source_snapshot_sha256,
                workflow_version,policy_sha256,native_plan_sha256
            FROM office_hybrid_publication_candidate WHERE preview_id=?""",
            (preview_id,)).fetchone()
        if candidate is None and preview["preview_status"] == 'consumed':
            # Exact committed retries use the permanent body selection, not
            # the ready-only candidate view. The preview consume trigger
            # requires this immutable receipt in the same transaction.
            candidate = connection.execute("""SELECT s.snapshot_id,s.job_id,
                    s.actor_user_id,s.artifact_id,s.extraction_snapshot_id,
                    p.source_snapshot_sha256,s.workflow_version,s.policy_sha256,
                    s.native_plan_sha256
                FROM office_hybrid_publication_snapshot s
                JOIN publication_preview p ON p.id=s.preview_id
                JOIN office_hybrid_registered_publication registered
                  ON registered.snapshot_id=s.snapshot_id
                JOIN publication_operation operation
                  ON operation.id=registered.publication_operation_id
                JOIN operation_dedup dedup ON dedup.id=operation.dedup_id
                  AND dedup.status='committed'
                WHERE s.preview_id=? AND s.actor_user_id=?
                  AND operation.artifact_id=s.artifact_id
                  AND operation.extraction_snapshot_id=s.extraction_snapshot_id
                  AND operation.actor_user_id=s.actor_user_id""",
                (preview_id, actor_id)).fetchone()
        if candidate is None:
            writer = connection.execute("""SELECT j.state FROM office_hybrid_wiki_run r
                JOIN durable_job j ON j.id=r.job_id WHERE r.preview_id=?""",
                (preview_id,)).fetchone()
            if writer is None or writer[0] in {"queued", "claimed", "running", "retry"}:
                raise RequestedBodyPendingError(
                    "The requested Office Wiki candidate is still pending.")
            raise RequestedBodyUnavailableError(
                "The requested Office Wiki candidate is unavailable.")
        if (tuple(candidate)[2:6] != (actor_id, artifact, extraction, source)
                or candidate[0] != preview_id):
            raise PublicationCommitIntegrityError(
                "The Office Wiki candidate is not bound to this upload.")
        from ..migrations.v056_office_hybrid_wiki import VERSION
        if candidate[6] != VERSION:
            raise PublicationCommitIntegrityError(
                "The Office Wiki candidate policy is unsupported.")
        identity = connection.execute("""SELECT kind,snapshot_id FROM
            publication_body_identity WHERE kind='office_hybrid_wiki'
              AND snapshot_id=?""", (preview_id,)).fetchall() if preview["preview_status"] == 'consumed' else connection.execute(
            """SELECT kind,snapshot_id FROM publication_requested_body_candidate
                WHERE preview_id=?""", (preview_id,)).fetchall()
        if [tuple(row) for row in identity] != [("office_hybrid_wiki", preview_id)]:
            raise PublicationCommitIntegrityError(
                "The Office Wiki candidate is not the exclusive upload body.")
        return RequestedPublicationBody("office_hybrid_wiki", preview_id,
            extraction, source, candidate[1], VERSION, candidate[7], candidate[8])
    native = connection.execute("""SELECT q.parent_job_id,q.policy_sha256,r.id,r.actor_user_id,r.artifact_id,
        r.extraction_snapshot_id,r.parent_job_id,state.status,s.id,
        json_extract(r.metadata_json,'$.policy_version')
        FROM native_workflow_request q LEFT JOIN native_authoring_run r ON r.preview_id=q.preview_id
        LEFT JOIN native_authoring_state state ON state.run_id=r.id
        LEFT JOIN native_wiki_snapshot s ON s.run_id=r.id WHERE q.preview_id=?""",(preview_id,)).fetchone()
    preserved = connection.execute("""SELECT q.parent_job_id,q.policy_sha256,r.id,r.actor_user_id,r.artifact_id,
        r.extraction_snapshot_id,r.parent_job_id,r.status,s.id,r.policy_version,r.source_sha256
        FROM publication_preservation_request q LEFT JOIN publication_preservation_run r ON r.preview_id=q.preview_id
        LEFT JOIN preserved_wiki_snapshot s ON s.run_id=r.id WHERE q.preview_id=?""",(preview_id,)).fetchone()
    authored = connection.execute("""SELECT r.id,r.actor_user_id,r.artifact_id,r.extraction_snapshot_id,
        r.status,s.id,r.policy_version,r.plan_sha256,r.source_sha256
        FROM publication_authoring_run r LEFT JOIN authored_wiki_snapshot s ON s.run_id=r.id
        WHERE r.preview_id=?""",(preview_id,)).fetchone()
    wiki_request = connection.execute("SELECT parent_job_id,policy_sha256 FROM publication_wiki_request WHERE preview_id=?",
        (preview_id,)).fetchone()
    native_run = connection.execute('SELECT 1 FROM native_authoring_run WHERE preview_id=?',(preview_id,)).fetchone()
    preserved_run = connection.execute('SELECT 1 FROM publication_preservation_run WHERE preview_id=?',(preview_id,)).fetchone()
    role_work = has_pptx_role_work(connection, preview_id)
    pdf_work = has_pdf_work(connection, preview_id)
    visual_work = has_pdf_visual_work(connection, preview_id)
    pdf = None
    if pdf_work and connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pdf_workflow_request'").fetchone():
        pdf = connection.execute("""SELECT q.parent_job_id,q.policy_sha256,r.id,r.actor_user_id,r.artifact_id,
            r.extraction_snapshot_id,r.parent_job_id,state.status,s.id,
            json_extract(r.metadata_json,'$.policy_version'),r.source_plan_sha256
            FROM pdf_workflow_request q LEFT JOIN pdf_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_authoring_state state ON state.run_id=r.id
            LEFT JOIN pdf_wiki_snapshot s ON s.run_id=r.id WHERE q.preview_id=?""", (preview_id,)).fetchone()
    visual = None
    if visual_work and connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pdf_visual_workflow_request'").fetchone():
        visual = connection.execute("""SELECT q.parent_job_id,q.policy_sha256,r.id,r.actor_user_id,r.artifact_id,
            r.extraction_snapshot_id,r.parent_job_id,state.status,s.id,
            json_extract(r.metadata_json,'$.policy_version'),r.source_plan_sha256,
            json_extract(r.metadata_json,'$.prompt_sha256')
            FROM pdf_visual_workflow_request q LEFT JOIN pdf_visual_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_visual_authoring_state state ON state.run_id=r.id
            LEFT JOIN pdf_visual_wiki_snapshot s ON s.run_id=r.id WHERE q.preview_id=?""", (preview_id,)).fetchone()
    role = None
    if role_work and connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pptx_role_workflow_request'").fetchone():
        role = connection.execute("""SELECT q.parent_job_id,q.policy_sha256,r.id,r.actor_user_id,r.artifact_id,
            r.extraction_snapshot_id,r.parent_job_id,state.status,s.id,
            json_extract(r.metadata_json,'$.policy_version'),r.source_plan_sha256
            FROM pptx_role_workflow_request q LEFT JOIN pptx_role_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pptx_role_authoring_state state ON state.run_id=r.id
            LEFT JOIN pptx_role_wiki_snapshot s ON s.run_id=r.id WHERE q.preview_id=?""", (preview_id,)).fetchone()
    if (preview[7:9]==('publication_preview.extract','v2') and native is None and role is None):
        raise PublicationCommitIntegrityError("Native v2 upload lacks its server-owned authoring request; raw publication is not allowed.")
    if ((native is None and native_run is not None) or (preserved is None and preserved_run is not None)
            or role_work and role is None or pdf_work and pdf is None or visual_work and visual is None):
        raise PublicationCommitIntegrityError("Requested authoring run lacks its upload request.")
    if sum((native is not None or native_run is not None,preserved is not None or preserved_run is not None,
            authored is not None or wiki_request is not None, role_work, pdf_work, visual_work))>1:
        raise PublicationCommitIntegrityError("Multiple authoring policies own the requested publication.")
    if visual is not None:
        from ..migrations.v095_pdf_numeric_append import POLICY_IDENTITIES, POLICY_V36
        if visual[9] == POLICY_V36:
            from ..migrations.pdf_numeric_append_catalog import SCHEMA_V95_MIGRATION
            if not connection.execute('SELECT 1 FROM schema_migration WHERE version=95 AND name=? AND checksum_sha256=?',
                    (SCHEMA_V95_MIGRATION.name, SCHEMA_V95_MIGRATION.checksum_sha256)).fetchone():
                raise PublicationCommitIntegrityError("Numeric append requires its verified v95 schema.")
        if (authored_snapshot_id is not None or visual[0] != parent
                or tuple(preview)[7:9] != ('publication_preview.extract', 'v1')):
            raise PublicationCommitIntegrityError("The PDF candidate is not an exact server-owned request.")
        if connection.execute("SELECT 1 FROM pdf_visual_workflow_stop WHERE preview_id=?", (preview_id,)).fetchone():
            raise RequestedBodyUnavailableError("The requested PDF Wiki workflow was stopped.")
        if visual[2] is None or visual[7] == 'pending':
            raise RequestedBodyPendingError("The requested PDF Wiki candidate is still pending.")
        if visual[7] != 'ready':
            raise RequestedBodyUnavailableError("The requested PDF Wiki candidate did not complete successfully.")
        if (tuple(visual)[3:7] != (actor_id, artifact, extraction, parent) or visual[8] is None
                or (visual[9], visual[11]) not in POLICY_IDENTITIES
                or not connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pdf_visual_publication_snapshot'").fetchone()
                or not connection.execute("SELECT 1 FROM pdf_visual_publication_snapshot WHERE snapshot_id=? AND run_id=? AND preview_id=? AND actor_user_id=?",
                    (visual[8], visual[2], preview_id, actor_id)).fetchone()):
            raise PublicationCommitIntegrityError("The requested PDF Wiki is not permanently registered to this upload.")
        return RequestedPublicationBody('pdf_visual_wiki', visual[8], extraction, source, visual[2], visual[9], visual[1], visual[10])
    if pdf is not None:
        from ..migrations.v039_pdf_authoring_storage import POLICY
        if (authored_snapshot_id is not None or pdf[0] != parent
                or tuple(preview)[7:9] != ('publication_preview.extract', 'v1')):
            raise PublicationCommitIntegrityError("The PDF candidate is not an exact server-owned request.")
        if connection.execute("SELECT 1 FROM pdf_workflow_stop WHERE preview_id=?", (preview_id,)).fetchone():
            raise RequestedBodyUnavailableError("The requested PDF Wiki workflow was stopped.")
        if pdf[2] is None or pdf[7] == 'pending':
            raise RequestedBodyPendingError("The requested PDF Wiki candidate is still pending.")
        if pdf[7] != 'ready':
            raise RequestedBodyUnavailableError("The requested PDF Wiki candidate did not complete successfully.")
        if (tuple(pdf)[3:7] != (actor_id, artifact, extraction, parent) or pdf[8] is None
                or pdf[9] != POLICY
                or not connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pdf_publication_snapshot'").fetchone()
                or not connection.execute("SELECT 1 FROM pdf_publication_snapshot WHERE snapshot_id=? AND run_id=? AND preview_id=? AND actor_user_id=?",
                    (pdf[8], pdf[2], preview_id, actor_id)).fetchone()):
            raise PublicationCommitIntegrityError("The requested PDF Wiki is not permanently registered to this upload.")
        return RequestedPublicationBody('pdf_wiki', pdf[8], extraction, source, pdf[2], pdf[9], pdf[1], pdf[10])
    if role is not None:
        from ..pptx_role_authoring import ROLE_AUTHORING_V2_VERSION
        if authored_snapshot_id is not None or role[0] != parent:
            raise PublicationCommitIntegrityError("The role candidate is not an exact server-owned request.")
        if role[2] is None or role[7] == 'pending':
            raise RequestedBodyPendingError("The requested role Wiki candidate is still pending.")
        if role[7] != 'ready':
            raise RequestedBodyUnavailableError("The requested role Wiki candidate did not complete successfully.")
        if (tuple(role)[3:7] != (actor_id, artifact, extraction, parent) or role[8] is None
                or role[9] != ROLE_AUTHORING_V2_VERSION
                or not connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pptx_role_publication_snapshot'").fetchone()
                or not connection.execute("SELECT 1 FROM pptx_role_publication_snapshot WHERE snapshot_id=? AND run_id=? AND preview_id=? AND actor_user_id=?",
                    (role[8], role[2], preview_id, actor_id)).fetchone()):
            raise PublicationCommitIntegrityError("The requested role Wiki is not permanently registered to this upload.")
        return RequestedPublicationBody('pptx_role_wiki', role[8], extraction, source, role[2], role[9], role[1], role[10])
    if native is not None or preserved is not None:
        if authored_snapshot_id is not None:
            raise PublicationCommitIntegrityError("A client-authored selection cannot replace the server-owned candidate.")
        row = native if native is not None else preserved
        if row[0]!=parent:
            raise PublicationCommitIntegrityError("Requested authoring parent is inconsistent.")
        if row[2] is None or row[7]=='pending':
            raise RequestedBodyPendingError("The requested Wiki candidate is still pending.")
        if row[7]!='ready':
            raise RequestedBodyUnavailableError("The requested Wiki candidate did not complete successfully.")
        if (tuple(row)[3:7]!=(actor_id,artifact,extraction,parent) or row[8] is None
                or (native is None and row[10]!=source)):
            raise PublicationCommitIntegrityError("Requested authoring identity is not bound to this upload.")
        supported = ('wiki-native-batch-authoring-v1',) if native is not None else (PRESERVATION_VERSION,*AUTHORING_ONLY_VERSIONS)
        # A forward, schema-verified registration is required. The policy name
        # alone must not adopt a retained standalone v32 result into publication.
        display = (native is not None and row[9] == DISPLAY_POLICY
            and connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
                "AND name='native_display_publication_snapshot'").fetchone() is not None
            and connection.execute("SELECT 1 FROM native_display_publication_snapshot "
                "WHERE snapshot_id=? AND run_id=? AND preview_id=? AND actor_user_id=?",
                (row[8], row[2], preview_id, actor_id)).fetchone() is not None)
        if row[9] not in supported and not display:
            raise PublicationCommitIntegrityError("Requested authoring policy is unsupported.")
        return RequestedPublicationBody('native_wiki' if native is not None else 'preserved_wiki',
            row[8],extraction,source,row[2],row[9],row[1])
    outcome = None
    if wiki_request is not None:
        if wiki_request[0]!=parent:
            raise PublicationCommitIntegrityError("Requested Wiki parent is inconsistent.")
        outcome = connection.execute("SELECT outcome,run_id FROM publication_wiki_source_result WHERE preview_id=?",
            (preview_id,)).fetchone()
        if outcome is None:
            raise RequestedBodyPendingError("The requested upload has no completed authoring decision.")
        if (authored is None and tuple(outcome)!=('extraction_only',None)
                or authored is not None and tuple(outcome)!=('planned',authored[0])):
            raise PublicationCommitIntegrityError("Authoring disposition is not explicit and source-bound.")
    if authored is not None:
        if wiki_request is None and authored_snapshot_id is None:
            # Without the public workflow selector, the base publisher would
            # choose extraction. Never hash an implicit authored candidate and
            # then publish a different body under that identity.
            raise PublicationCommitIntegrityError("Unregistered authored preparation requires its explicit internal selection.")
        if tuple(authored)[1:4]!=(actor_id,artifact,extraction) or authored[8]!=source:
            raise PublicationCommitIntegrityError("Requested authored identity is inconsistent.")
        if authored[4]=='pending':
            raise RequestedBodyPendingError("The requested authored Wiki is still pending.")
        if authored[4]=='ready':
            if (authored[5] is None or authored[6]!=AUTHORING_POLICY_VERSION
                    or authored_snapshot_id not in (None,authored[5])):
                raise PublicationCommitIntegrityError("Requested authored snapshot is not exact.")
            return RequestedPublicationBody('authored_wiki',authored[5],extraction,source,
                authored[0],authored[6],wiki_request[1] if wiki_request is not None else None,authored[7])
        if authored[4]!='extraction_only' or authored[5] is not None:
            raise RequestedBodyUnavailableError("The requested authored Wiki is unavailable.")
    if authored_snapshot_id is not None:
        raise PublicationCommitIntegrityError("The requested authored snapshot is missing.")
    return RequestedPublicationBody('extraction',extraction,extraction,source,
        authored[0] if authored is not None else None,'extraction',
        wiki_request[1] if wiki_request is not None else None)

def has_pdf_continuation_work(connection, preview_id):
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pdf_continuation_request'").fetchone() is not None and connection.execute(
        "SELECT 1 FROM pdf_continuation_request WHERE parent_preview_id=?", (preview_id,)).fetchone() is not None


def _continuation_requested(connection, actor_id, preview_id, artifact, extraction, source, status):
    rows = connection.execute("""SELECT * FROM pdf_continuation_publication_candidate
        WHERE preview_id=? AND actor_user_id=?""", (preview_id, actor_id)).fetchall()
    if not rows and status == 'consumed':
        rows = connection.execute("""SELECT s.* FROM pdf_continuation_publication_snapshot s
            JOIN pdf_continuation_registered_publication registered ON registered.snapshot_id=s.snapshot_id
            JOIN publication_operation operation ON operation.id=registered.publication_operation_id
            JOIN operation_dedup dedup ON dedup.id=operation.dedup_id AND dedup.status='committed'
            WHERE s.preview_id=? AND s.actor_user_id=? AND operation.actor_user_id=s.actor_user_id
              AND operation.artifact_id=s.artifact_id AND operation.extraction_snapshot_id=s.extraction_snapshot_id""",
            (preview_id, actor_id)).fetchall()
    if len(rows) != 1:
        pending = connection.execute("""SELECT 1 FROM pdf_continuation_request q
            JOIN pdf_continuation_state state ON state.request_id=q.id
            WHERE q.parent_preview_id=? AND q.actor_user_id=? AND state.status='pending'
              AND q.deadline_at>CAST(unixepoch('subsec')*1000 AS INTEGER)
              AND NOT EXISTS(SELECT 1 FROM pdf_continuation_stop stop WHERE stop.request_id=q.id)""",
            (preview_id, actor_id)).fetchone()
        if not rows and pending:
            raise RequestedBodyPendingError("The requested PDF continuation is still pending.")
        raise RequestedBodyUnavailableError("The requested PDF continuation has no single complete authorized candidate.")
    row = rows[0]
    if (row['artifact_id'], row['extraction_snapshot_id'], row['source_snapshot_sha256']) != (artifact, extraction, source):
        raise PublicationCommitIntegrityError("The continuation source identity differs from its upload.")
    return RequestedPublicationBody('pdf_continuation_wiki', row['snapshot_id'], extraction, source,
        row['request_id'], row['policy_version'], row['policy_sha256'], row['source_plan_sha256'])

