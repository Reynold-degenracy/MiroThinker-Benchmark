import json
import os

import pytest

from miroflow_tools.dev_mcp_servers import jina_scrape_llm_summary as jina_module
from miroflow_tools.mcp_servers import searching_google_mcp_server
from miroflow_tools.mcp_servers import serper_mcp_server


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_google_and_jina_tools_are_registered_without_agent_loop():
    google_tools = await searching_google_mcp_server.mcp.list_tools()
    jina_tools = await jina_module.mcp.list_tools()

    google_tool_names = {tool.name for tool in google_tools}
    jina_tool_names = {tool.name for tool in jina_tools}

    assert "google_search" in google_tool_names
    assert "scrape_website" in google_tool_names
    assert "scrape_and_extract_info" in jina_tool_names


def test_serper_google_search_returns_filtered_json(monkeypatch):
    monkeypatch.setattr(serper_mcp_server, "SERPER_API_KEY", "test-serper-key")
    captured = {}

    def fake_make_serper_request(payload, headers):
        captured["payload"] = payload
        captured["headers"] = headers
        return _FakeResponse(
            {
                "organic": [
                    {
                        "title": "keep me",
                        "link": "https://example.com/article",
                        "snippet": "useful",
                    },
                    {
                        "title": "drop me",
                        "link": "https://huggingface.co/datasets/demo/test",
                    },
                ],
                "searchParameters": {"q": payload["q"], "gl": payload["gl"]},
            }
        )

    monkeypatch.setattr(serper_mcp_server, "make_serper_request", fake_make_serper_request)

    raw = serper_mcp_server.google_search(
        q="  latest example query  ",
        gl="us",
        hl="en",
        num=5,
        page=2,
        autocorrect=False,
    )
    result = json.loads(raw)

    assert captured["headers"]["X-API-KEY"] == "test-serper-key"
    assert captured["payload"] == {
        "q": "latest example query",
        "gl": "us",
        "hl": "en",
        "num": 5,
        "page": 2,
        "autocorrect": False,
    }
    assert [item["link"] for item in result["organic"]] == ["https://example.com/article"]
    assert result["searchParameters"]["q"] == "latest example query"


def test_serper_google_search_rejects_empty_query(monkeypatch):
    monkeypatch.setattr(serper_mcp_server, "SERPER_API_KEY", "test-serper-key")

    raw = serper_mcp_server.google_search(q="   ")
    result = json.loads(raw)

    assert result["success"] is False
    assert "cannot be empty" in result["error"]


@pytest.mark.asyncio
async def test_jina_scrape_and_extract_info_combines_scrape_and_llm(monkeypatch):
    monkeypatch.setattr(jina_module, "SUMMARY_LLM_MODEL_NAME", "fake-summary-model")

    async def fake_scrape_url_with_jina(url, custom_headers=None):
        assert url == "https://example.com/post"
        assert custom_headers == {"X-Test": "1"}
        return {
            "success": True,
            "content": "line1\nline2",
            "error": "",
            "line_count": 2,
            "char_count": 11,
            "last_char_line": 2,
            "all_content_displayed": True,
        }

    async def fake_extract_info_with_llm(url, content, info_to_extract, model, max_tokens):
        assert url == "https://example.com/post"
        assert content == "line1\nline2"
        assert info_to_extract == "what happened?"
        assert model == "fake-summary-model"
        assert max_tokens == 8192
        return {
            "success": True,
            "extracted_info": "the extracted answer",
            "error": "",
            "model_used": model,
            "tokens_used": 123,
        }

    monkeypatch.setattr(jina_module, "scrape_url_with_jina", fake_scrape_url_with_jina)
    monkeypatch.setattr(jina_module, "extract_info_with_llm", fake_extract_info_with_llm)

    raw = await jina_module.scrape_and_extract_info(
        url="https://example.com/post",
        info_to_extract="what happened?",
        custom_headers={"X-Test": "1"},
    )
    result = json.loads(raw)

    assert result == {
        "success": True,
        "url": "https://example.com/post",
        "extracted_info": "the extracted answer",
        "error": "",
        "scrape_stats": {
            "line_count": 2,
            "char_count": 11,
            "last_char_line": 2,
            "all_content_displayed": True,
        },
        "model_used": "fake-summary-model",
        "tokens_used": 123,
    }


