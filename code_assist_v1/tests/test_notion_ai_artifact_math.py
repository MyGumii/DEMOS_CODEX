"""Offline slide math keeps model text inert and admits only bundled code."""
import base64
import hashlib
import html
import re

from code_assist_v1 import notion_ai_chat_artifacts as artifacts


def message(bullet):
    return {"text": "자료", "artifact": {"kind": "slides", "title": "GAT",
            "slides": [{"title": "선형 변환", "bullets": [bullet]}]}}


def test_math_export_is_self_contained_and_csp_hashes_only_fixed_renderer_scripts():
    data, mime, name = artifacts.export(message(r"변환 $z_i = W h_i$, $$\frac{a}{b}$$"), "html")
    body = data.decode()
    assert mime == "text/html; charset=utf-8" and name == "ai-slides.html"
    scripts = re.findall(r"<script>(.*?)</script>", body, re.S)
    assert len(scripts) == 3
    policy = html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', body)[1])
    allowed = re.search(r"script-src ([^;]+)", policy)[1].split()
    expected = ["'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'" for script in scripts]
    assert allowed == expected
    assert "font-src data:" in policy and "default-src 'none'" in policy
    assert "'unsafe-inline'" not in re.search(r"script-src ([^;]+)", policy)[1]
    assert "data:font/woff2;base64," in body
    assert not re.search(r'url\(["\']?fonts/', body)
    assert not re.search(r'<script[^>]+src=|<link[^>]+href=', body)
    assert 'NotionAiAnswer.renderText(el,el.textContent)' in scripts[-1]
    assert "trust: false" in scripts[1] and "maxExpand: 1000" in scripts[1]


def test_math_export_does_not_interpolate_model_text_into_executable_code():
    attack = '</script><script>attack_marker()</script><img src=x onerror="attack_marker()">'
    body = artifacts.export(message("$x$ " + attack), "html")[0].decode()
    assert html.escape(attack) in body
    assert all("attack_marker" not in source for source in re.findall(r"<script>(.*?)</script>", body, re.S))
    assert '<img src=x' not in body and '<script>attack_marker' not in body


def test_plain_slide_export_stays_script_free_and_preserves_literal_text():
    body = artifacts.export(message("한글 자료 <script>literal</script>"), "html")[0].decode()
    assert "<script>" not in body and "&lt;script&gt;literal&lt;/script&gt;" in body
    assert "font-src" not in body and "script-src" not in body


def test_saved_ascii_math_slide_also_includes_the_safe_offline_renderer():
    body = artifacts.export(message("alpha_ij = exp(e_ij) / sum(exp(e_ik))"), "html")[0].decode()
    assert len(re.findall(r"<script>(.*?)</script>", body, re.S)) == 3
    assert "alpha_ij = exp(e_ij) / sum(exp(e_ik))" in body
    assert "NotionAiAnswer.renderText" in body and "font-src data:" in body
