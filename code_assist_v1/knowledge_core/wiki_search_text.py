"""Query-only text extraction; never rewrite a Wiki or establish provenance.

Recognize exact renderer-owned introductions, not domain stopwords or
question-specific hints. An identical user-authored signature is treated as
scaffolding too: recognition is not proof of trusted authorship. The original
body remains available to the judge, citations, canonical storage and the UI.
"""
from html.parser import HTMLParser

PDF_WIKI_TITLE = '# 원문 기반 Wiki'
PDF_VISUAL_NOTICE = ("원문 텍스트·검출된 표와 페이지 이미지로 작성했습니다. 시각 관찰은 별도로 표시하며, "
    "추가 LLM 의미 검수를 수행하지 않았습니다. 정확한 인용·전체 페이지 전달은 의미 정확성이나 핵심 내용의 완전한 보존을 보증하지 않습니다.")
PDF_GEOMETRY_NOTICE = ("PDF에서 검출한 표의 셀 내용과 AI가 정리한 설명입니다. 별도 LLM 의미 검수는 "
    "수행하지 않았으며, 읽기 순서·표 설명의 귀속·핵심 내용의 완전한 보존을 자동 보증하지 않습니다.")
NATIVE_WIKI_TITLE = "# 자료 기반 Wiki"
NATIVE_WIKI_NOTICE = "원문 구조의 표와 AI가 정리한 설명입니다. 별도 LLM 의미 검수는 수행하지 않았으며, 핵심 내용의 완전한 보존을 자동 보증하지 않습니다."
# Preserve default block/list/table boundaries without splitting words across
# inline emphasis. This is structural text extraction, not a CSS layout engine.
_BLOCKS = frozenset((
    'address', 'article', 'aside', 'blockquote', 'body', 'br', 'caption',
    'center', 'dd', 'details', 'dialog', 'dir', 'div', 'dl', 'dt', 'fieldset',
    'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5',
    'h6', 'header', 'hgroup', 'hr', 'html', 'legend', 'li', 'main', 'menu',
    'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table', 'tbody', 'td',
    'tfoot', 'th', 'thead', 'tr', 'ul',
))


def without_generated_intro(markdown: str) -> str:
    """Remove only complete anchored signatures, never generic source headings."""
    for notice in (PDF_VISUAL_NOTICE, PDF_GEOMETRY_NOTICE):
        prefix = PDF_WIKI_TITLE + '\n\n' + notice + '\n\n## 원문 페이지 1\n\n'
        if markdown.startswith(prefix):
            return markdown[len(prefix):]
    prefix = NATIVE_WIKI_TITLE + '\n\n' + NATIVE_WIKI_NOTICE + '\n\n'
    if markdown.startswith(prefix):
        body = markdown[len(prefix):]
        # Display-aware PPTX assembly begins with this navigation label; DOCX
        # and legacy PPTX start directly with source-owned text or headings.
        navigation = '## 원문 슬라이드 1\n\n'
        return body[len(navigation):] if body.startswith(navigation) else body
    return markdown


class _SearchText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden.append(tag)
        if not self.hidden:
            if tag in _BLOCKS:
                self.parts.append('\n')
            elif tag == 'img':
                self.parts.append(' ' + (dict(attrs).get('alt') or '') + ' ')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in _BLOCKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def markdown_search_text(markdown: str) -> str:
    """Extract text without HTML tags/attributes or link destinations; no execution."""
    from markdown_it import MarkdownIt

    parser = _SearchText()
    parser.feed(MarkdownIt('commonmark').render(markdown))
    parser.close()
    return ''.join(parser.parts)
