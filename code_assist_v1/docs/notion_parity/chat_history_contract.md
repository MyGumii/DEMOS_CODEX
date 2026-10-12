# Sidebar chat history search

`GET /api/notion/ai-home/conversations` accepts optional `q` (at most 200 characters). Whitespace is trimmed; NFC-normalized, case-insensitive literal substring matching covers the title and stored user/assistant message text. SQL wildcard characters have no special meaning. Results contain only the existing id/title/updated_at/archived/revision metadata.

Each record must belong to the requested workspace and pass the existing current conversation/source authority check before matching or returning it. Owner-specific encryption and the existing active/archived filter remain unchanged. No new index, plaintext projection, schema, or migration is introduced.

The service scans the existing owner's bounded store (maximum 500 conversations) in 50-record batches until it finds 50 authorized matches and one matching lookahead or reaches the end. The numeric cursor identifies the unconsumed lookahead position in the original updated-time/id ordering. It is not a count of returned matches. Clients must retain the workspace, query and archive filter when following it. Like the existing offset cursor, it is not a snapshot under concurrent updates; the sidebar suppresses duplicate IDs on append. A fresh search re-reads current ordering and authority.

The UI invalidates old rows and pending pagination immediately on query changes, debounces non-composing input by 200 ms, and ignores obsolete responses after query/filter/account/workspace/tab changes. Enter searches immediately. Escape closes the inline input, clears the query and restores the ordinary list and search-trigger focus. Clearing the input then immediately closing still triggers the pending ordinary-list refresh.

Displayed date groups use the browser's local calendar and conversation update timestamp: today, yesterday, remaining dates less than 7 days old, remaining dates less than 30 days old, then year/month. Invalid timestamps receive a separate unknown-date group. Grouping is computed at list refresh. Actual Notion's inline search/empty today group/Escape behavior was observed; its historic group boundaries and search coverage could not be verified in the empty reference account.

Evidence: `tests/test_notion_chat_history.py`, `tests/test_notion_sidebar_tabs.js` (UI-CHAT-HISTORY), plus the 2026-09-22 isolated Chrome journey. Windows native IME, all roles/themes and live multi-client ordering are not claimed as verified by this increment.
