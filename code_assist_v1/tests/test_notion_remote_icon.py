"""UI-ICON-LINK: bounded, read-only image import and HTTP boundaries."""
import io
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from flask import Flask
from PIL import Image

from code_assist_v1 import notion_remote_icon as remote
from code_assist_v1 import routes_notion_files as routes


@pytest.fixture
def png():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), '#12abcd').save(output, 'PNG')
    return output.getvalue()


@pytest.mark.parametrize('url', [None, '', 'https://' , 'file:///a.png', 'ftp://example.com/a',
    'https://u:p@example.com/x', 'https://@example.com', 'https://example.com:0/x',
    'https://example.com:65536/x', 'https://example.com\\@other.com',
    'https://example.com/\nx', 'https://example.com/a b', 'https://[fe80::1%25eth0]/x', 'x'*2049])
def test_UI_ICON_LINK_rejects_malformed_urls(url):
    with pytest.raises(ValueError):
        remote.normalize_url(url)


def test_UI_ICON_LINK_normalizes_unicode_and_drops_fragment():
    assert remote.normalize_url(' HTTPS://예시.com/한글.png?x=1#preview ') == 'https://xn--vv4b11d.com/%ED%95%9C%EA%B8%80.png?x=1'


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.2.3', '172.16.2.3', '192.168.2.3',
    '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1', '192.0.2.1',
    '::1', 'fc00::1', 'fe80::1', 'ff02::1', '::ffff:8.8.8.8', '64:ff9b::808:808',
    '64:ff9b:1::808:808', '2002:0808:0808::1', '2001::1', 'garbage'])
def test_UI_ICON_LINK_rejects_nonpublic_and_translated_addresses(address):
    assert not remote._public_address(address)


def address(ip='8.8.8.8', port=443):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, port))


def test_UI_ICON_LINK_mixed_dns_denied_before_connect(monkeypatch):
    monkeypatch.setattr(remote.socket, 'getaddrinfo', lambda *a, **k: [address(), address('127.0.0.1')])
    factory = Mock()
    monkeypatch.setattr(remote.socket, 'socket', factory)
    with pytest.raises(ValueError, match='외부'):
        remote._connect('https://example.com/x', time.monotonic()+10, threading.Event())
    factory.assert_not_called()


