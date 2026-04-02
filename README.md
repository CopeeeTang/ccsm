# CCSM — Claude Code Session Manager

> TUI + MCP plugin for browsing, searching, and resuming Claude Code sessions.

```
⬡ CCSM — Claude Code Session Manager
 WORKTREES          │ SESSIONS · GUI/panel        │ DETAIL
─────────────────── │────────────────────────────── │──────────────────────────
▶ PROJECTS          │[ 🟢 ACTIVE 3 ] 🔵 BACK 0 ...│── 📋 SESSION ──────────
  ▼ GUI (432)       │                              │  Title   整理GPT5.2数据
    main (329)      │ ● 整理GPT5.2数据并对比分析   │  Status  ● ACTIVE  Duration 2h
    panel (4)       │   📝 "整理GPT5.2在subset..." │  Branch  memory   Last     5h ago
    memory (73)     │ ● CCSM架构设计        23h ago│── 🧭 MILESTONES ────────
    streamIT (3)    │   📝 "重构数据库..."   💬 42  │  ✓ Data Cleanup & Analysis
  ▼ VLM-Router (0)  │                              │  ✓ Multi-Model Comparison
                    │                              │  ▶ Eval Mode Analysis
 ↑↓ Navigate  Tab Panel  1-4 Status  / Search  r Resume  s AI  D Archive  q Quit
```

## Features

- **Three-panel TUI** — Worktree tree / Session list / Detail view
- **Status Tab filtering** — `ACTIVE` | `BACK` | `IDEA` | `DONE`, switch with `1/2/3/4`
- **Milestone timeline** — Rule-based (free, instant) or LLM-powered (haiku, ~12s) phase extraction
- **Breakpoint** — "Where was I?" highlighted in orange, the single most useful info for context restoration
- **AI Title & Intent** — Haiku generates short titles (≤8 Chinese chars) and one-line summaries, cached to sidecar
- **Search** — Press `/` for fuzzy search across title, intent, branch, and session ID
- **Resume** — Press `r` to jump back into a session via `claude --resume`
- **Archive** — Press `D` to mark sessions as DONE
- **MCP Server** — Expose sessions to Claude Code itself for self-aware context management

## Quick Start

```bash
# From the project root
cd /path/to/GUI
source ml_env/bin/activate

# Run the TUI
PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m ccsm

# Or install as a package
pip install -e projects/ccsm
ccsm
```

## Architecture

```
ccsm/
├── core/                   # Backend logic (no UI dependencies)
│   ├── discovery.py        # Scan ~/.claude/projects/ for sessions & worktrees
│   ├── parser.py           # JSONL parser + XML sanitization
│   ├── status.py           # Auto-classify sessions: ACTIVE/BACK/IDEA/DONE/NOISE
│   ├── milestones.py       # Rule-based milestone extraction (6 signal types)
│   ├── summarizer.py       # Dual-mode summarizer (extract / LLM via haiku)
│   └── meta.py             # Sidecar metadata read/write (~/.ccsm/)
├── models/
│   └── session.py          # Dataclasses: SessionInfo, SessionMeta, Milestone, Breakpoint
├── tui/
│   ├── app.py              # Textual App entry point
│   ├── screens/main.py     # MainScreen — panel layout, keybindings, async workers
│   ├── widgets/
│   │   ├── worktree_tree.py    # Left panel: project/worktree tree
│   │   ├── session_list.py     # Middle panel: tab bar + session cards
│   │   ├── session_card.py     # 2-line compact card with right-aligned metadata
│   │   └── session_detail.py   # Right panel: SESSION/MILESTONES/BREAKPOINT/LAST REPLY
│   └── styles/
│       └── claude_native.tcss  # Stone/Orange theme inspired by Claude's design language
├── mcp/
│   └── server.py           # MCP server with 30s TTL cache
└── cli/
    └── main.py             # Click CLI (placeholder for `ccsm list`, `ccsm resume`)
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` `2` `3` `4` | Switch to ACTIVE / BACK / IDEA / DONE tab |
| `/` | Toggle search input (fuzzy match) |
| `r` | Resume selected session (`claude --resume`) |
| `s` | Trigger AI summary (haiku LLM) |
| `D` | Archive selected session (mark as DONE) |
| `h` | Toggle NOISE session visibility |
| `Tab` / `Shift+Tab` | Cycle panel focus |
| `q` | Quit |

## Summarization Modes

### Rule-based (`extract`) — Free, instant
- Scans user messages for 6 discourse marker signals: topic shift, approval, directive, review, summary, slash commands
- Produces milestones in ~0ms, zero API cost
- Quality: functional but labels are raw message fragments

### LLM-powered (`llm`) — ~$0.01, ~12s
- Sends compressed conversation context to `claude-haiku-4.5` via local proxy
- Produces semantic milestones with clear phase labels and actionable breakpoints
- Auto-triggers after 1.5s hover on a session card (silent, no notification)
- Results cached to `~/.ccsm/summaries/` — subsequent loads are instant

### AI Title Generation
- Generates ≤8 Chinese character (or ≤20 English character) titles
- One-line intent summary cached to `~/.ccsm/meta/`
- Triggers lazily on first session selection if no title exists

## Data Storage

CCSM never modifies Claude Code's data. All user metadata lives in `~/.ccsm/`:

```
~/.ccsm/
├── meta/
│   └── {session_id}.meta.json      # Name, tags, status override, AI intent
└── summaries/
    └── {session_id}.summary.json   # Milestones, breakpoint, description
```

## Security

- **Path traversal prevention** — Session IDs validated with `^[a-zA-Z0-9_-]+$` regex
- **Rich markup injection** — All user content wrapped in `rich_escape()` before rendering
- **ReDoS prevention** — XML sanitizer uses bounded quantifiers `{0,200}` instead of nested `*`
- **Format string safety** — User content braces escaped before `.format()` calls

## Requirements

- Python ≥ 3.10
- [Textual](https://textual.textualize.io/) ≥ 1.0.0
- [Rich](https://rich.readthedocs.io/) ≥ 13.0.0
- Claude Code sessions in `~/.claude/projects/`
- (Optional) Local LLM proxy at `http://127.0.0.1:4142` with `claude-haiku-4.5` model

## License

MIT
