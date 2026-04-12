"""Part 3 — Session Detail panel simplification.

These tests assert the post-simplification structure:
  - No session_card header block (deleted — redundant with list card)
  - No CONTEXT SUMMARY collapsible (deleted — overlaps digest)
  - No standalone WHERE YOU LEFT OFF (merged into LAST EXCHANGE)
  - No decorative emoji in section titles/fields
  - Only 2 Collapsibles: WHAT WAS DONE + LAST EXCHANGE, both collapsed by default
  - LAST EXCHANGE carries the merged Next / Insight rows

The tests mount SessionDetail inside a minimal Textual App (not the full
CCSMApp/Drawer stack) for fast, focused verification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from ccsm.models.session import (
    Breakpoint,
    Priority,
    SessionDetailData,
    SessionDigest,
    SessionInfo,
    SessionSummary,
    Status,
)
from ccsm.tui.widgets.session_detail import SessionDetail


# ── Fixtures ──────────────────────────────────────────────────────────────


def _fake_session() -> SessionInfo:
    return SessionInfo(
        session_id="abc-12345678",
        project_dir="-home-user-project",
        jsonl_path=Path("/tmp/fake.jsonl"),
        display_name="Fix OAuth refresh logic",
        status=Status.ACTIVE,
        priority=Priority.FOCUS,
        message_count=42,
        first_timestamp=datetime(2026, 4, 12, 10, tzinfo=timezone.utc),
        last_timestamp=datetime(2026, 4, 12, 15, tzinfo=timezone.utc),
        model_name="claude-sonnet-4-5",
        total_input_tokens=1000,
        total_output_tokens=2000,
    )


def _fake_summary() -> SessionSummary:
    digest = SessionDigest(
        progress="Implemented OAuth token refresh logic.",
        breakpoint="Waiting for integration test results.",
        decisions=["Use refresh_token rotation"],
        todo=["Add rate limit", "Write docs"],
    )
    return SessionSummary(
        session_id="abc-12345678",
        mode="llm",
        milestones=[],
        digest=digest,
        breakpoint=Breakpoint(
            milestone_label="Testing",
            detail="Running integration tests",
            last_topic="debugging OAuth refresh",
        ),
        key_insights=["refresh_token must be rotated on each use"],
    )


def _fake_detail_data() -> SessionDetailData:
    return SessionDetailData(
        session_id="abc-12345678",
        files_edited=["src/auth.py"],
        commands_run=["pytest tests/"],
        files_read=["README.md"],
        last_user_msg="please fix the refresh token bug",
        last_assistant_msg="Last AI reply content with some detail",
    )


class _DetailHarness(App):
    """Minimal Textual App that hosts only a SessionDetail widget."""

    CSS_PATH = Path(__file__).resolve().parents[1] / "ccsm" / "tui" / "styles" / "claude_native.tcss"

    def compose(self) -> ComposeResult:
        yield SessionDetail()


def _collect_static_text(root) -> str:
    """Walk all `Static` widgets under `root` and concatenate their textual content.

    Textual's `Static` stores its content in a private `__content` slot; we pull it
    via the name-mangled attribute plus a `render()` fallback so the helper works
    across minor Textual releases.
    """
    from textual.widgets import Static

    parts: list[str] = []
    for w in root.query(Static):
        content = getattr(w, "_Static__content", None)
        if content is None:
            try:
                content = w.render()
            except Exception:
                content = ""
        try:
            parts.append(str(content))
        except Exception:
            pass
    return "\n".join(parts)


# ── Task 1: Session Card deletion ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_detail_panel_has_no_session_card_section():
    """The deleted _mount_session_card block must not render in detail view."""
    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=[],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        # Assert: no widget with class `det-session-card` exists
        cards = detail.query(".det-session-card")
        assert len(cards) == 0, (
            "session_card block should be deleted from detail view"
        )


# ── Task 3: where_left_off merged into last_exchange ─────────────────────


@pytest.mark.asyncio
async def test_where_left_off_merged_into_last_exchange():
    """After merge: no standalone 'WHERE YOU LEFT OFF', but LAST EXCHANGE still exists."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=["Last AI reply content"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        titles = [c.title for c in collapsibles]

        # WHERE YOU LEFT OFF should be gone
        assert not any("WHERE YOU LEFT OFF" in t.upper() for t in titles), (
            f"where_left_off should be merged. Titles: {titles}"
        )
        # LAST EXCHANGE should still exist
        assert any("LAST EXCHANGE" in t.upper() for t in titles), (
            f"last_exchange should remain. Titles: {titles}"
        )


