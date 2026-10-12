# Staged synced-reference detach request (2026-09-24)

This boundary detaches one **reference** to a synced block. It leaves the
original and other references linked. Owned blocks and database/file resources
are copied into the destination page in one transaction. A child page remains
in its original document; the detached content links to that existing page.

`POST /api/notion/workspaces/<workspace_id>/pages/<page_id>/synced-blocks/<block_id>/detach`
accepts an exact JSON object with `origin`, `witnesses` and `operation_id`.
The caller obtains the first two fields from the canonical synced-block GET.
The server validates the request origin and takes the trusted application
origin from `NOTION_CANONICAL_ORIGIN`, never from JSON. The operation ID must be
stable across retries. First commit returns 201; an exact replay returns 200.
Changing the request while reusing that ID returns 409. Stale source or
destination witnesses also return 409 before any partial write.

`POST` to the same path with `/detach-result` instead of `/detach` accepts the
identical body. It performs an exact, read-only receipt lookup. `unknown`
means no committed receipt was found; `committed` includes the saved result and
the destination's current archive and sequence state. The lookup works after
the source has changed, so a lost save response does not force another copy.
For an active destination, both routes require current page-view access. After
the destination is archived, only its current workspace owner may recover the
receipt from their own trash. Responses use `no-store`.
The service never falls back to the legacy page store.

The server capability `collaborative_synced_detach` remains **false** in the
normal runtime. The routes therefore return 503; disposable HTTP and full-app
browser fixtures override the capability only to verify the boundary. The
reference block menu and browser-side exact receipt recovery are wired and
checked for same-page and cross-page references with deliberately lost save
responses. The isolated HTTP suite covers view-only denial, a member with
destination edit access, and grant revocation. The browser also checks a
file-and-database reference with a lost save response. A two-tab browser check
holds the same block's Web Lock in one tab, proves the other tab does not
issue or persist a save request, then verifies one save and receipt recovery.
Isolated guest and workspace-member browser checks confirm a view-only
disabled menu and a source-view/destination-edit save with receipt recovery.
An isolated browser downgrade after the witness read returns `synced_source_changed`
without a copy. Only that definitive 409 followed by an unknown exact receipt
discards the stale local intent; a later edit grant permits a fresh request.
Busy and stale notices remain visible in the page until dismissed. Lost response
recovery is rechecked after this change. Full access revocation, remote
concurrent-editor and varied-resource browser checks remain
before activation. The historical NP-1B/NP-2A route manifests and their fixed
route-count tests predate this staged API and are not treated as an activation
gate for this feature.
