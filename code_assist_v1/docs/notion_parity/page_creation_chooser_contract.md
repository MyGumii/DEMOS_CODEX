# Page creation chooser — partial R3/R10 implementation

The sidebar bottom action has Page, Chat, AI notes and Database entries, filtered
by installed server capabilities. Page creates immediately. Chat opens the
existing unsent chat view. AI notes creates one existing `ai_notes` block without
starting capture or a model request. Database opens the creation chooser. A team
plus opens the chooser with that current team selected.

The chooser exposes only destinations currently authorized by the existing
personal/team creation rules. It supports search, keyboard entry, Escape and
return to the current trigger, including a sidebar redraw. Scope, navigation
generation, destination and permission are checked again at submission. A stale
or already submitted chooser cannot create another document.

`POST /api/notion/workspaces/{workspace}/pages` optionally accepts one server-owned
`template`: `page`, `database`, `linked_database`, `task_tracker`, `meeting_notes`, `ai_notes`. Unknown
keys/types/IDs are rejected. The original empty-page request/hash is unchanged.
Non-default template identity participates in the original immutable operation
hash, and retries retain destination, template and the initial title.

The existing page creation service transaction commits blocks, initial database
schema, revision/checkpoint, native location, parent link, audit, outbox and replay
receipt together. Database snapshots include schema before computing the first
content hash. No client-provided block payload or SQL is accepted. No new schema
migration is needed; exact existing capability markers remain required. No
production migration or feature flag was applied for this packet.

Templates are deliberately bounded: a blank database has one title property;
the task tracker adds status/date properties and table/board views; meeting notes
has eight heading/list/text/task blocks. They do not reproduce Notion's full
template catalog, example content or the database-based reference meeting log.
The `linked_database` card searches currently readable sources in the selected
workspace. Selecting one creates a page with a local table view storing only
source document/database IDs and view configuration. The transaction rechecks
source visibility and active state; no source rows or properties are copied.
Source access is checked again when the linked view is read. Source identities
participate in the immutable create request and retry hash. Choosing an
existing source view and reproducing Notion's full template catalog remain
unimplemented. The same-file schema version now advertises page templates,
and database template creation passes the required actor to its schema writer.

Chrome verification exposed the old database New action using the legacy page
endpoint. Collaborative rows now go through the existing canonical database
sync commands, retain a stable row identity across a failed creation attempt,
and focus their title cell. Row creation and subsequent cell commands are the
existing separate transactions; this is not a new atomic row-plus-body contract.
Inline title/status edits and board projection survive reload. Database item
detail routing and editor work continued in later R10 packets; this chooser
contract alone does not declare database parity.

Evidence: `outputs/Notion_UI_생성창_검증_20260916.md` in the task workspace;
service/HTTP template tests, chooser/native-row UI tests and actual Chrome CUA.
The 2026-09-29 linked-source follow-up additionally passed a full isolated
Chrome path from blank database creation through linking it on another page,
with source data unchanged and 390px viewport containment. Server tests cover
exact replay, missing/mismatched sources, atomic failure, team source access,
and read denial after revocation.
The original R1–R11, copy and synced-block completion scope remains open.
