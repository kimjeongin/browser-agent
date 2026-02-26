"""Tests for Chat Agent tools and utilities.

Covered:
- _extract_text   (HTML stripping utility)
- web_search      (DuckDuckGo search with mocked httpx)
- fetch_webpage   (webpage fetching with mocked httpx)
- /health endpoint
"""

import httpx
import pytest
import respx

from main import _extract_text, fetch_webpage, web_search


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    """Test HTML-to-text extraction utility."""

    def test_strips_simple_tags(self):
        assert _extract_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_removes_script_tags_and_content(self):
        html = '<div>Text<script>alert("xss")</script>More</div>'
        result = _extract_text(html)
        assert "alert" not in result
        assert "Text" in result
        assert "More" in result

    def test_removes_style_tags_and_content(self):
        html = "<div>Text<style>.red{color:red}</style>More</div>"
        result = _extract_text(html)
        assert "color" not in result
        assert "Text" in result
        assert "More" in result

    def test_unescapes_html_entities(self):
        result = _extract_text("&amp; &lt;tag&gt; &quot;quoted&quot;")
        assert "& <tag>" in result
        assert '"quoted"' in result

    def test_collapses_whitespace(self):
        result = _extract_text("<p>  lots   of    spaces  </p>")
        assert result == "lots of spaces"

    def test_empty_string(self):
        assert _extract_text("") == ""

    def test_nested_script_in_body(self):
        html = """<body>
        <script type="text/javascript">
            var x = 1;
            var y = 2;
        </script>
        <p>Visible content</p>
        </body>"""
        result = _extract_text(html)
        assert "var x" not in result
        assert "Visible content" in result


# ---------------------------------------------------------------------------
# web_search (mocked httpx)
# ---------------------------------------------------------------------------

_DUCKDUCKGO_LITE_RESPONSE = """
<html>
<body>
<table>
    <tr>
        <td>
            <a rel="nofollow" href="https://example.com/result1" class="result-link">
                Example Result One
            </a>
        </td>
    </tr>
    <tr>
        <td class="result-snippet">This is the first snippet about the topic.</td>
    </tr>
    <tr>
        <td>
            <a rel="nofollow" href="https://example.com/result2" class="result-link">
                Example Result Two
            </a>
        </td>
    </tr>
    <tr>
        <td class="result-snippet">This is the second snippet with details.</td>
    </tr>
</table>
</body>
</html>
"""

_DUCKDUCKGO_NO_RESULTS = """
<html>
<body>
<table>
    <tr><td>No results found.</td></tr>
</table>
</body>
</html>
"""


class TestWebSearch:
    """Test DuckDuckGo search tool with mocked HTTP responses."""

    @respx.mock
    async def test_returns_parsed_results(self):
        respx.get("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(200, text=_DUCKDUCKGO_LITE_RESPONSE),
        )

        results = await web_search.ainvoke({"query": "test query", "max_results": 5})

        assert isinstance(results, list)
        assert len(results) == 2

        assert results[0]["title"] == "Example Result One"
        assert results[0]["url"] == "https://example.com/result1"
        assert "first snippet" in results[0]["snippet"]

        assert results[1]["title"] == "Example Result Two"
        assert results[1]["url"] == "https://example.com/result2"

    @respx.mock
    async def test_returns_no_results_message(self):
        respx.get("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(200, text=_DUCKDUCKGO_NO_RESULTS),
        )

        results = await web_search.ainvoke({"query": "nonexistent_gibberish_xyz"})

        assert len(results) == 1
        assert results[0]["title"] == "No results"
        assert "No results found" in results[0]["snippet"]

    @respx.mock
    async def test_respects_max_results(self):
        respx.get("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(200, text=_DUCKDUCKGO_LITE_RESPONSE),
        )

        results = await web_search.ainvoke({"query": "test", "max_results": 1})

        assert len(results) == 1
        assert results[0]["title"] == "Example Result One"


# ---------------------------------------------------------------------------
# fetch_webpage (mocked httpx)
# ---------------------------------------------------------------------------

_SAMPLE_WEBPAGE = """
<html>
<head><title>Test Page Title</title></head>
<body>
    <h1>Main Heading</h1>
    <p>This is the main content of the test page.</p>
    <script>console.log("hidden");</script>
    <p>More visible content here.</p>
</body>
</html>
"""


class TestFetchWebpage:
    """Test webpage fetching tool with mocked HTTP responses."""

    @respx.mock
    async def test_fetches_and_extracts_content(self):
        respx.get("https://example.com/page").mock(
            return_value=httpx.Response(200, text=_SAMPLE_WEBPAGE),
        )

        result = await fetch_webpage.ainvoke({"url": "https://example.com/page"})

        assert result["url"] == "https://example.com/page"
        assert result["title"] == "Test Page Title"
        assert "Main Heading" in result["content"]
        assert "main content" in result["content"]
        assert "console.log" not in result["content"]

    @respx.mock
    async def test_truncates_at_max_chars(self):
        respx.get("https://example.com/long").mock(
            return_value=httpx.Response(200, text=_SAMPLE_WEBPAGE),
        )

        result = await fetch_webpage.ainvoke({
            "url": "https://example.com/long",
            "max_chars": 20,
        })

        assert result["content"].endswith("... [truncated]")
        # Content before truncation marker should be at most max_chars
        content_before_marker = result["content"].replace("... [truncated]", "")
        assert len(content_before_marker) == 20

    @respx.mock
    async def test_handles_page_without_title(self):
        no_title_html = "<html><body><p>Content only</p></body></html>"
        respx.get("https://example.com/notitle").mock(
            return_value=httpx.Response(200, text=no_title_html),
        )

        result = await fetch_webpage.ainvoke({"url": "https://example.com/notitle"})

        assert result["title"] == ""
        assert "Content only" in result["content"]


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Test the ACP health endpoint."""

    async def test_health_returns_ok(self, chat_client):
        response = await chat_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
