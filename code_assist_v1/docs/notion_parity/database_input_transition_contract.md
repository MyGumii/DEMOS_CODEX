# Database input transitions

An owned database cell commits its ordinary change event to the current block
and row identity. A canonical refresh may replace or reorder the model after
the input was painted; the handler must not mutate the detached rendered model
or a different row occupying its former index. Removed rows, disabled inputs,
revoked edit permission and a changed account/workspace/page/load are rejected.
Legacy rows without an identity retain their existing index-based behavior.

The main block renderer leaves a focused database field mounted, preserving its
native text, selection and composition state. Focus belongs to the scope in
which the input was bound; a previous page's input cannot defer a new page.
When focus leaves, a deferred render is replayed. If leaving was caused by a
pointer action, the render waits through pointer release and click dispatch.
Cancellation and window blur release the pointer guard. Saving still uses the
existing change, history and database command paths; this is not a new draft
store or a change to conflict/retry policy.

Actual browser reproduction showed a keyboard edit followed by a board/inline
button click saved the cell but swallowed the first click. The fixed browser
changed views on that first click and retained the edited value. A second
connection edited a different row while the first input remained focused; both
values survived a new connection. Search typing, keyboard clearing, side-peek
closure, full/inline transitions and leaving/reopening the document were also
checked in an isolated synthetic fixture.

`test_notion_database_input_transition.js` exercises held-pointer ordering,
keyboard focus changes, cancellation, navigation, identity rebinding and the
actual change handler. The existing toolbar focus fixture was updated for the
current renderer's collaboration guard and layout mount. Nineteen runner checks
passed across six files, including the database bridge, creation, range history
and shell runtime. Native Windows IME, all roles and same-cell concurrent
conflicts are not newly certified by this packet.

Two observations remain unresolved: an automatic `fill` followed by a transition
did not commit the same way as keyboard input, and unspecified Korean fragments
appeared twice during the browser session. Later tab-scoped sequential key entry
and the final saved Korean value were exact. Neither observation is attributed
to the user or declared fixed; preserve them for a separate input-event audit.
