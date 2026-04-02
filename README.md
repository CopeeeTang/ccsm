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
- **Full-text Search** — Press `/` for fuzzy search across title, intent, content, branch, tags (powered by in-memory SessionIndex, no count limits)
- **Lineage Detection** — Automatic fork/compact/duplicate relationship detection across sessions
- **Session Graph** — Press `g` to toggle DAG visualization showing session relationships (fork=◆, compact=◇, duplicate=◉)
- **Timestamp Fix** — Sessions sort by last actual message time, not file modification time (prevents "peeking" from changing order)
- **Duplicate Detection** — Identifies sessions from concurrent SSH terminals (same cwd+branch, <5min gap)
- **Title Lock** — `lock_title()` stores titles in CCSM sidecar, immune to Claude Code's 64KB tail window crashes
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
│   ├── discovery.py        # Scan ~/.claude/projects/ for sessions & worktrees + detect_duplicates()
│   ├── parser.py           # JSONL parser + XML sanitization + parse_session_timestamps()
│   ├── lineage.py          # NEW: Fork/compact/dup detection, DAG construction
│   ├── index.py            # NEW: Persistent full-text fuzzy search index (SessionIndex)
│   ├── status.py           # Auto-classify sessions: ACTIVE/BACK/IDEA/DONE/NOISE
│   ├── milestones.py       # Rule-based milestone extraction (6 signal types)
│   ├── summarizer.py       # Dual-mode summarizer (extract / LLM via haiku)
│   └── meta.py             # Sidecar metadata read/write (~/.ccsm/) + lock_title(), lineage serialization
├── models/
│   └── session.py          # Dataclasses: SessionInfo, SessionMeta, Milestone, Breakpoint + LineageType, SessionLineage
├── tui/
│   ├── app.py              # Textual App entry point
│   ├── screens/main.py     # MainScreen — panel layout, keybindings, async workers
│   ├── widgets/
│   │   ├── worktree_tree.py    # Left panel: project/worktree tree
│   │   ├── session_list.py     # Middle panel: tab bar + session cards
│   │   ├── session_card.py     # 2-line compact card with right-aligned metadata
│   │   ├── session_detail.py   # Right panel: SESSION/MILESTONES/BREAKPOINT/LAST REPLY
│   │   └── session_graph.py    # NEW: DAG visualization widget (lineage graph)
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
| `/` | Full-text fuzzy search (backed by SessionIndex) |
| `g` | Toggle session lineage graph view |
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

## Lineage Detection

CCSM automatically detects relationships between sessions and constructs a DAG (directed acyclic graph). Press `g` to visualize it. Three relationship types are detected:

- **Fork** — Detected by `(branch)` suffix in display name or a compact summary appearing as the first message. Displayed as ◆ in the graph.
- **Compact** — Detected by `compact_boundary` system entries in the JSONL session file. Displayed as ◇ in the graph.
- **Duplicate** — Same `(cwd, git_branch)` with <5min time gap between sessions, typically caused by concurrent SSH terminals. Displayed as ◉ in the graph.

Lineage metadata is serialized to the CCSM sidecar (`~/.ccsm/meta/`) and reloaded on subsequent launches.

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

## Version History

### v1.0 — Lineage & Search (2026-04-02)
- Lineage detection (fork/compact/duplicate) with DAG visualization
- Full-text fuzzy search index (SessionIndex)
- Timestamp semantics fix (last_message_at replaces file mtime)
- Title lock mechanism (sidecar-based, crash-immune)
- Duplicate session detection for multi-SSH scenarios
- 33 new tests, all passing

### v0.1 — Initial Release
- Three-panel TUI with status tabs and milestone timeline
- AI-powered summarization and title generation
- MCP server integration

## Documentation

| Document | Path | Content |
|----------|------|---------|
| Product Review Guide | `docs/ccsm-product-review-guide.md` | PM/designer experience guide, UI mockups, user journeys |
| Design Spec | `docs/superpowers/specs/2026-04-01-ccsm-design.md` | 8 pain points, architecture, data model, classification |
| Architecture Overview | `docs/shared/research/ccsm-architecture-overview.md` | Full pipeline diagram, data flow timing, module dependencies |
| v1 Implementation Summary | `docs/shared/research/ccsm-v1-implementation-summary.md` | Pain point → fix mapping, new modules, test coverage |
| Lineage Detection | `docs/shared/research/ccsm-lineage-detection.md` | Fork/compact/duplicate detection, DAG algorithm |
| Search Index | `docs/shared/research/ccsm-search-index.md` | SessionIndex design, scoring, timestamp fix |
| Sidecar Metadata | `docs/shared/research/ccsm-sidecar-metadata.md` | Title lock, lineage serialization, atomic writes |
| TUI Pipeline | `docs/shared/research/ccsm-tui-pipeline.md` | MainScreen integration, async orchestration, data flow |
| v1 Plan | `docs/superpowers/plans/2026-04-02-ccsm-resume-painpoints.md` | 9-task TDD plan (completed) |
| v2 Plan Tasks 1-3 | `docs/superpowers/plans/2026-04-02-ccsm-workflow-swimlane-v2.md` | Workflow/WorkflowCluster models, chain extraction, cache I/O |
| v2 Plan Tasks 4-7 | `docs/superpowers/plans/2026-04-02-ccsm-v2-tasks-4-7.md` | AI clustering, WorkflowList widget, Swimlane timeline, MainScreen integration |

## License

MIT
