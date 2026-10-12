# Wiki files and shared feedback — approved implementation amendment

Approved in this task on 2026-09-06/07. This is an additive application feature
packet, not a claim that the historical company-provider or production gates
have completed. Existing canonical schema versions and existing API operation
IDs remain unchanged. No ranking, retrieval, graph scoring or LLM prompt changes
are part of this packet.

## Product behavior

- Knowledge rows show the title, Markdown download, original download, and an
  original-link action for the exact native editor. Missing original provenance
  is distinguished from a metadata request failure. The inaccurate `직접 작성`
  badge is removed from these rows. Original downloads move out of the body modal;
  answer citations continue to use the existing authorized source endpoint.
- The existing Graph detail card moves beside the selected Wiki heading. The
  adjacent whole-Markdown button exports the selected view, not all personal
  Wikis and not just the current page. Team/Responsibility backend support does
  not enable their intentionally disabled frontend selectors.
- Export packages contain every currently visible active/published Wiki body,
  Index, Log, revision/source-space provenance, and retained canonical image
  attachments. Each document gets a title-plus-UUID folder, preventing collisions.
  Raw PDF/PPT/Word files are obtained separately using the original buttons.
- An original link preserves the existing item, immutable body/revision and
  graph. Deterministic native extraction may record original text; it performs
  no LLM conversion or Wiki publication. Failed extraction does not discard raw
  source bytes. Identical retained source bytes do not create duplicate links.
- Original recovery by automation requires evidence in historical upload records
  or a historical original-file digest. Filename/title similarity is not proof.
  A user can explicitly identify the source via the original-link dialog.
- The top-right duplicate new-session button becomes `버그·개선 요청`. The
  session-sidebar plus continues to create sessions.
- Every active authenticated account can list/read feedback and download its
  attachments. Only the author can edit/delete their post; an administrator
  can independently change received/reviewing/completed status. Admin status
  does not grant editing/deletion of another author's content.
- The modal supports list/detail/create/edit, image paste, multiple dropped or
  selected files, previews/removal, and returns to the list after creation.
  Editing returns to the detail. Submission failures retain the in-memory draft.
  Deletion is logical, including denial of retained attachment URLs.
- Every reader, including the author, has `← 게시판으로` both above the post
  content and in the modal footer. Returning shows the shared list without
  closing the modal; editing/deletion permissions do not govern navigation.
- The board displays compact title/requester/status/date rows. Previous/next
  replaces the current 30-row page instead of appending rows. The footer keeps
  navigation available while the list scrolls. Returning from a post restores
  the applied search/filters, page, scroll and focused row in the current browser
  page. Changing criteria resets pagination; creating a new post resets to the
  unfiltered first page so the new post is visible. An emptied last page falls
  back to the nearest remaining earlier page.
- Search defaults to title + body + requester display name/account ID. Users may
  select title-only, body-only or requester-only search. Each whitespace-separated
  term must match a selected field; terms are literal substrings, including `%`
  and `_`. The API accepts at most 200 characters / 8 terms. Requester names are
  the attribution recorded at posting, and the stable account ID is searchable.
- Type, received/reviewing/completed status and `내가 쓴 글만` are combinable
  server-side filters over the full visible board, not only the loaded page.
  The mine filter is bound to the authenticated account, not a client author ID.

## Data boundaries

Original links reuse `source_artifact`, raw CAS blobs, real extraction snapshots,
and `item_source(role=evidence)`. Scope/role, active item and expected revision
are revalidated in the immediate transaction. Source, provenance, audit, outbox,
generation invalidation and actor/operation-id dedup commit atomically. Physical
file staging, extraction and hashing occur outside that transaction.

Feedback is not Wiki content. Its versioned auxiliary SQLite database is
`<canonical-core-directory>/feedback.db`. Schema v1 contains post,
attachment (verified original bytes), history and operation tables. It is
initialized during application composition, with serialized startup migration,
WAL and foreign keys. Posts, bytes, history and replay result commit together.
Edits/deletes/status changes compare `row_version`; retries are actor-scoped
and a reused operation ID with different contents is rejected. Include this
database in deployment backup/restore and data-root transfer procedures.

Board queries use the existing creation-time/ID keyset and retain deleted cursor
anchors. No COUNT/OFFSET or all-post client rendering is required. List responses
contain only one metadata page and attachment metadata, not bodies or file bytes.
Literal substring search currently uses parameterized SQLite LIKE predicates;
matching may scan stored text as the corpus grows. This is bounded response/DOM
memory, not a claim of constant-time text search or a full-text-index benchmark.
No schema migration is introduced for this search/pagination extension.

