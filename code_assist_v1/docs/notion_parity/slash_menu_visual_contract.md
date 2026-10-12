# Slash menu visual recheck (2026-09-27)

Direct inspection of the signed-in Notion comparison page was paired with
one temporary `/` keystroke in its dedicated test paragraph. Escape closed the
menu and Backspace returned the paragraph to its original `앞 문장 /` value;
the user tab was returned to its prior page. The transient edit may still
appear in Notion's page history.

At the 873×836 viewport, the observed menu's outer box measured 324 px wide.
The isolated local menu also measured 324 px wide. Both show the recommended
and basic-block sections, shortcut hints, selected first item and an `esc`
close footer. The reference account showed four recommended rows, including
recent heading-4 choices; a fresh local account showed its two defaults.
That per-account recommendation difference does not justify a fixed row count.

The local footer originally exposed a recommendation-settings icon at rest,
whereas the observed reference footer showed only the close command. Later
rechecks removed that icon and per-item pin controls from both slash and plus
menus; past recommendation order remains available through recorded use.
Isolated browser checks verified the simpler footer, Escape dismissal, and
preservation of the slash text. This is a visual and interaction slice; other
slash commands, IME and full R1–R11 parity remain under the broader goal.

On 2026-09-28, the signed-in reference menu was measured again in a
1280×720 viewport from an empty block on the comparison page. The dialog was
324×288 px and ended 82 px above the viewport bottom. At this point the
account's recommended list contained AI notes, HTML, linked data source, and
page; this account history is not a fixed menu contract. A comparable isolated
development page showed a 324×357 px menu that ended much lower. The shared
popup placer now reserves bottom space only for the slash menu on short
viewports; the 1013×847 reference height remains 357 px. A 1280×720 isolated
Chrome capture shows the adjusted menu ending at approximately the same bottom
position, and the 1013px/390px layout and Escape assertions pass. The local
empty block was still vertically higher than the reference by about 12 px at
that checkpoint. The comparison page's temporary slash was removed and the
original Notion tab restored.

The follow-up measured the reference paragraph rows at y=268/308, 40 px high,
with editor leaves at y=274/314. The comparable local rows had been y=274/302,
28 px high. Top-level paragraph content now has 6 px of vertical hit area on
each side, with the first paragraph's editor kept at y=274. In a 1280×720
isolated Chrome run the rows and editors match all four reference coordinates.
The slash popover now starts at y=350 and measures 324×288 px, ending at
y=638 like the reference. The 1013×847 full-height and 390px in-bounds cases
also pass. The focused browser test plus three gutter selection widths passed
(4 cases), as did the eight popup/slash Node checks and JavaScript syntax.
The inspected screenshot is `outputs/paragraph-spacing-20260928/notion_slash_menu_720.png`.
This is geometry matching for the compared page, not a claim about every block
type or role. The actual Notion tab was returned to the original four-row DB.
