"""UI-HTML-STRUCTURE: hierarchy, passive markup and read-only export integration."""
from copy import deepcopy
from html.parser import HTMLParser
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import notion_html_blocks as html


def test_ui_html_inline_named_colors_preserve_text_and_only_accept_known_values():
    rendered = html.rich({"content":"선택 문구", "props":{"rich_text_html":
        '<span data-notion-inline-color="red" data-notion-inline-background="yellow">선택 문구</span>'}})
    assert 'color:#d44c47' in rendered
    assert 'background-color:#fbf3db' in rendered
    assert '선택 문구' in rendered
    assert 'data-notion-' not in rendered
    invalid = html.rich({"props":{"rich_text_html":
        '<span data-notion-inline-color="url(javascript:bad)" data-notion-inline-background="other">문구</span>'}})
    assert invalid == '<span>문구</span>'


def block(id, kind='paragraph', content=None, **props):
    return {'id': id, 'type': kind, 'content': content if content is not None else id, 'props': props}


class Tree(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parent': self.stack[-1] if self.stack else None, 'text': ''}
        self.nodes.append(node)
        if tag not in {'input', 'img', 'br', 'hr', 'meta', 'link', 'source'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()['tag'] == tag:
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data

    def find(self, tag=None, text=None, **attrs):
        return [node for node in self.nodes if (tag is None or node['tag'] == tag)
                and (text is None or node['text'] == text)
                and all(node['attrs'].get(k) == v for k, v in attrs.items())]


def nested_fixture():
    return [block('h', 'heading2', '제목', text_color='blue'),
            block('c', 'callout', '안내', icon='💡', background_color='yellow', text_color='purple'),
            block('p', content='하위 글', indent=1, rich_text_html='하위 <strong>글</strong>'),
            block('b', 'bullet', '목록', indent=1, text_color='red'),
            block('t', 'toggle', '접힌 글', open=False),
            block('hidden', content='펼쳐야 보임', indent=1),
            block('gap', content='독립 문단', visual_indent=2),
            block('table', 'simple_table', '', rows=[['열', 0], ['값', '<unsafe>']], header_row=True)]


def test_ui_html_fourth_heading_and_collapsed_toggle_keep_semantics():
    source = [block('h4', 'heading4', '네 번째 제목'),
              block('t4', 'toggle_heading4', '접힌 제목', open=False),
              block('child', content='하위 본문', indent=1)]
    tree = Tree(html.render(source))
    assert tree.find('h4', text='네 번째 제목')
    toggle = tree.find('details', **{'class': 'html-toggle html-toggle-4'})[0]
    assert 'open' not in toggle['attrs'] and toggle['text'] == '접힌 제목하위 본문'


def test_ui_html_structure_callout_toggle_independent_gap_and_immutable_input():
    source = nested_fixture(); before = deepcopy(source)
    tree = Tree(html.render(source))
    callout = tree.find('aside', **{'class': 'html-callout'})[0]
    assert callout['text'] == '💡안내하위 글목록'
    assert tree.find('strong', text='글')
    toggle = tree.find('details')[0]
    assert 'open' not in toggle['attrs'] and toggle['text'] == '접힌 글펼쳐야 보임'
    assert tree.find('div', text='독립 문단', style='margin-left:48px')
    assert source == before
    assert len(tree.find('th')) == 2 and tree.find('th', text='0')
    assert tree.find('td', text='<unsafe>')


def test_ui_html_structure_nested_colors_and_inline_marks_are_safe():
    source = [block('outer', 'callout', 'outer', text_color='purple', background_color='yellow'),
              block('inner', 'callout', 'inner', indent=1, visual_indent=1, background_color='blue'),
              block('leaf', content='leaf', indent=2, text_color='red')]
    tree = Tree(html.render(source))
    assert len(tree.find('aside', **{'class': 'html-callout'})) == 2
    inner = tree.find('div', **{'data-block-type': 'callout'})[1]
    assert 'margin-left:24px' in inner['attrs']['style'] and '--html-block-bg:#e7f3f8' in inner['attrs']['style']
    assert tree.find('div', style='margin-left:0px;color:#d44c47')
    for color, values in html.COLORS.items():
        output = html.render([block('color', text_color=color, background_color=color)])
        assert values[0] in output and values[1] in output


def test_ui_html_structure_untrusted_rich_content_never_escapes_its_block():
    markup = '<b onclick="evil()">bold</b><script>SECRET</script><svg onload="evil()"><text>SVG</text></svg></div><img src=x onerror=evil()><a href="javascript:evil()">bad</a><a href="https://example.com/?a=1&amp;b=2" target=_blank>safe</a><span style="position:fixed;color:#d44c47;background-image:url(https://evil.test)">red</span><strong>unclosed'
    output = html.render([block('attack', rich_text_html=markup, background_color='red;position:fixed'), block('next')])
    tree = Tree(output)
    assert len(tree.find('div', **{'class': 'html-block'})) == 2
    assert not any(value in output for value in ('onclick', '<script', '<svg', '<img', 'SECRET', 'javascript:', 'position:', 'background-image', 'evil.test'))
    assert tree.find('a', text='bad')[0]['attrs'].get('href') is None
    assert tree.find('a', text='safe')[0]['attrs']['href'] == 'https://example.com/?a=1&b=2'
    assert tree.find('span', style='color:#d44c47')
    assert tree.find('p', text='next')[0]['parent']['parent'] is None


def test_ui_html_structure_lists_and_columns_keep_separate_ancestry():
    source = [block('c', 'callout', layout_group='columns', layout_column=0, layout_columns=2),
              block('one', 'numbered', indent=1, layout_group='columns', layout_column=0),
              block('two', 'numbered', indent=1, layout_group='columns', layout_column=0),
              block('other', 'numbered', layout_group='columns', layout_column=1)]
    tree = Tree(html.render(source))
    assert tree.find('aside')[0]['text'] == '💡conetwo'
    assert [node['attrs']['start'] for node in tree.find('ol')] == ['1', '2', '1']
    assert len(tree.find('div', **{'class': 'public-column'})) == 2


def test_ui_html_structure_tabs_keep_all_contents_and_unique_panel_ids():
    source = [block('tabs', 'tabs', tabs=[{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}], active_tab_id='a'),
              block('ca', 'callout', tab_group='tabs', tab_id='a'),
              block('pa', indent=1, tab_group='tabs', tab_id='a'),
              block('tb', 'toggle', tab_group='tabs', tab_id='b'),
              block('pb', indent=1, tab_group='tabs', tab_id='b')]
    tree = Tree(html.render(source))
    panels = tree.find('section', role='tabpanel')
    assert len(panels) == 2 and len({node['attrs']['id'] for node in panels}) == 2
    assert 'hidden' not in panels[0]['attrs'] and 'hidden' in panels[1]['attrs']
    assert panels[0]['text'] == '💡capa' and panels[1]['text'] == 'tbpb'


def test_ui_html_structure_public_shell_preserves_resource_routes_and_forms():
    from code_assist_v1.routes_notion import _public_page_html
    app = Flask(__name__)
    source = nested_fixture() + [block('h4', 'heading4', '네 번째 제목'),
                               block('t4', 'toggle_heading4', '접힌 네 번째 제목', open=False),
                               block('img', 'image', 'Picture', file_id='f_example'),
                               block('collab_img', 'image', 'Collaborative picture', file_id='file_example'),
                               block('form', 'form', 'Form', properties=[{'id': 'title', 'name': 'Title', 'type': 'title'}], rows=[])]
    with app.test_request_context('/public/notion/test-token'):
        output = _public_page_html('test-token', {'page': {'id': 'page', 'title': 'Public'}, 'blocks': source})
    assert '/public/notion/test-token/files/f_example' in output
    assert '/public/notion/test-token/forms/form' in output
    assert Tree(output).find('aside', text='💡안내하위 글목록')
    assert 'html-toggle-children' in output
    assert Tree(output).find('h4', text='네 번째 제목')
    assert Tree(output).find('details', **{'class': 'html-toggle html-toggle-4'})
    with app.test_request_context('/'):
        authenticated = _public_page_html('', {'page': {'id': 'page', 'title': 'Export'}, 'blocks': source})
    assert '/api/notion/collaboration/files/file_example' in authenticated
    assert '/api/notion/files/f_example' in authenticated


def test_ui_html_structure_nested_tabs_keep_their_panels_inside_outer_panel():
    source = [block('root', 'tabs', tabs=[{'id': 'a'}, {'id': 'b'}]),
              block('inner', 'tabs', tabs=[{'id': 'x'}, {'id': 'y'}], tab_group='root', tab_id='b'),
              block('x', tab_group='inner', tab_id='x'), block('y', tab_group='inner', tab_id='y')]
    tree = Tree(html.render(source))
    panels = tree.find('section', role='tabpanel')
    assert len(panels) == 4 and len({node['attrs']['id'] for node in panels}) == 4
    assert tree.find('p', text='x') and tree.find('p', text='y')
    for leaf in (tree.find('p', text='x')[0], tree.find('p', text='y')[0]):
        ancestors = []; parent = leaf['parent']
        while parent:
            if parent['attrs'].get('role') == 'tabpanel':
                ancestors.append(parent)
            parent = parent['parent']
        assert len(ancestors) == 2 and 'hidden' in ancestors[1]['attrs']


def test_ui_html_structure_collaborative_export_keeps_bundle_and_database_readonly(tmp_path):
    from code_assist_v1.tests.notion_collaboration.test_visible_search import _environment
    from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
    a, _, _, catalog, workspace = _environment(tmp_path)
    page = a.read_document('owner', 'page-e1')
    a.create_document('owner', workspace, 'html-structure-document', title='Structured', blocks=nested_fixture(),
                      expected_workspace_epoch=page['workspace_epoch'], operation_id='op-html-structure-create-0001')
    def dump():
        with sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True) as con:
            return tuple(con.iterdump())
    before = dump()
    result = a.export_html('owner', 'html-structure-document')
    assert result['bundle'] == a.export_bundle('owner', 'html-structure-document')
    assert Tree(result['html']).find('aside', text='💡안내하위 글목록')
    assert Tree(result['html']).find('details')[0]['text'] == '접힌 글펼쳐야 보임'
    with pytest.raises(NotFoundOrDenied):
        a.export_html('outsider', 'html-structure-document')
    assert dump() == before


def test_ui_html_structure_explicit_nested_columns_and_zero_row_containers():
    source = [block('outer', 'callout', children=['holder', 'outside']),
              block('inside-a', content='inner left', layout_group='inner', layout_column=0),
              block('outside', content='outer right', layout_group='outer', layout_column=1),
              block('holder', content='', layout_container=True, layout_group='outer', layout_column=0, children=['inside-a', 'inside-b']),
              block('inside-b', 'toggle', 'inner right', layout_group='inner', layout_column=1, children=['hidden'], open=False),
              block('hidden', content='closed body')]
    before = deepcopy(source); tree = Tree(html.render(source))
    assert len(tree.find('div', **{'class': 'public-columns'})) == 2
    holder = tree.find('div', **{'data-html-block': 'holder'})[0]
    assert holder['text'] == 'inner leftinner rightclosed body'
    assert holder['attrs']['style'] == 'margin-left:0px'
    assert all(node['text'] for node in tree.find('p'))
    assert tree.find('aside')[0]['text'] == '💡outerinner leftinner rightclosed bodyouter right'
    assert 'open' not in tree.find('details')[0]['attrs']; assert source == before


def test_ui_html_structure_tab_members_keep_owned_children_without_repeated_tab_tags():
    source = [block('tabs', 'tabs', '', tabs=[{'id':'a'}, {'id':'b'}], children=['holder', 'other']),
              block('holder', content='', tab_group='tabs', tab_id='a', layout_container=True, children=['left', 'right']),
              block('left', layout_group='g', layout_column=0), block('right', layout_group='g', layout_column=1),
              block('other', tab_group='tabs', tab_id='b')]
    tree = Tree(html.render(source)); panels = tree.find('section', role='tabpanel')
    assert panels[0]['text'] == 'leftright' and panels[1]['text'] == 'other'
    assert len(tree.find('div', **{'class':'public-columns'})) == 1


def test_ui_html_structure_inline_children_preserve_local_gap_and_resource_fallback():
    source = [block('holder', content='', layout_container=True, children=[
        block('marked', content='visible', indent=2, layout_container=True, children=[block('asset', 'image', 'caption')])])]
    seen = []
    output = html.render(source, lambda b: seen.append(b['id']) or '<img src="/fixture.png">')
    tree = Tree(output)
    assert tree.find('p', text='visible')
    assert tree.find('div', **{'data-html-block':'marked'})[0]['attrs']['style']=='margin-left:48px'
    assert seen == ['asset'] and tree.find('img')


def test_ui_html_structure_invalid_ownership_fails_before_partial_output():
    for source in ([block('a', children=['missing'])], [block('a', children=['b']), block('b', children=['a'])],
                   [block('a', children=['child']), block('b', children=['child']), block('child')]):
        with pytest.raises(ValueError): html.render(source)