Exports capture the selected-view closure, complete keyset-paginated document
inventory and activity snapshot. Bodies are read one at a time outside write
transactions and packed into a disk-backed temporary ZIP. Authority and the
complete snapshot are rechecked before response. Missing bodies, failed retained
asset verification, or changed inventory/closure cause an error, not a partial
successful archive. The browser streams bulk ZIP responses using its download
manager rather than buffering the ZIP in JavaScript. Exports are downloads for
reading, not signed, trusted temporal re-import envelopes.

## Additive API surface

All paths require the current authenticated canonical account. Mutations retain
the application's existing origin/session/CSRF checks. Filenames are sanitized
and attachments are sent with private/no-store, nosniff and attachment headers.
Upload bytes obey the configured `MAX_UPLOAD_MB` limit.

| Method | Path | Input / result |
|---|---|---|
| GET | `/api/knowledge/v2/spaces/{space_id}/markdown-export` | Complete ZIP; `X-Wiki-Document-Count` |
| GET | `/api/knowledge/v2/spaces/{space_id}/items/{item_id}/markdown-download` | Current authorized Markdown attachment |
| POST | `/api/knowledge/v2/spaces/{space_id}/source-availability` | JSON `item_ids` (max 100); map item ID to retained source count |
| POST | `/api/knowledge/v2/spaces/{space_id}/items/{item_id}/original-links` | Multipart `files` (one), `operation_id`, `expected_revision_id`; artifact/item/status |
| GET | `/api/code/feedback` | Optional `cursor`, `q`, `field=all/title/body/requester`, `kind`, `status`, `mine=0/1`; 30 rows and `next_cursor` |
| POST | `/api/code/feedback` | Multipart title/kind/body/operation_id/files/keep_ids; ID/version/action |
| GET | `/api/code/feedback/{post_id}` | Body, author/times/status/version, attachments and capabilities |
| PUT | `/api/code/feedback/{post_id}` | Multipart create fields plus expected_version and retained attachment IDs |
| DELETE | `/api/code/feedback/{post_id}` | JSON operation_id/expected_version; logical deletion |
| PATCH | `/api/code/feedback/{post_id}/status` | Admin JSON operation_id/expected_version/status |
| GET | `/api/code/feedback/{post_id}/attachments/{attachment_id}` | Retained, visible-post attachment bytes |

These 8 paths / 11 operations form this explicitly registered extension; they
are separate from the frozen 72-path/80-operation historical knowledge contract.
The combined mounted feature surface has 80 paths / 91 operations when both
packages are enabled. Its exact route signatures are checked against the runtime
by this packet's tests rather than changing past baseline hashes.

## Acceptance and evidence

| Scenario | Required evidence |
|---|---|
| WF-001 | Link original, retry and repeat-content dedup; unchanged revision/body/graph; exact downloaded bytes; foreign account denied |
| WF-002 | More than 100 same-title documents export without omission/collision; Index/Log present; foreign-space data excluded |
| WF-003 | Actual browser individual/whole/original downloads and relocated Graph controls |
| WF-004 | Two accounts share a post with pasted image/dropped file; only author edits/deletes; removed attachment URL denied |
| WF-005 | Reopen database/reload, mutation retries, stale version conflict, draft preservation and authentication denial |
| WF-006 | Title/body/requester-name/ID search, literal wildcard escaping, combined type/status/mine filters, bounded complete pages and deleted cursor anchors |
| WF-007 | Browser page replacement, condition reset, return with page/scroll/focus restoration, and fallback after a final page becomes empty |

Automated tests: `tests/knowledge_spaces/unit/test_workspace_features.py`,
existing source-artifact routes and browser transport regressions.
Opt-in isolated browser runner: `tools/verify_workspace_features_browser.py`.
Its CLI restricts use to port 10221. It creates a labeled feedback post and
logically deletes it through the author UI; it only reads existing Wiki data.

Implementation/testing results and limits are recorded in
`evidence/WORKSPACE_FEATURES_20260907.md` after verification.
The search/filter/pagination follow-up is verified separately in
[FEEDBACK_SEARCH_PAGINATION_20260907.md](evidence/FEEDBACK_SEARCH_PAGINATION_20260907.md).
