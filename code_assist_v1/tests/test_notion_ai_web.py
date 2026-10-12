"""Public fetch fencing and the separately configured loopback search gateway."""
import json
from unittest.mock import Mock

import pytest

from code_assist_v1 import notion_ai_web as web


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost/x", "https://x.local/x",
                                "https://user:pass@example.com", "https://example.com:8080", "file:///c:/secret", "https://example.com/\n",
                                "https://127.0.0.1/x", "https://[::1]/x", "https://localhost./x"])
def test_nonpublic_or_credential_urls_are_rejected(url):
    with pytest.raises(ValueError):
        web.public_url(url)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.8"], ["169.254.169.254"], ["::1"], ["93.184.216.34", "127.0.0.1"]])
def test_dns_private_and_mixed_addresses_fail_before_a_connection(monkeypatch, addresses):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443)) for ip in addresses])
    factory = Mock(); monkeypatch.setattr(web.urllib3, "HTTPSConnectionPool", factory)
    with pytest.raises(ValueError, match="private destination"):
        web.public_request("https://example.com/paper", lambda: None)
    assert not factory.called


def test_dns_is_pinned_and_tls_checks_original_hostname(monkeypatch):
    dns = Mock(return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(web.socket, "getaddrinfo", dns)
    response = Mock(status=200, headers={"Content-Type": "text/html"})
    response.stream.return_value = [b"<article>Public paper body</article>"]
    pool = Mock(); pool.urlopen.return_value = response
    factory = Mock(return_value=pool); monkeypatch.setattr(web.urllib3, "HTTPSConnectionPool", factory)
    raw, _, url = web.public_request("https://example.com/paper#fragment", lambda: None)
    assert dns.call_count == 1
    assert factory.call_args.args == ("93.184.216.34",)
    assert factory.call_args.kwargs["server_hostname"] == "example.com"
    assert factory.call_args.kwargs["assert_hostname"] == "example.com"
    assert factory.call_args.kwargs["cert_reqs"] == "CERT_REQUIRED"
    assert pool.urlopen.call_args.kwargs["headers"]["Host"] == "example.com"
    assert url == "https://example.com/paper" and b"Public" in raw
    assert response.close.called and pool.close.called


def test_redirect_to_loopback_is_rejected_without_second_fetch(monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1" if a[0] == "127.0.0.1" else "93.184.216.34", 443))])
    response = Mock(status=302, headers={"Location": "https://127.0.0.1/private"})
    pool = Mock(); pool.urlopen.return_value = response
    monkeypatch.setattr(web.urllib3, "HTTPSConnectionPool", lambda *a, **k: pool)
    with pytest.raises(ValueError):
        web.public_request("https://example.com/paper", lambda: None)
    assert pool.urlopen.call_count == 1 and response.close.called


def test_budget_and_revocation_close_public_response(monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    response = Mock(status=200, headers={"Content-Type": "text/html"}); response.stream.return_value = [b"x" * 30]
    pool = Mock(); pool.urlopen.return_value = response
    monkeypatch.setattr(web.urllib3, "HTTPSConnectionPool", lambda *a, **k: pool)
    with pytest.raises(web.WebUnavailable, match="budget"):
        web.public_request("https://example.com/paper", lambda: None, maximum=20)
    assert response.close.called and pool.close.called
    factory = Mock(); monkeypatch.setattr(web.urllib3, "HTTPSConnectionPool", factory)
    with pytest.raises(PermissionError):
        web.public_request("https://example.com/paper", lambda: (_ for _ in ()).throw(PermissionError("revoked")))
    assert not factory.called


@pytest.mark.parametrize("endpoint", ["http://localhost:10324", "http://127.0.0.1:10324/private", "https://example.com", "http://user:pass@127.0.0.1:10324", "http://127.0.0.1:10324/?q=private"])
def test_search_gateway_is_exact_loopback_configuration_only(monkeypatch, endpoint):
    monkeypatch.setenv("CODE_ASSIST_SEARXNG_URL", endpoint)
    assert web.PublicWebSearch().capability()["enabled"] is False


def test_searxng_request_has_only_query_and_bounded_public_results(monkeypatch):
    monkeypatch.setenv("CODE_ASSIST_SEARXNG_URL", "http://127.0.0.1:10324")
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
    response.iter_content.return_value = [json.dumps({"results": [
        {"url": "https://example.com/paper", "title": "Paper", "content": "<b>Public</b> paper evidence."},
        {"url": "http://127.0.0.1/private", "title": "private", "content": "ignore"}], "unresponsive_engines": [["blocked", "timeout"]]}).encode()]
    session = Mock(); session.post.return_value = response
    session.__enter__ = Mock(return_value=session); session.__exit__ = Mock()
    monkeypatch.setattr(web.requests, "Session", lambda: session)
    results = web.PublicWebSearch().search("graph attention", lambda: None)
    assert len(results) == 1 and "<b>" not in results[0]["excerpt"]
    assert results[0]["search_partial"] is True
    args = session.post.call_args
    assert args.args == ("http://127.0.0.1:10324/search",)
    assert args.kwargs["data"] == {"q": "graph attention", "format": "json", "categories": "general,science"}
    assert args.kwargs["allow_redirects"] is False and session.trust_env is False


def test_topic_ranking_prefers_original_and_keeps_empty_snippet_for_body_read(monkeypatch):
    monkeypatch.setenv("CODE_ASSIST_SEARXNG_URL", "http://127.0.0.1:10324")
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
    response.iter_content.return_value = [json.dumps({"results": [
        {"url": "https://example.com/CGAT", "title": "CGAT: Classification of Research Papers Using Contrastive Graph Attention Networks", "content": "GAT original paper application"},
        {"url": "https://arxiv.org/abs/1710.10903v3", "title": "Graph Attention Networks", "content": "Graph attention network paper"},
        {"url": "https://arxiv.org/abs/1710.10903", "title": "Graph Attention Networks", "content": ""},
        {"url": "https://example.com/source", "title": "Graph Attention Networks", "content": ""}]}).encode()]
    session = Mock(); session.post.return_value = response
    session.__enter__ = Mock(return_value=session); session.__exit__ = Mock()
    monkeypatch.setattr(web.requests, "Session", lambda: session)
    results = web.PublicWebSearch().search("Graph Attention Networks GAT original paper", lambda: None)
    assert results[0]["url"] == "https://arxiv.org/abs/1710.10903v3"
    assert sum("arxiv.org" in result["url"] for result in results) == 1
    assert any(result["url"] == "https://example.com/source" and not result["excerpt"] for result in results)


def test_html_read_uses_main_content_and_ignores_scripts_forms_and_navigation():
    text = web.html_text("<head>SECRET HEAD</head><nav>NAV</nav><main><article><h1>Paper</h1><p>"
        "Public research evidence that is long enough to select the article body.</p>"
        "<script>DO_NOT_EXECUTE</script><form>DO_NOT_SEND</form></article></main><footer>FOOTER</footer>")
    assert "Public research" in text and "Paper" in text
    assert all(item not in text for item in ("SECRET", "NAV", "DO_NOT", "FOOTER"))


def test_primary_publisher_pdf_metadata_ignores_arbitrary_links_and_private_destinations():
    html=b'<meta name="citation_pdf_url" content="/paper/paper.pdf"><a href="other.pdf">other work</a>'
    assert web.paper_pdf_url(html,'https://proceedings.mlr.press/v267/paper.html')=='https://proceedings.mlr.press/paper/paper.pdf'
    assert web.paper_pdf_url(html,'https://example.com/paper.html') is None
    assert web.paper_pdf_url(b'<a href="other.pdf">other work</a>','https://proceedings.mlr.press/paper.html') is None
    for url in ('https://127.0.0.1/private','https://user:pass@example.com/paper.pdf','http://example.com/paper.pdf'):
        assert web.paper_pdf_url(('<meta name="citation_pdf_url" content="'+url+'">').encode(),'https://proceedings.mlr.press/paper.html') is None


def test_publisher_pdf_failure_preserves_available_html_and_revocation_still_stops_read(monkeypatch):
    html=b'<meta name="citation_pdf_url" content="https://example.com/paper.pdf"><main>'+b'Original paper abstract. '*12+b'</main>'
    calls=[]
    def read(url,guard,**kw):
        guard(); calls.append(url)
        if len(calls)>1: raise web.WebUnavailable('PDF blocked')
        return html,'text/html',url
    monkeypatch.setattr(web,'public_request',read)
    result=web.PublicWebSearch().read_document({'url':'https://proceedings.mlr.press/paper.html','title':'Original'},lambda:None)
    assert result['source_format']=='html' and result['content_status']=='abstract_text' and result['warnings']
    assert 'Original paper abstract' in result['excerpt'] and len(calls)==2
    calls.clear()
    def revoke():
        if calls: raise PermissionError('revoked')
    with pytest.raises(PermissionError):
        web.PublicWebSearch().read_document({'url':'https://proceedings.mlr.press/paper.html','title':'Original'},revoke)
