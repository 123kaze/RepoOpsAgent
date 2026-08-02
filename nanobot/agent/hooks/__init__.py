"""Concrete agent hook implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nanobot.agent.hook import AgentTurnHookFactory
from nanobot.agent.hooks.file_edit_activity import (
    FileEditActivityHook,
    create_file_edit_activity_hook,
)
from nanobot.agent.hooks.repoops_status import (
    RepoOpsStatusHook,
    create_repoops_status_hook_factory,
)

if TYPE_CHECKING:
    from nanobot.config.schema import Config


def default_agent_hook_factories(config: Config) -> list[AgentTurnHookFactory]:
    """Return the lifecycle extensions enabled by the official runtimes."""
    return [
        create_file_edit_activity_hook,
        create_repoops_status_hook_factory(
            config=config.tools.repoops,
            max_iterations=config.agents.defaults.max_tool_iterations,
        ),
    ]

__all__ = [
    "FileEditActivityHook",
    "RepoOpsStatusHook",
    "create_file_edit_activity_hook",
    "create_repoops_status_hook_factory",
    "default_agent_hook_factories",
]
