"""Left panel: Worktree tree widget.

Displays projects as top-level nodes with worktrees as children.
Each worktree shows session count; ● marks active worktrees.
"""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Tree

from ccsm.models.session import Project, Worktree


class WorktreeTree(Tree[Worktree | Project]):
    """Tree widget for project → worktree hierarchy."""

    class WorktreeSelected(Message):
        """Emitted when a worktree is selected."""

        def __init__(self, worktree: Worktree, project: Project) -> None:
            self.worktree = worktree
            self.project = project
            super().__init__()

    class ProjectSelected(Message):
        """Emitted when a project node is selected (show all sessions)."""

        def __init__(self, project: Project) -> None:
            self.project = project
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__("PROJECTS", id="worktree-tree", **kwargs)
        self._projects: list[Project] = []

    def load_projects(self, projects: list[Project]) -> None:
        """Populate the tree with project and worktree data."""
        self._projects = projects
        self.clear()

        for project in sorted(projects, key=lambda p: p.name):
            total = project.total_count
            has_active = any(
                wt.has_active
                for wt in ([project.main_worktree] if project.main_worktree else [])
                + project.worktrees
            )
            active_marker = "● " if has_active else "  "
            label = f"{active_marker}{project.name} ({total})"
            project_node = self.root.add(label, data=project, expand=True)

            # Add main worktree
            if project.main_worktree:
                wt = project.main_worktree
                wt_marker = "● " if wt.has_active else "  "
                wt_label = f"{wt_marker}main ({wt.total_count})"
                project_node.add_leaf(wt_label, data=wt)

            # Add named worktrees
            for wt in sorted(project.worktrees, key=lambda w: w.name):
                wt_marker = "● " if wt.has_active else "  "
                wt_label = f"{wt_marker}{wt.name} ({wt.total_count})"
                project_node.add_leaf(wt_label, data=wt)

        self.root.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection — emit custom messages."""
        node = event.node
        data = node.data

        if isinstance(data, Worktree):
            # Find parent project
            parent = node.parent
            project = parent.data if parent and isinstance(parent.data, Project) else None
            if project:
                self.post_message(self.WorktreeSelected(data, project))
        elif isinstance(data, Project):
            self.post_message(self.ProjectSelected(data))
