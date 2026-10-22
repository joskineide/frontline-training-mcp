import pytest
from mcp import Client

from frontline_training_mcp.server import mcp

pytestmark = pytest.mark.anyio


async def test_get_module_is_listed_as_read_only_with_output_schema():
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["get_module"]
    assert tool.annotations.read_only_hint is True
    assert tool.output_schema["properties"].keys() == {
        "module_id",
        "title",
        "summary",
        "duration_minutes",
    }


async def test_get_module_returns_structured_content():
    async with Client(mcp) as client:
        result = await client.call_tool("get_module", {"module_id": "forklift-safety"})

    assert result.is_error is False
    assert result.structured_content["title"] == "Forklift Safety"


async def test_unknown_module_is_a_tool_error_not_a_protocol_error():
    async with Client(mcp) as client:
        result = await client.call_tool("get_module", {"module_id": "does-not-exist"})

    assert result.is_error is True
    assert "does-not-exist" in result.content[0].text
