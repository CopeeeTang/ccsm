"""Tests for the canonical resolve_title() priority chain.

Priority (highest -> lowest):
  1. meta.name         - user-set or AI-locked title
  2. meta.ai_intent    - AI-extracted intent (if meta.name empty)
  3. session.display_title - internal fallback chain
  4. session_id[:8]    - last resort
"""
from __future__ import annotations

import pytest

from ccsm.models.session import SessionInfo, SessionMeta, resolve_title


def _mk(sid: str = "abc12345-test", display_name: str = "slug-name-here") -> SessionInfo:
    """Create a minimal SessionInfo."""
    return SessionInfo(
        session_id=sid,
        project_dir="test",
        jsonl_path="/tmp/x.jsonl",
        display_name=display_name,
    )


def test_meta_name_wins():
    """meta.name always wins (user-set or AI-locked title)."""
    s = _mk()
    m = SessionMeta(session_id=s.session_id, name="User Set Title")
    assert resolve_title(s, m) == "User Set Title"


def test_meta_ai_intent_fallback_when_no_name():
    """meta.ai_intent used when meta.name is None/empty."""
    s = _mk()
    m = SessionMeta(session_id=s.session_id, name=None, ai_intent="修复登录Bug")
    assert resolve_title(s, m) == "修复登录Bug"


def test_meta_ai_intent_empty_string_skipped():
    """Empty ai_intent should not be used."""
    s = _mk()
    m = SessionMeta(session_id=s.session_id, name=None, ai_intent="")
    assert resolve_title(s, m) == "slug-name-here"  # falls to display_title


def test_meta_ai_intent_too_long_skipped():
    """ai_intent longer than 80 chars should not be used as title."""
    s = _mk()
    long_intent = "x" * 81
    m = SessionMeta(session_id=s.session_id, name=None, ai_intent=long_intent)
    assert resolve_title(s, m) == "slug-name-here"  # falls to display_title


def test_display_title_fallback_when_no_meta():
    """Without meta, falls back to session.display_title."""
    s = _mk(display_name="Valid Display")
    assert resolve_title(s, None) == "Valid Display"


def test_display_title_fallback_when_meta_empty():
    """Meta exists but name and ai_intent are both None."""
    s = _mk(display_name="Valid Display")
    m = SessionMeta(session_id=s.session_id, name=None, ai_intent=None)
    assert resolve_title(s, m) == "Valid Display"


def test_session_id_prefix_last_resort():
    """When display_name is None and no meta, falls to session_id[:8]."""
    s = SessionInfo(
        session_id="abcdef12-xxx",
        project_dir="test",
        jsonl_path="/tmp/x.jsonl",
        display_name=None,
    )
    assert resolve_title(s, None) == "abcdef12"


def test_meta_name_empty_string_treated_as_none():
    """Empty string name should not win; falls to ai_intent or display_title."""
    s = _mk(display_name="FallbackTitle")
    m = SessionMeta(session_id=s.session_id, name="", ai_intent="SomeIntent")
    assert resolve_title(s, m) == "SomeIntent"


def test_meta_name_whitespace_only_treated_as_empty():
    """Whitespace-only name should not win."""
    s = _mk(display_name="FallbackTitle")
    m = SessionMeta(session_id=s.session_id, name="   ", ai_intent=None)
    assert resolve_title(s, m) == "FallbackTitle"
