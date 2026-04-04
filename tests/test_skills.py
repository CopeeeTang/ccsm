"""Skill structure validation tests.

Verifies that all expected skill directories exist under skills/
and that each SKILL.md has valid YAML frontmatter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

EXPECTED_SKILLS = ["ccsm-search", "ccsm-resume", "ccsm-overview", "ccsm-setup"]


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter from a SKILL.md file using simple string splitting.

    Returns a dict with keys like 'name' and 'description'.
    """
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1].strip()
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def test_skills_directory_exists():
    """skills/ directory must exist."""
    assert SKILLS_DIR.is_dir(), f"skills directory not found at {SKILLS_DIR}"


def test_all_expected_skills_exist():
    """All 4 expected skills must have a SKILL.md file."""
    for skill_name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_md.is_file(), f"Missing SKILL.md for {skill_name}: {skill_md}"


def test_skill_frontmatter_valid():
    """Each SKILL.md must have YAML frontmatter with name == directory name and a description."""
    for skill_name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)

        assert "name" in fm, f"{skill_name}/SKILL.md missing 'name' in frontmatter"
        assert "description" in fm, f"{skill_name}/SKILL.md missing 'description' in frontmatter"
        assert fm["name"] == skill_name, (
            f"{skill_name}/SKILL.md: name '{fm['name']}' != directory name '{skill_name}'"
        )


def test_skill_has_content():
    """Each SKILL.md body (after frontmatter) must be > 100 chars."""
    for skill_name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{skill_name}/SKILL.md has no frontmatter delimiters"
        body = parts[2].strip()
        assert len(body) > 100, (
            f"{skill_name}/SKILL.md body too short ({len(body)} chars, need > 100)"
        )