@pytest.mark.asyncio
async def test_last_exchange_carries_next_and_insight_rows():
    """After merge, Next (breakpoint.last_topic) and Insight (key_insights[-1])
    are rendered inside LAST EXCHANGE as additional rows after the bubbles."""
    from textual.widgets import Collapsible, Static

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),  # has last_topic="debugging OAuth refresh"
            last_replies=["Last AI reply content"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        # Find the LAST EXCHANGE collapsible and gather all Static text within
        last_exchange = None
        for c in detail.query(Collapsible):
            if "LAST EXCHANGE" in c.title.upper():
                last_exchange = c
                break
        assert last_exchange is not None, "LAST EXCHANGE collapsible missing"

        text = _collect_static_text(last_exchange)

        assert "Next" in text, f"'Next' row missing in LAST EXCHANGE: {text[:300]}"
        assert "debugging OAuth refresh" in text, (
            f"breakpoint.last_topic missing: {text[:300]}"
        )
        assert "Insight" in text, (
            f"'Insight' row missing in LAST EXCHANGE: {text[:300]}"
        )
        assert "refresh_token" in text, (
            f"key_insights[-1] missing: {text[:300]}"
        )


# ── Task 4: decorative emoji removal ─────────────────────────────────────


# Decorative emoji that must NOT appear in the detail panel.
# Excluded intentionally:
#   ✓ ▶ ○  — milestone status icons (functional)
#   ←      — HERE pointer on in-progress milestone sub-items (functional)
DECORATIVE_EMOJIS = [
    "🧠", "🧭", "📋", "📝", "🔧", "💬", "📍",  # section titles
    "📊", "⚖", "⏸",                             # digest fields
    "🗣", "🤖", "💡",                            # where_left_off (should be deleted)
    "⚡", "📖",                                   # what_was_done
]


@pytest.mark.asyncio
async def test_no_decorative_emoji_in_detail_panel():
    """All decorative emoji should be stripped from section titles and body."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=["reply"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        all_text = _collect_static_text(detail)
        for c in detail.query(Collapsible):
            all_text += "\n" + c.title

        for emoji in DECORATIVE_EMOJIS:
            assert emoji not in all_text, (
                f"Decorative emoji {emoji!r} should be removed from detail panel. "
                f"Found context: {all_text[:400]}..."
            )


# ── Task 2: Context Summary deletion ──────────────────────────────────────


@pytest.mark.asyncio
async def test_no_context_summary_section():
    """CONTEXT SUMMARY collapsible must be gone (overlaps with digest)."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=[],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        titles = [c.title for c in collapsibles]
        assert not any("CONTEXT SUMMARY" in t.upper() for t in titles), (
            f"Context summary should be deleted. Found titles: {titles}"
        )


# ── Task 5: Collapsible defaults + expand behaviour ──────────────────────


@pytest.mark.asyncio
async def test_all_collapsibles_default_collapsed():
    """Every Collapsible in the simplified detail must start collapsed."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=["last reply"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        assert len(collapsibles) >= 2, (
            "Expected at least WHAT WAS DONE + LAST EXCHANGE collapsibles"
        )
        for c in collapsibles:
            assert c.collapsed is True, (
                f"Collapsible {c.title!r} should default to collapsed"
            )


@pytest.mark.asyncio
async def test_collapsible_expands_programmatically():
    """Collapsible.collapsed can be toggled to expand/collapse sections."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=["last reply"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        target = collapsibles[0]
        assert target.collapsed is True

        # Expand
        target.collapsed = False
        await pilot.pause()
        assert target.collapsed is False

        # Re-collapse
        target.collapsed = True
        await pilot.pause()
        assert target.collapsed is True


@pytest.mark.asyncio
async def test_collapsible_count_is_two():
    """After simplification: exactly 2 collapsibles (WHAT WAS DONE + LAST EXCHANGE)."""
    from textual.widgets import Collapsible

    app = _DetailHarness()
    async with app.run_test() as pilot:
        detail = app.query_one(SessionDetail)
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            last_replies=["reply"],
            detail_data=_fake_detail_data(),
            compact_parsed=None,
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        titles = sorted(c.title for c in collapsibles)
        assert len(collapsibles) == 2, (
            f"Expected 2 collapsibles (WHAT WAS DONE + LAST EXCHANGE), "
            f"got {len(collapsibles)}: {titles}"
        )
        assert any("WHAT WAS DONE" in t.upper() for t in titles), (
            f"Missing WHAT WAS DONE. Titles: {titles}"
        )
        assert any("LAST EXCHANGE" in t.upper() for t in titles), (
            f"Missing LAST EXCHANGE. Titles: {titles}"
        )
