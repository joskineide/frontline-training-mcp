"""The tool contract the model reads: names, descriptions, schemas and annotations.

No database needed: listing tools never touches the database or the caller.
"""

import json
import os
from pathlib import Path

import pytest
from mcp import Client

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import Identity
from frontline_training_mcp.server import create_server

pytestmark = pytest.mark.anyio

SNAPSHOT = Path(__file__).parent / "snapshots" / "tools.json"
READ_TOOLS = {
    "search_training",
    "get_module",
    "my_assignments",
    "list_announcements",
    "team_progress",
}


async def list_tools():
    server = create_server(
        Database("postgresql://unused@127.0.0.1:1/unused"),  # never connected to here
        lambda: Identity(tenant_id="unused", user_id="unused", scopes=frozenset()),
    )
    async with Client(server) as client:
        return sorted((await client.list_tools()).tools, key=lambda tool: tool.name)


async def test_read_tools_are_read_only_and_closed_world():
    tools = {tool.name: tool for tool in await list_tools()}
    assert READ_TOOLS <= tools.keys()
    for name in READ_TOOLS:
        assert tools[name].annotations.read_only_hint is True, name
        assert tools[name].annotations.open_world_hint is False, name


async def test_no_tool_accepts_a_tenant():
    for tool in await list_tools():
        assert "tenant_id" not in tool.input_schema.get("properties", {}), tool.name


async def test_the_contract_matches_the_reviewed_snapshot():
    """Descriptions and schemas are prompts: a change must be reviewed, and re-evaluated.

    To accept a reviewed change: UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py
    """
    contract = [
        tool.model_dump(by_alias=True, exclude_none=True, mode="json")
        for tool in await list_tools()
    ]
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
        SNAPSHOT.write_text(text, encoding="utf-8")
    reviewed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert contract == reviewed, "The tool contract changed: review the diff, then update."
