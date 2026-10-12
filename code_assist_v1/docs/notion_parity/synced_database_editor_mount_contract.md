# Owned database body synced editor mounts

Status (2026-10-03): staged owned row and editable template bodies now mount
explicit synced references through the existing authenticated read/text APIs.
This follows `synced_database_text_contract.md`. Physical schemas, runtime flags
and collaboration activation are unchanged. The whole goal remains active.

## Read and edit scope

Each body owns independent read observers and text slots, bound to actor, realm,
workspace, physical owner page, current navigation/data object and exact row or
template location. Server-returned instance storage must match that body. A
reference owns no cached content/children. Canonical original roots and ordinary
siblings retain the generic body editor; source text inside a reference uses
the existing encrypted draft journal, witnesses and canonical synced writer.
Nested references stop the materialized content boundary. No slash-menu creation
or new partial-copy semantics are enabled by this mount.

The shared text DOM hooks accept a per-body controller/current predicate/file
scope while main-page hooks still resolve their lazily initialized controller.
IME events stay in the synced editor. Role denial redacts content and retains
encrypted input; role downgrade makes the authorized read-only span noneditable.
The row browser cases prove edit_content to view in a second authenticated
session, with ordinary fields also read-only and canonical body unchanged.

## Saving and DOM preservation

Ordinary dirty/composing/request state disables new reference input. Outstanding
reference input disables ordinary body fields; template name/default controls
are temporarily disabled too. A row close/navigation flushes the reference
before its ordinary body writer. Template save/close also flushes shared text.
Lost write authority can park an encrypted draft without a remote write.

A successful source-body write rereads the owning canonical database and
acknowledges its owning-page sequence without applying an absent full-body
receipt. During focused reference typing, the ordinary backing blocks and
inactive original fields adopt the new body immediately while the reference DOM
keeps native composition/undo. An ordinary save therefore cannot combine the
new row version with the old source text. Generic drafts keep their old version
and server conflict fences. A template adopts a refreshed body/version only
when its whole-template draft/form/request is clean; a dirty form is never
silently rebased across another text commit.

## Original location and verification boundaries

The original button flushes local shared text and repeats the authenticated
synced GET, checking current origin and exact instance body before opening its
page and storage location. An owned row opens in the existing item surface;
an authorized editable template opens its existing editor. The asynchronous
body target is focused only while page/navigation/root remain current.
Main-page reference buttons use the same storage-aware navigation helper.

Final light1280/dark390 row/template cases prove source/reference display, rich
child text, a real canonical source write, a later ordinary body write preserving
the new original and empty reference, and same-body original focus. The5 existing
browser cases retain main-reference/copy behavior, two-tab template stale-write
refusal, saved-template defaults and item side/full layout. The61 Node cases
retain journal, replay, conflict, permission, observer and tree-render contracts.
The existing extracted-renderer Node fixture now defines the source-creation
availability helper; no production behavior was changed for that test.

These are application checks in synthetic temporary fixtures, not fresh direct
Notion observation or a native Windows IME claim. Linked-body mounts on another
owner, read-only template navigation, hidden DB owner surfaces, broader cross-
body/navigation overlap, DB detach/partial copy, unsupported history/legacy and
full operating rollout remain outside this verified packet.
