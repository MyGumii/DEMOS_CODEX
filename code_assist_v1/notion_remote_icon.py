"""Bounded public image previews. No catalog or file mutation occurs here."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from code_assist_v1.notion_workspace_images import MAX_BYTES, validate_image

TIMEOUT = 15
MAX_REDIRECTS = 3
_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='notion-icon-preview')
_slots = threading.BoundedSemaphore(4)
_translation_networks = tuple(ipaddress.ip_network(v) for v in ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32'))


def normalize_url(value: str) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 2048:
        raise ValueError('이미지의 http 또는 https 주소를 붙여넣어 주세요.')
    value = value.strip()
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('이미지 주소를 확인해 주세요.')
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or url.username is not None or url.password is not None or not url.hostname:
            raise ValueError()
        port = url.port
        if port == 0:
            raise ValueError()
        host = url.hostname.encode('idna').decode('ascii')
        if '%' in host:
            raise ValueError()
        if ':' not in host and not re.fullmatch(r'[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.?', host):
            raise ValueError()
        netloc = f'[{host}]' if ':' in host else host
        if port is not None:
            netloc += f':{port}'
        return urlunsplit((url.scheme, netloc, quote(url.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                           quote(url.query, safe="/%?:@!$&'()*+,;=-._~"), ''))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('이미지의 http 또는 https 주소를 확인해 주세요.') from exc


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or any(address in network for network in _translation_networks):
            return False
    return True


def _remaining(deadline: float, canceled: threading.Event) -> float:
    remaining = deadline - time.monotonic()
    if canceled.is_set() or remaining <= 0:
        raise ValueError('이미지를 불러오는 시간이 초과됐습니다. 다시 시도해 주세요.')
    return remaining


def _connect(url: str, deadline: float, canceled: threading.Event):
    """Resolve once; connect only checked numeric addresses, preserving TLS SNI."""
    target = urlsplit(url)
    host, port = target.hostname, target.port or (443 if target.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    _remaining(deadline, canceled)
    if not addresses or any(not _public_address(item[4][0]) for item in addresses):
        raise ValueError('외부에서 접근할 수 있는 이미지 주소를 사용해 주세요.')
    last_error = None
    for family, kind, protocol, _, address in addresses:
        stream = None
        try:
            stream = socket.socket(family, kind, protocol)
            stream.settimeout(min(5, _remaining(deadline, canceled)))
            stream.connect(address)
            if target.scheme == 'https':
                stream = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
            stream.settimeout(_remaining(deadline, canceled))
            connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline, canceled))
            connection.auto_open = 0  # Never let a retry resolve the host again.
            connection.sock = stream
            return connection, stream
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
            if stream is not None:
                stream.close()
        except ValueError:
            if stream is not None:
                stream.close()
            raise
    raise ValueError('이미지 주소에 연결하지 못했습니다.') from last_error


def _mime(raw: bytes) -> str:
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    raise ValueError('PNG, JPEG, WebP 이미지 주소를 사용해 주세요.')


def _fetch(url: str, canceled: threading.Event) -> tuple[bytes, str]:
    deadline = time.monotonic() + TIMEOUT
    for redirects in range(MAX_REDIRECTS + 1):
        _remaining(deadline, canceled)
        connection, stream = _connect(url, deadline, canceled)
        response = None
        try:
            target = urlsplit(url)
            path = target.path + ('?' + target.query if target.query else '')
            connection.request('GET', path, headers={'Accept': 'image/png,image/jpeg,image/webp',
                'Accept-Encoding': 'identity', 'User-Agent': 'NotionWorkspace-ImagePreview/1.0', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or redirects == MAX_REDIRECTS:
                    raise ValueError('이미지 주소의 이동이 너무 많습니다. 최종 이미지 주소를 사용해 주세요.')
                url = normalize_url(urljoin(url, location))
                continue  # Every redirect repeats address validation and pinning.
            if response.status != 200:
                raise ValueError('이미지를 불러오지 못했습니다. 공개된 이미지 주소인지 확인해 주세요.')
            if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise ValueError('압축된 이미지 응답을 사용할 수 없습니다.')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                raise ValueError('2MB 이하의 이미지를 사용해 주세요.')
            chunks, size = [], 0
            while True:
                # HTTPResponse closes its file after the final Content-Length
                # chunk; a Connection: close socket is no longer usable then.
                if response.isclosed():
                    break
                stream.settimeout(_remaining(deadline, canceled))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('2MB 이하의 이미지를 사용해 주세요.')
                chunks.append(chunk)
            raw = b''.join(chunks)
            mime = _mime(raw)
            declared = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            if declared not in ('', 'application/octet-stream', mime):
                raise ValueError('이미지 주소의 파일 형식이 일치하지 않습니다.')
            try:
                validate_image(raw, mime)
            except ValueError as exc:
                raise ValueError('2MB, 1,600만 화소 이하의 정지 이미지를 사용해 주세요.') from exc
            _remaining(deadline, canceled)
            return raw, mime
        finally:
            if response is not None:
                response.close()
            connection.close()
            stream.close()
    raise ValueError('이미지 주소를 확인해 주세요.')


def fetch_image(value: str) -> tuple[bytes, str]:
    url = normalize_url(value)
    if not _slots.acquire(blocking=False):
        raise ValueError('다른 이미지를 불러오는 중입니다. 잠시 후 다시 시도해 주세요.')
    canceled = threading.Event()

    def run():
        try:
            return _fetch(url, canceled)
        finally:
            _slots.release()

    try:
        future = _workers.submit(run)
    except Exception:
        _slots.release()
        raise
    try:
        return future.result(timeout=TIMEOUT)
    except FutureTimeout as exc:
        canceled.set()
        if future.cancel():
            _slots.release()
        raise ValueError('이미지를 불러오는 시간이 초과됐습니다. 다시 시도해 주세요.') from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('이미지 주소에 연결하지 못했습니다.') from exc
