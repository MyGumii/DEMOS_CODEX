# Timeline hour scale — 2026-09-26

The signed-in Notion comparison page `Codex UI 비교 — 연결된 DB 원본
2026-09-16` offered these timeline scales in order: 시간, 일, 주, 2주,
월, 분기, 년, 5년. At `시간`, the visible grid had 15-minute labels and a
red current-time line. The comparison used a temporary browser tab and
read-only view changes. The original month scale and table view were
restored before that tab was closed; the two original database rows were
unchanged.

The local scale list previously omitted `시간`. The local date model now
provides 192 quarter-hour slots across the anchored day and the following
day, one-day navigation, and time-aware slot coordinates. The owned and
linked timeline renderer uses those coordinates for timed bars, spans an
untimed date across its day, groups the slots under both day labels, and
shows a current-time line when today is in range. The new scale appears
first, matching the observed Notion menu order.

The hour view scrolls near the current-time marker on first render and
retains a user's horizontal position when the owned or linked view redraws.
The focused Node date/linked-view suite passed 18 checks. Isolated browser
cases verified the 192 labels and two day headings, a 17:00–17:30 bar
spanning three slots, an all-day bar spanning 96 slots, a next-day timed
item, the current-time marker, initial scroll, and scroll preservation in
owned and linked views (2 passed in 32.89 seconds). The existing six
timeline-undated browser cases passed together
in 122.70 seconds during this change. JavaScript and Python syntax checks
passed. The first hour browser case initially failed due to a malformed
test fixture and then a whitespace-only style-string expectation; both
test errors were corrected without changing product behavior.

The normal development address on port 10322 had no listener during this
comparison; local verification used an isolated application fixture. No
production catalog or feature flag was changed.

## Undated item assignment follow-up

In a temporary Notion comparison tab, two synthetic undated rows were added
solely to test the hour menu. With the visible quarter-hour window beginning at
15:45, clicking the first row in `날짜 없음` assigned 2026-09-26 16:45–17:45.
After scrolling the hour window to begin at 07:15, clicking the second row
assigned 2026-09-26 08:15–09:15. Thus both observed assignments started one
hour after the first visible quarter-hour slot and lasted one hour. Each
synthetic row was moved to the recoverable trash; the original two rows,
table view, and month scale were confirmed before the tab was closed.

The local owned and linked undated-menu actions now pass the first visible
quarter-hour slot to the shared date model. That model returns a timed one-hour
range, including a next-day end when the visible slot is late at night. The
focused Node date/linked suite passed 20 checks, and JavaScript/Python syntax
checks passed. Nine isolated hour and undated browser cases passed together.
An additional browser case sent the hour assignment through the authenticated
2423 cell command and confirmed the 08:15–09:15 range in its isolated database.
The original local hour window held only two dates. It now slides one date in
either direction as the horizontal scroll approaches an edge, retaining the
visible time and keeping `timeline_anchor` unchanged. The effective window
start is carried in the rendered scroll element, so an undated item assigned
after sliding uses the newly visible date. Owned and linked views both use the
same window manager. Node date/linked/window checks passed 21 cases. Four
focused hour browser cases passed together; a linked-view edge shift and an
owned-view next-day 08:15–09:15 assignment after shifting passed individually.
The signed-in Notion comparison view was then scrolled right from September 26
through September 27 to September 28 without using its date-navigation buttons;
the date headings and 15-minute labels continued across those days. Its month
scale and original table view were restored, and the temporary comparison tab
was closed without changing the two original rows. The local browser case now
crosses two successive right edges and two left edges, preserving the stored
anchor and assigning a next-day undated item at 08:15–09:15. Seven existing
undated browser cases passed after the window change. The two-date local DOM is
a sliding rendering window, not a stored database range. Notion preloads more
offscreen dates and uses a different scrollbar geometry, so exact pixel-level
scroll parity remains open.
