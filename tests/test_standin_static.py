"""The stand-in's browser code may be AI-generated, but it must never parse user text as HTML."""

import re

from frontline_training_mcp.standin.app import STATIC

HTML_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")


def test_the_ui_never_parses_text_as_html():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert [sink for sink in HTML_SINKS if sink in script] == []


def test_the_page_has_no_inline_script():
    # The Content-Security-Policy forbids inline script; the page must not depend on any.
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", page) == []  # <script> without src
    assert re.findall(r"<[^>]*\son[a-z]+\s*=", page, re.IGNORECASE) == []  # onclick=, onload=…
    assert "javascript:" not in page
