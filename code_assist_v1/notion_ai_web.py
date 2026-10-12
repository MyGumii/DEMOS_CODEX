"""Public search adapters and bounded, DNS-pinned reads for the AI home.

Search receives only the user's current query, never private excerpts/history.
Credentials belong to the host environment, not the browser or conversations.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from io import BytesIO
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs, urlencode

import urllib3
import requests


class _PublicText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = {}
        self.containers = {"main": 0, "article": 0}
        self.text = {"all": [], "main": [], "article": []}

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "header", "footer", "form", "noscript", "svg", "iframe", "head"}:
            self.skip[tag] = self.skip.get(tag, 0) + 1
        if tag in self.containers:
            self.containers[tag] += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "section"}:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if tag in self.skip:
            self.skip[tag] = max(0, self.skip[tag] - 1)
        self.handle_data("\n")
        if tag in self.containers:
            self.containers[tag] = max(0, self.containers[tag] - 1)

    def handle_data(self, data):
        if any(self.skip.values()):
            return
        self.text["all"].append(data)
        for key, count in self.containers.items():
            if count:
                self.text[key].append(data)


def html_text(raw):
    parser = _PublicText()
    parser.feed(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw))
    text = ""
    for key in ("article", "main", "all"):
        text = "\n".join(line.strip() for line in "".join(parser.text[key]).splitlines() if line.strip())
        if len(text) > 40:
            break
    return text


class WebUnavailable(RuntimeError):
    pass


class _PaperPdf(HTMLParser):
    """Only explicit publisher metadata, not arbitrary links in a bibliography."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.url=None

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta' and attrs.get('name','').casefold()=='citation_pdf_url':
            self.url=attrs.get('content')


