"""Tests for Claude Code plugin packaging structure."""
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent


def test_plugin_json_exists():
    """plugin.json must exist in .claude-plugin/."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "name" in data
    assert data["name"] == "ccsm"
    assert "version" in data


def test_mcp_json_exists():
    """.mcp.json must declare the ccsm MCP server."""
    path = PLUGIN_ROOT / ".mcp.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "mcpServers" in data
    assert "ccsm" in data["mcpServers"]
    server = data["mcpServers"]["ccsm"]
    assert server["type"] == "stdio"


def test_hooks_json_exists():
    """hooks.json must exist and be valid JSON."""
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "hooks" in data
    assert "SessionStart" in data["hooks"] or "SessionEnd" in data["hooks"]


def test_mcp_shim_exists():
    """Node.js MCP shim script must exist."""
    path = PLUGIN_ROOT / "scripts" / "mcp-shim.js"
    assert path.exists(), f"Missing {path}"
    content = path.read_text()
    assert "spawn" in content  # Must spawn Python process
    assert "ccsm.mcp.server" in content  # Must reference Python module


def test_pyproject_toml_valid():
    """pyproject.toml must have ccsm entry point."""
    path = PLUGIN_ROOT / "pyproject.toml"
    assert path.exists()
    content = path.read_text()
    assert 'name = "ccsm"' in content
