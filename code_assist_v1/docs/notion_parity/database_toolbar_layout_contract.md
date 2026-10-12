# Database header and full-page table layout

The database title and create action share a flexible header. The title may truncate or move to its own line; the action keeps its full label and does not shrink into vertical text. A decorative dropdown arrow is not shown on the single-action create button.

View tabs keep their individual widths and scroll inside the available toolbar width. Other toolbar buttons keep their labels, while the toolbar may wrap. This uses the available database area, including a page narrowed by item side peek, rather than only the browser width.

Full-page tables use the same visible-property count as inline tables. CSS must not hide non-title cells or insert gaps based on row number. Horizontal table overflow stays inside the table; sidebar collapse must not force a two-column grid. The existing full-page-to-inline action is available to pointer and keyboard users.

This packet changes CSS and its version only. It does not change schema permissions, view configurations, row creation, cell persistence, or migrations. Verification uses the real app in a sanitized fixture: narrow side peek, full-page table, long view name, list/board/table switching, search and clearing, item creation, inline return, filter Escape, sidebar collapse, light/dark, 390px and 900px, and a fresh browser connection.

Saved-data verification distinguishes preserved original content from expected normalization: missing date cells become null entries, view defaults become explicit, and one intentionally created row and one test view remain. All original title/status values, body blocks, row versions and property definitions remain unchanged.

The current browser does not provide a fresh logged-in Notion comparison. Exact Notion spacing, every role/theme/view combination, and production rollout remain outside this packet's completed evidence. An initial rapid automation fill/transition sequence did not retain the intended cell/search value; later keyboard input and fresh-connection checks succeeded. The initial sequence's cause remains a separate input-transition audit item.
