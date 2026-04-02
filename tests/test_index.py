"""Tests for ccsm.core.index — SessionIndex with fuzzy search."""

from datetime import datetime

from ccsm.core.index import IndexEntry, SessionIndex


def _make_entries() -> list[IndexEntry]:
    """Return three well-known test entries (s1, s2, s3)."""
    now = datetime(2026, 4, 1, 12, 0, 0)
    return [
        IndexEntry(
            session_id="s1",
            worktree="main",
            project="GUI",
            title="fix-login-bug",
            intent="Fix the login validation bug",
            first_user_content="The login form crashes when...",
            last_message_at=now,
            status="active",
            tags=["bug", "urgent"],
        ),
        IndexEntry(
            session_id="s2",
            worktree="panel",
            project="GUI",
            title="add-auth-feature",
            intent="Add OAuth2 authentication",
            first_user_content="We need OAuth2 integration",
            last_message_at=now,
            status="active",
            tags=["feature"],
        ),
        IndexEntry(
            session_id="s3",
            worktree="main",
            project="GUI",
            title="refactor-api",
            intent="Refactor API client",
            first_user_content="The API client needs cleanup",
            last_message_at=now,
            status="done",
            tags=[],
        ),
    ]


def _build_index() -> SessionIndex:
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    return idx


# ── search tests ─────────────────────────────────────────────────────────────


def test_search_by_title():
    idx = _build_index()
    results = idx.search("login")
    assert len(results) == 1
    assert results[0].session_id == "s1"


def test_search_by_intent():
    idx = _build_index()
    results = idx.search("OAuth")
    assert len(results) == 1
    assert results[0].session_id == "s2"


def test_search_by_content():
    idx = _build_index()
    results = idx.search("crashes")
    assert len(results) == 1
    assert results[0].session_id == "s1"


def test_search_fuzzy():
    """'auth' appears in s2's title and intent."""
    idx = _build_index()
    results = idx.search("auth")
    ids = {r.session_id for r in results}
    assert "s2" in ids


# ── filter tests ─────────────────────────────────────────────────────────────


def test_filter_by_worktree():
    idx = _build_index()
    results = idx.search("", worktree="panel")
    assert len(results) == 1
    assert results[0].session_id == "s2"


def test_filter_by_project():
    idx = _build_index()
    results = idx.search("", project="GUI")
    assert len(results) == 3


# ── listing / limit ──────────────────────────────────────────────────────────


def test_list_all_no_limit():
    idx = _build_index()
    results = idx.search("")
    assert len(results) == 3


# ── persistence ──────────────────────────────────────────────────────────────


def test_save_and_load(tmp_path):
    idx = _build_index()
    p = tmp_path / "index.json"
    idx.save(p)

    loaded = SessionIndex.load(p)
    results = loaded.search("login")
    assert len(results) == 1
    assert results[0].session_id == "s1"
    assert results[0].last_message_at == datetime(2026, 4, 1, 12, 0, 0)
