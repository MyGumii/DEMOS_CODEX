"""UI-ICON-003: export renderer shares catalog and authorized file scope."""
from code_assist_v1.notion_icons import icon_html, _catalog
from code_assist_v1.notion_icons import export_icon_html, icon_attribution_html
import base64
import hashlib
import io
import pytest


def test_vector_palette_and_legacy_emoji():
    for item in _catalog()['vectors']:
        for color in _catalog()['colors']:
            html = icon_html(f'ni:{item["id"]}:{color["id"]}')
            assert '<svg' in html and color['light'] in html
    assert '<svg' in icon_html('list')
    assert '1f44b.svg' in icon_html('👋')
    assert '1f44b-1f3fd.svg' in icon_html('👋🏽')
    assert 'CC BY 4.0' in icon_attribution_html('👋🏽')
    assert 'data:image/svg+xml;base64,' in export_icon_html('👋🏽', page_id='page-a', resolve_file=lambda _: pytest.fail('emoji must not read user files'))
    assert '&lt;script&gt;' in icon_html('<script>')


def test_file_requires_authorized_mapping():
    assert '<img' not in icon_html('nf:f_example')
    assert '<img' not in icon_html('nf:f_example', file_urls={'f_example':'https://outside.test'})
    image = icon_html('nf:f_example', file_urls={'f_example':'/public/notion/token/files/f_example'})
    assert '<img' in image and 'object-fit:cover' in image and 'object-position:center' in image
    assert '<img' not in icon_html('nf:../../private')


def test_ui_icon_export_empty_and_embedded_catalog_assets():
    assert icon_html('') == '' and icon_html(None) == ''
    assert '<svg' in icon_html('nw:wi_' + 'a' * 40)
    value = export_icon_html('👋', page_id='page-a', resolve_file=lambda _: pytest.fail('emoji must not read user files'))
    encoded = value.split('base64,', 1)[1].split('"', 1)[0]
    assert base64.b64decode(encoded).startswith(b'<svg')
    assert 'CC BY 4.0' in icon_attribution_html('👋')
    assert icon_attribution_html('ni:doc:blue') == ''


def test_ui_icon_export_file_is_page_scoped_bounded_and_integrity_checked(tmp_path):
    raw = b'\x89PNG\r\n\x1a\nsynthetic-raster-signature'
    path = tmp_path / 'icon.png'; path.write_bytes(raw)
    item = dict(document_id='page-a', path=path, mime_type='image/png', size_bytes=len(raw),
                content_sha256='sha256:' + hashlib.sha256(raw).hexdigest())
    def render(descriptor):
        return export_icon_html('nf:file_example', page_id='page-a', resolve_file=lambda _: descriptor)
    assert base64.b64encode(raw).decode() in render(item)
    for changes in ({'document_id':'page-b','path':tmp_path/'must-not-read'},
                    {'size_bytes':3*1024*1024}, {'mime_type':'image/svg+xml'},
                    {'size_bytes':1}, {'content_sha256':'sha256:wrong'}):
        assert 'data:image' not in render({**item,**changes})
    path.write_bytes(b'<script>not a raster</script>')
    assert 'data:image' not in render(item)
    def denied(_): raise PermissionError('denied')
    with pytest.raises(PermissionError):
        export_icon_html('nf:file_example', page_id='page-a', resolve_file=denied)


@pytest.mark.parametrize(('fmt','mime'), [('PNG','image/png'),('JPEG','image/jpeg'),('WEBP','image/webp')])
def test_ui_icon_export_supported_raster_formats_decode_without_app_urls(tmp_path, fmt, mime):
    from PIL import Image
    output=io.BytesIO(); Image.new('RGB',(3,2),'blue').save(output,format=fmt)
    raw=output.getvalue(); path=tmp_path/'image'; path.write_bytes(raw)
    value=export_icon_html('nf:f_image',page_id='page-a',resolve_file=lambda _:dict(page_id='page-a',path=path,mime_type=mime,size=len(raw)))
    assert f'data:{mime};base64,' in value and '/api/notion/' not in value
    assert 'object-fit:cover' in value and 'object-position:center' in value and 'width="124" height="124"' in value
    payload=base64.b64decode(value.split('base64,',1)[1].split('"',1)[0])
    with Image.open(io.BytesIO(payload)) as image:
        image.load(); assert image.size==(3,2) and image.format==fmt