def test_UI_ICON_LINK_pins_resolved_address_and_verifies_tls_hostname(monkeypatch):
    dns = Mock(return_value=[address()])
    monkeypatch.setattr(remote.socket, 'getaddrinfo', dns)
    stream, tls, context = Mock(), Mock(), Mock()
    context.wrap_socket.return_value = tls
    monkeypatch.setattr(remote.socket, 'socket', Mock(return_value=stream))
    default_context = Mock(return_value=context)
    monkeypatch.setattr(remote.ssl, 'create_default_context', default_context)
    connection, result = remote._connect('https://example.com/x', time.monotonic()+10, threading.Event())
    stream.connect.assert_called_once_with(('8.8.8.8', 443))
    context.wrap_socket.assert_called_once_with(stream, server_hostname='example.com')
    default_context.assert_called_once_with()
    dns.assert_called_once_with('example.com', 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    assert result is tls and connection.sock is tls and connection.auto_open == 0
    connection.close()


def test_UI_ICON_LINK_bad_certificate_and_expired_connect_close_socket(monkeypatch):
    monkeypatch.setattr(remote.socket, 'getaddrinfo', lambda *a, **k: [address()])
    stream, context = Mock(), Mock()
    monkeypatch.setattr(remote.socket, 'socket', Mock(return_value=stream))
    monkeypatch.setattr(remote.ssl, 'create_default_context', Mock(return_value=context))
    context.wrap_socket.side_effect = ssl.SSLCertVerificationError('bad certificate')
    with pytest.raises(ValueError, match='연결'):
        remote._connect('https://example.com/x', time.monotonic()+10, threading.Event())
    stream.close.assert_called_once()
    stream.reset_mock()
    monkeypatch.setattr(remote, '_remaining', Mock(side_effect=[1, ValueError('expired')]))
    with pytest.raises(ValueError, match='expired'):
        remote._connect('https://example.com/x', time.monotonic()+10, threading.Event())
    stream.close.assert_called_once()


class Response:
    def __init__(self, raw=b'', *, status=200, headers=None):
        self.status, self.headers, self.stream = status, headers or {}, io.BytesIO(raw)
        self.closed = False

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        return self.stream.read(size)

    def isclosed(self):
        return self.closed

    def close(self):
        self.closed = True


def connections(monkeypatch, responses):
    opened = []
    def connect(url, deadline, canceled):
        response = responses[len(opened)]
        connection, stream = Mock(), Mock()
        connection.getresponse.return_value = response
        opened.append((url, connection, stream, response))
        return connection, stream
    monkeypatch.setattr(remote, '_connect', connect)
    return opened


def test_UI_ICON_LINK_fetches_binary_without_forwarding_identity_and_closes(monkeypatch, png):
    opened = connections(monkeypatch, [Response(png, headers={'Content-Type':'image/png', 'Content-Length':str(len(png))})])
    assert remote._fetch('https://example.com/a.png?x=1', threading.Event()) == (png, 'image/png')
    _, connection, stream, response = opened[0]
    args, kwargs = connection.request.call_args
    assert args == ('GET', '/a.png?x=1')
    assert set(kwargs['headers']) == {'Accept', 'Accept-Encoding', 'User-Agent', 'Connection'}
    assert kwargs['headers']['Accept-Encoding'] == 'identity'
    assert response.closed
    connection.close.assert_called_once(); stream.close.assert_called_once()


def test_UI_ICON_LINK_real_http_response_closes_after_last_content_length_chunk(monkeypatch, png):
    client, server = socket.socketpair()
    try:
        server.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: image/png\r\nContent-Length: '
                       + str(len(png)).encode() + b'\r\n\r\n' + png)
        connection = remote.http.client.HTTPConnection('public.example')
        connection.auto_open = 0
        connection.sock = client
        monkeypatch.setattr(remote, '_connect', lambda *args: (connection, client))
        assert remote._fetch('http://public.example/x', threading.Event()) == (png, 'image/png')
        assert client.fileno() == -1
    finally:
        client.close(); server.close()