def paper_pdf_url(raw,base):
    if urlsplit(base).hostname not in {'proceedings.mlr.press','proceedings.neurips.cc','papers.nips.cc','aclanthology.org'}:
        return None
    parser=_PaperPdf()
    parser.feed(raw.decode('utf-8',errors='replace'))
    if not parser.url: return None
    try:
        return public_url(urljoin(base,parser.url))
    except ValueError:
        return None


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("invalid public URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("HTTPS public URLs only")
    host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("private host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("private host")
        if address.version == 6:
            host = "[" + host + "]"
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def _public_addresses(host):
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("private destination")
    return addresses


def public_request(url, guard, *, method="GET", headers=None, body=None, maximum=1048576):
    """Resolve and pin the public IP; validate every redirect and TLS hostname."""
    for redirects in range(3):
        guard()
        url = public_url(url)
        parts = urlsplit(url)
        address = _public_addresses(parts.hostname)[0]
        guard()
        # Connecting to the checked address prevents a second, unvalidated DNS lookup.
        pool = urllib3.HTTPSConnectionPool(address, port=443, server_hostname=parts.hostname,
                                         assert_hostname=parts.hostname, cert_reqs="CERT_REQUIRED",
                                         ca_certs=requests.certs.where(),
                                         timeout=urllib3.Timeout(connect=5, read=12), retries=False)
        response = None
        try:
            request_headers = {"User-Agent": "NotionResearch/1.0", "Accept-Encoding": "identity",
                               **(headers or {}), "Host": parts.hostname}
            response = pool.urlopen(method, urlunsplit(("", "", parts.path or "/", parts.query, "")),
                                    body=body, headers=request_headers, redirect=False, preload_content=False)
            guard()
            if response.status in (301, 302, 303, 307, 308):
                # Never forward provider credentials/body to a redirect target.
                if method != "GET" or headers or body or redirects == 2:
                    raise WebUnavailable("redirect refused")
                url = public_url(urljoin(url, response.headers.get("Location", "")))
                continue
            if response.status != 200:
                raise WebUnavailable("public request unavailable")
            data = bytearray()
            for chunk in response.stream(16384, decode_content=False):
                guard()
                data.extend(chunk)
                if len(data) > maximum:
                    raise WebUnavailable("public response exceeds budget")
            guard()
            return bytes(data), str(response.headers.get("Content-Type", "")), url
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise WebUnavailable("redirect limit")


class PublicWebSearch:
    @staticmethod
    def endpoint():
        raw = os.environ.get("CODE_ASSIST_SEARXNG_URL", "").strip()
        if not raw:
            return None
        parts = urlsplit(raw)
        # This deliberate loopback exception applies only to the configured search
        # gateway, never URLs returned by an engine or generated by a model.
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ("", "/")):
            raise ValueError("자체 SearXNG 주소는 http://127.0.0.1:포트 형식이어야 합니다.")
        return f"http://127.0.0.1:{parts.port}/search"

    def capability(self):
        try:
            configured = bool(self.endpoint())
        except ValueError:
            configured = False
        return {"enabled": configured, "provider": "searxng" if configured else None,
                "reason": "" if configured else "자체 웹 검색 서버 연결이 필요합니다."}

    def search(self, query, guard, *, limit=4):
        capability = self.capability()
        if not capability["enabled"]:
            raise WebUnavailable("자체 웹 검색 서버가 연결되지 않았습니다. 웹 검색을 끄거나 검색 서버 연결을 확인하세요.")
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError("웹 검색어는 600자 이내로 입력하세요.")
        limit = min(12, max(1, int(limit)))
        guard()
        with requests.Session() as session:
            session.trust_env = False
            with session.post(self.endpoint(), data={"q": query, "format": "json", "categories": "general,science"},
                              timeout=(3, 15), allow_redirects=False, stream=True) as response:
                guard()
                if response.status_code != 200:
                    raise WebUnavailable("자체 검색 서버의 응답을 받지 못했습니다.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    guard()
                    raw.extend(chunk)
                    if len(raw) > 1048576:
                        raise WebUnavailable("검색 결과가 허용된 크기를 넘었습니다.")
                data = json.loads(raw)
        guard()
        items = data.get("results", [])
        if not isinstance(items, list):
            raise WebUnavailable("검색 결과 형식을 확인하지 못했습니다.")
        if not items and data.get("unresponsive_engines"):
            raise WebUnavailable("검색엔진 응답을 받지 못했습니다. 잠시 후 다시 요청하세요.")
        # SearXNG combines general/scientific engines. Put exact topic matches
        # ahead of broad scientific results rather than trusting a mixed score.
        words = {word for word in re.findall(r"[\w-]+", query.casefold()) if len(word) > 2}
        def relevance(item):
            if not isinstance(item, dict):
                return -1
            title = set(re.findall(r"[\w-]+", str(item.get("title") or "").casefold()))
            body = set(re.findall(r"[\w-]+", str(item.get("content") or "").casefold()))
            hits = len(words & title)
            # Whole tokens prevent GAT from matching CGAT; prefer titles focused
            # on the requested topic over lengthy follow-up/application papers.
            return 6 * hits + len(words & body) + 30 * hits / max(1, len(title))
        items = sorted(items[:200], key=relevance, reverse=True)
        results, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                raw_url = item.get("url")
                if isinstance(raw_url, str) and raw_url.startswith("http://"):
                    raw_url = "https://" + raw_url[7:]
                url = public_url(raw_url)
            except (ValueError, TypeError):
                continue
            identity = url
            if urlsplit(url).hostname == "arxiv.org":
                identity = re.sub(r"(/abs/[\w.\-/]+?)v\d+(?=$|[?#])", r"\1", url)
            if identity in seen:
                continue
            seen.add(identity)
            snippet = html_text(str(item.get("description") or item.get("content") or "")[:12000])
            if snippet or item.get("title"):
                results.append({"url": url, "title": str(item.get("title") or url)[:200],
                                "excerpt": snippet[:2200], "content_status": "search_excerpt",
                                "search_partial": bool(data.get("unresponsive_engines"))})
            if len(results) == limit:
                break
        guard()
        return results

    def read(self, result, guard):
        raw, content_type, url = public_request(result["url"], guard)
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise WebUnavailable("HTML 또는 텍스트 본문만 읽을 수 있습니다.")
        if "text/html" in content_type:
            text = html_text(raw)
        else:
            text = raw.decode("utf-8", errors="replace")
        text = text.encode()[:10000].decode("utf-8", errors="ignore").strip()
        if len(text) < 40:
            raise WebUnavailable("본문을 읽지 못했습니다.")
        return {**result, "url": url, "excerpt": text, "content_status": "page_excerpt"}

    def read_document(self, result, guard):
        """Keep source text outside the model context; disclose extraction bounds."""
        url = result["url"]
        parts = urlsplit(url)
        if parts.hostname in {"arxiv.org", "www.arxiv.org"} and parts.path.startswith("/abs/"):
            url = "https://arxiv.org/pdf/" + parts.path[5:]
        elif parts.hostname == "openreview.net" and parts.path == "/forum":
            paper_id = parse_qs(parts.query).get("id", [""])[0]
            if paper_id:
                url = "https://openreview.net/pdf?" + urlencode({"id": paper_id})
        raw, content_type, resolved = public_request(url, guard, maximum=16 * 1024 * 1024)
        warnings, page_count, read_pages = [], None, None
        if 'text/html' in content_type:
            pdf=paper_pdf_url(raw,resolved)
            if pdf:
                try:
                    pdf_raw,pdf_type,pdf_resolved=public_request(pdf,guard,maximum=16 * 1024 * 1024)
                    if not pdf_raw.startswith(b'%PDF-'):
                        raise WebUnavailable('publisher PDF returned non-PDF content')
                    raw,content_type,resolved=pdf_raw,pdf_type,pdf_resolved
                except (WebUnavailable,ValueError):
                    guard()
                    warnings.append('출판사가 연결한 PDF를 읽지 못해 공개 페이지 본문만 확인했습니다.')
        if "application/pdf" in content_type or raw.startswith(b"%PDF-"):
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw))
            page_count = len(reader.pages)
            texts = []
            for index, page in enumerate(reader.pages):
                guard()
                if index >= 200 or sum(len(t.encode()) for t in texts) >= 500000:
                    warnings.append("PDF 추출 한도에 도달해 일부 페이지는 읽지 못했습니다.")
                    break
                text = page.extract_text() or ""
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "�", text)
                texts.append(f"[PDF page {index + 1}]\n{text}")
            read_pages = len(texts)
            text = "\n\n".join(texts)
            source_format = "pdf"
            if len(re.sub(r"\[PDF page \d+\]|\s", "", text)) < 100:
                raise WebUnavailable("PDF에서 읽을 수 있는 텍스트가 부족합니다. 이미지 문서는 별도 확인이 필요합니다.")
            if "�" in text:
                warnings.append("PDF의 일부 글자·수식은 정확히 추출되지 않았습니다.")
        elif "text/html" in content_type or "text/plain" in content_type:
            text = html_text(raw) if "text/html" in content_type else raw.decode("utf-8", errors="replace")
            source_format = "html" if "text/html" in content_type else "text"
        else:
            raise WebUnavailable("읽을 수 있는 HTML·텍스트·PDF 자료가 아닙니다.")
        guard()
        original_bytes = len(text.encode())
        text = text.encode()[:500000].decode("utf-8", errors="ignore").strip()
        if len(text) < 100:
            raise WebUnavailable("원문 텍스트가 부족합니다.")
        truncated = original_bytes > len(text.encode()) or bool(page_count and read_pages != page_count)
        arxiv = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", text[:6500])
        status = "partial_text" if truncated else "document_text"
        if source_format != "pdf" and (len(text) < 2000 or parts.hostname in {"arxiv.org", "openreview.net", "www.arxiv.org", "alphaxiv.org", "www.alphaxiv.org", "semanticscholar.org", "www.semanticscholar.org"}):
            status = "abstract_text"
        return {**result, "excerpt": text, "document_url": resolved, "source_format": source_format,
                "canonical_paper_id": "arxiv:" + arxiv[1] if arxiv else None,
                "page_count": page_count, "read_pages": read_pages, "warnings": warnings,
                "content_status": status, "document_truncated": truncated}

    @staticmethod
    def reference(result):
        url = public_url(result["url"])
        return {**result, "kind": "web", "id": url,
                "source_id": "W" + hashlib.sha256(url.encode()).hexdigest()[:12]}