@pytest.mark.asyncio
async def test_jina_scrape_and_extract_info_falls_back_to_python(monkeypatch):
    calls = []
    monkeypatch.setattr(jina_module, "SUMMARY_LLM_MODEL_NAME", "fake-summary-model")

    async def fake_scrape_url_with_jina(url, custom_headers=None):
        calls.append("jina")
        return {
            "success": False,
            "content": "",
            "error": "jina unavailable",
            "line_count": 0,
            "char_count": 0,
            "last_char_line": 0,
            "all_content_displayed": False,
        }

    async def fake_scrape_url_with_python(url, custom_headers=None):
        calls.append("python")
        return {
            "success": True,
            "content": "fallback content",
            "error": "",
            "line_count": 1,
            "char_count": 16,
            "last_char_line": 1,
            "all_content_displayed": True,
        }

    async def fake_extract_info_with_llm(url, content, info_to_extract, model, max_tokens):
        calls.append("llm")
        assert content == "fallback content"
        return {
            "success": True,
            "extracted_info": "fallback answer",
            "error": "",
            "model_used": model,
            "tokens_used": 7,
        }

    monkeypatch.setattr(jina_module, "scrape_url_with_jina", fake_scrape_url_with_jina)
    monkeypatch.setattr(jina_module, "scrape_url_with_python", fake_scrape_url_with_python)
    monkeypatch.setattr(jina_module, "extract_info_with_llm", fake_extract_info_with_llm)

    raw = await jina_module.scrape_and_extract_info(
        url="https://example.com/fallback",
        info_to_extract="key fact",
    )
    result = json.loads(raw)

    assert calls == ["jina", "python", "llm"]
    assert result["success"] is True
    assert result["extracted_info"] == "fallback answer"
    assert result["scrape_stats"]["char_count"] == 16


@pytest.mark.asyncio
async def test_real_google_search_mcp_smoke_when_keys_present():
    if os.getenv("RUN_REAL_MCP_SMOKE") != "1":
        pytest.skip("未设置 RUN_REAL_MCP_SMOKE=1，跳过真实 google_search MCP 烟测")
    if not os.getenv("SERPER_API_KEY"):
        pytest.skip("SERPER_API_KEY 未配置，跳过真实 google_search MCP 烟测")

    searching_google_mcp_server.SERPER_API_KEY = os.environ["SERPER_API_KEY"]
    searching_google_mcp_server.SERPER_BASE_URL = os.getenv(
        "SERPER_BASE_URL", "https://google.serper.dev"
    )

    result = await searching_google_mcp_server.google_search(
        q="Example Domain",
        num=3,
    )

    assert "[ERROR]" not in result
    payload = json.loads(result)
    assert "organic" in payload
    assert isinstance(payload["organic"], list)


@pytest.mark.asyncio
async def test_real_jina_scrape_mcp_smoke_when_keys_present():
    if os.getenv("RUN_REAL_MCP_SMOKE") != "1":
        pytest.skip("未设置 RUN_REAL_MCP_SMOKE=1，跳过真实 jina scrape MCP 烟测")
    required = ["JINA_API_KEY", "SUMMARY_LLM_BASE_URL", "SUMMARY_LLM_MODEL_NAME"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        pytest.skip(f"缺少真实 jina scrape 烟测所需环境变量: {', '.join(missing)}")

    jina_module.JINA_API_KEY = os.environ["JINA_API_KEY"]
    jina_module.JINA_BASE_URL = os.getenv("JINA_BASE_URL", "https://r.jina.ai")
    jina_module.SUMMARY_LLM_BASE_URL = os.environ["SUMMARY_LLM_BASE_URL"]
    jina_module.SUMMARY_LLM_MODEL_NAME = os.environ["SUMMARY_LLM_MODEL_NAME"]
    jina_module.SUMMARY_LLM_API_KEY = os.getenv("SUMMARY_LLM_API_KEY", "")

    result = await jina_module.scrape_and_extract_info(
        url="https://example.com",
        info_to_extract="Summarize the key topic in one sentence.",
    )

    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["extracted_info"]