def test_UI_ICON_LINK_redirects_recheck_dns_before_second_connection(monkeypatch):
    response = Response(status=302, headers={'Location':'http://internal.example/image'})
    connection, stream = Mock(), Mock()
    connection.getresponse.return_value = response
    first = True
    real_connect = remote._connect
    def connect(url, deadline, canceled):
        nonlocal first
        if first:
            first = False
            return connection, stream
        return real_connect(url, deadline, canceled)
    dns = Mock(return_value=[address('127.0.0.1',80)])
    monkeypatch.setattr(remote, '_connect', connect)
    monkeypatch.setattr(remote.socket, 'getaddrinfo', dns)
    factory = Mock(); monkeypatch.setattr(remote.socket, 'socket', factory)
    with pytest.raises(ValueError, match='외부'):
        remote._fetch('https://example.com/x', threading.Event())
    dns.assert_called_once_with('internal.example', 80, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    factory.assert_not_called(); assert response.closed


def test_UI_ICON_LINK_relative_redirect_and_redirect_limit(monkeypatch, png):
    opened = connections(monkeypatch, [Response(status=302, headers={'Location':'/final.png'}), Response(png)])
    assert remote._fetch('https://example.com/start', threading.Event()) == (png, 'image/png')
    assert opened[1][0] == 'https://example.com/final.png'
    responses = [Response(status=302, headers={'Location':'/again'}) for _ in range(4)]
    opened = connections(monkeypatch, responses)
    with pytest.raises(ValueError, match='이동'):
        remote._fetch('https://example.com/start', threading.Event())
    assert len(opened) == 4 and all(r.closed for r in responses)


@pytest.mark.parametrize('kind', ['oversized-header','oversized-stream','encoding','type','bad-image','html','status'])
def test_UI_ICON_LINK_rejects_invalid_or_unbounded_responses(monkeypatch, png, kind):
    raw, headers, status = png, {}, 200
    if kind == 'oversized-header': headers['Content-Length'] = str(remote.MAX_BYTES+1)
    if kind == 'oversized-stream': raw += b'0'*remote.MAX_BYTES
    if kind == 'encoding': headers['Content-Encoding'] = 'gzip'
    if kind == 'type': headers['Content-Type'] = 'image/jpeg'
    if kind == 'bad-image': raw = png[:12]
    if kind == 'html': raw = b'<html>login</html>'
    if kind == 'status': status = 403
    response = Response(raw, status=status, headers=headers)
    opened = connections(monkeypatch, [response])
    with pytest.raises(ValueError):
        remote._fetch('https://example.com/x', threading.Event())
    assert response.closed
    opened[0][1].close.assert_called_once(); opened[0][2].close.assert_called_once()


def test_UI_ICON_LINK_timeout_keeps_capacity_until_worker_exits(monkeypatch, png):
    slots, started, finish = threading.BoundedSemaphore(1), threading.Event(), threading.Event()
    canceled_seen = []
    def slow(url, canceled):
        started.set(); finish.wait(2); canceled_seen.append(canceled.is_set()); return png, 'image/png'
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(remote, '_workers', executor)
        monkeypatch.setattr(remote, '_slots', slots)
        monkeypatch.setattr(remote, '_fetch', slow)
        monkeypatch.setattr(remote, 'TIMEOUT', .05)
        try:
            with pytest.raises(ValueError, match='초과'): remote.fetch_image('https://example.com/x')
            assert started.is_set()
            with pytest.raises(ValueError, match='다른'): remote.fetch_image('https://example.com/y')
            assert not slots.acquire(blocking=False)
        finally:
            finish.set()
    assert canceled_seen == [True]
    assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)
    slots.release()


@pytest.fixture
def client(monkeypatch, png):
    app = Flask(__name__)
    routes.register_notion_file_routes(app)
    monkeypatch.setattr(routes.workspace_routes, '_uid', lambda:'owner')
    fetch = Mock(return_value=(png, 'image/png'))
    monkeypatch.setattr(remote, 'fetch_image', fetch)
    return app.test_client(), fetch


def test_UI_ICON_LINK_route_binary_no_store_and_auth_recheck(client, monkeypatch, png):
    browser, fetch = client
    result = browser.post('/api/notion/icon-image-preview', json={'url':'https://example.com/x'})
    assert result.status_code == 200 and result.data == png and result.mimetype == 'image/png'
    assert 'no-store' in result.headers['Cache-Control']
    assert result.headers['X-Content-Type-Options'] == 'nosniff'
    monkeypatch.setattr(routes.workspace_routes, '_uid', Mock(side_effect=['owner','other']))
    assert browser.post('/api/notion/icon-image-preview', json={'url':'https://example.com/x'}).status_code == 403


def test_UI_ICON_LINK_route_rejects_before_network(client, monkeypatch):
    browser, fetch = client
    path = '/api/notion/icon-image-preview'
    cases = [({'json':{'url':'https://example.com/x'},'headers':{'Origin':'https://other.example'}},400),
        ({'json':{}},400), ({'json':{'url':'x','extra':1}},400),
        ({'data':'{"url":"a","url":"b"}','content_type':'application/json'},400),
        ({'data':'{"url":NaN}','content_type':'application/json'},400),
        ({'data':'{"url":"x"}','content_type':'text/plain'},400),
        ({'data':' '*70000,'content_type':'application/json'},413)]
    for kwargs, status in cases:
        assert browser.post(path, **kwargs).status_code == status
    monkeypatch.setattr(routes.workspace_routes, '_uid', lambda:None)
    assert browser.post(path, json={'url':'https://example.com/x'}).status_code == 403
    fetch.assert_not_called()
