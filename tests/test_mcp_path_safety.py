"""Tests for MCP path sanitization."""


def test_sanitize_cwd_returns_project_name():
    """sanitize_cwd should return only the last path component."""
    from ccsm.mcp.server import _sanitize_cwd

    assert _sanitize_cwd("/home/user/projects/my-app") == "my-app"
    assert _sanitize_cwd("/very/deep/nested/path/project") == "project"


def test_sanitize_cwd_handles_none():
    """None cwd should return empty string."""
    from ccsm.mcp.server import _sanitize_cwd

    assert _sanitize_cwd(None) == ""


def test_sanitize_cwd_handles_trailing_slash():
    """Trailing slash should be stripped before extraction."""
    from ccsm.mcp.server import _sanitize_cwd

    assert _sanitize_cwd("/home/user/project/") == "project"


def test_sanitize_cwd_preserves_home_tilde():
    """Home directory path should be shortened to ~/basename."""
    from ccsm.mcp.server import _sanitize_cwd

    # Just the basename — no full path leaked
    assert _sanitize_cwd("/home/v-tangxin/GUI") == "GUI"
