# Page canvas width check (2026-09-27)

In the signed-in Notion reference page `Codex UI 비교 — 연결된 DB 원본 2026-09-16`, the browser capture was 1013px wide. Its user-resized sidebar ended near x=255 and the ordinary page title began near x=355, leaving about 100px between the sidebar and the content. An earlier 1280px reference with a 270px sidebar put the content near x=417.

The local ordinary page previously used `min(720px, viewport-content-width - 96px)`, leaving only about 48px per side below the 720px cap. The page now uses `min(720px, viewport-content-width - 200px)`. At 1013px with the sidebar set to 255px in an isolated collaborative fixture, the page/title starts at x=355. At 1280px, the existing 720px cap remains in effect. Full-width and database-full-page layouts keep their separate rules; mobile retains its dedicated 28px outer gutter.

`tests/browser/test_notion_sidebar_hover_parity.py` records the 1013px reference geometry and checks that the title, sidebar and top actions remain inside the viewport. This is a measured layout match for the sampled account/page, not a claim that all Notion page types or sidebar preferences share identical dimensions. No content in the signed-in reference page was edited.
