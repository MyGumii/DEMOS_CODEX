# Database item body tabs

The item body editor supports the existing `tabs` block shape. This is compatibility with the application's page editor; this packet does not establish that Notion has an identical block or menu.

## Data and editing

- The owner keeps ordered `props.tabs` and `active_tab_id`. Nested body blocks are stored as objects in `props.children`; each panel root has the owner's `tab_group` and a matching `tab_id`.
- Switching, adding, renaming, duplicating, reordering and removing panels uses the existing item body history, draft journal and atomic row-content save. The final valid panel cannot be removed. Unassigned legacy children remain visible.
- Read-only viewers can switch panels locally without calling the mutation path. Mutation controls are omitted.
- Conversion of existing content to tabs wraps the original content, formatting and descendants in the first panel. Conversion away removes panel metadata without dropping descendants.
- Moving or pasting into a panel rebinds external tab membership. Moving out clears it. Nested tab ownership is preserved. Duplication assigns fresh block IDs and nested owners; duplicated panel columns receive independent layout groups. Whole-block duplication preserves an existing outer column.
- Hidden panel roots do not become keyboard move destinations. Backspace cannot merge text across panel boundaries. Ordinary paragraph splitting and block insertion preserve panel membership.

## Input and display

- Tabs expose tablist, tab and tabpanel semantics. Inactive panels are hidden. Left/Right/Home/End navigate panel buttons; a tab switch does not select the entire block.
- Undo and save from a focused tab stay within the item editor. Tab-name inputs retain ordinary caret keys, and composition-confirming Enter does not submit a rename. The name input remains mounted while composing Korean text.
- Panels participate in the existing local-width column layout. Long tab bars scroll horizontally within the item; they do not enlarge the document.

## Verification boundary

The 2026-09-23 packet uses a named sanitized full-app fixture and real browser actions for text input, slash cancellation and conversion, nested panel switching, panel editing, independent duplication, delete/undo/redo, tab-boundary dragging, whole-tree clipboard, conversion preserving text, fresh connections and 390px layout. Focused automated checks cover tree ownership, rendering, editor input events, clipboard, saving, selection and movement.

Browser Unicode input is not a new native Windows IME check. The composition event regression is automated; the user's earlier native IME confirmation remains separate evidence. Read-only rendering is automated, not a new live role matrix. Actual Notion comparison, all role/concurrency combinations, the original R1–R11 scope and operating-port rollout remain separate gates.
