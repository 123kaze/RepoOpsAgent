from __future__ import annotations

from nanobot.agent.hook import AgentHookContext, AgentTurnHookContext
from nanobot.agent.hooks.repoops_status import RepoOpsStatusHookFactory
from nanobot.agent.tools.repoops import RepoOpsToolConfig
from nanobot.providers.base import ToolCallRequest
from nanobot.repoops.models import Evidence, GitHubDraft, RepoTaskType
from nanobot.repoops.state import DraftStore, RepoTaskStore


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=name, arguments=arguments)


async def _complete_calls(
    hook,
    *,
    iteration: int,
    calls: list[ToolCallRequest],
    results: list[str],
    statuses: list[str] | None = None,
) -> None:
    context = AgentHookContext(iteration=iteration, messages=[], tool_calls=calls)
    await hook.before_execute_tools(context)
    context.tool_results = results
    context.tool_events = [
        {"name": call.name, "status": status, "detail": "test"}
        for call, status in zip(calls, statuses or ["ok"] * len(calls))
    ]
    await hook.after_iteration(context)


def _hook(tmp_path, *, session_key: str = "session-1", budget: int = 6):
    config = RepoOpsToolConfig(
        allowed_repositories=["acme/widget"],
        status_bar_tool_budget=budget,
    )
    factory = RepoOpsStatusHookFactory(config=config, max_iterations=20)
    hook = factory(
        AgentTurnHookContext(
            workspace=tmp_path,
            session_key=session_key,
        )
    )
    assert hook is not None
    return hook


async def test_status_is_ephemeral_and_reports_task_budget_repeat_and_approval(tmp_path) -> None:
    task_store = RepoTaskStore(tmp_path)
    state = task_store.get_or_create("acme/widget", RepoTaskType.ISSUE_ANALYSIS, 7)
    state.confirmed_facts = ["timeout is reproducible"]
    state.evidence = [
        Evidence(
            evidence_id="E1",
            claim="timeout is reproducible",
            source="src/client.py",
            locator="L10-L20",
        )
    ]
    state.next_actions = ["验证 timeout 异常路径"]
    state.completed_actions = ["读取 Issue"]
    state.requires_human_approval = True
    task_store.save(state)
    DraftStore(tmp_path).save(
        GitHubDraft(
            draft_id="0123456789ab",
            operation="post_comment",
            repository="acme/widget",
            body="diagnosis",
            target_number=7,
            created_session_key="session-1",
            created_turn_id="turn-1",
        )
    )

    hook = _hook(tmp_path)
    state_call = _call(
        "state",
        "repoops_get_task_state",
        {"repository": "acme/widget", "task_type": "issue_analysis", "number": 7},
    )
    search_arguments = {"repository": "acme/widget", "query": "timeout"}
    calls = [
        state_call,
        _call("search-1", "repoops_search_workspace", search_arguments),
        _call("search-2", "repoops_search_workspace", search_arguments),
        _call("search-3", "repoops_search_workspace", search_arguments),
    ]
    await _complete_calls(
        hook,
        iteration=0,
        calls=calls,
        results=[state.model_dump_json(), "same evidence", "same evidence", "same evidence"],
    )

    persisted = [{"role": "user", "content": "diagnose"}]
    model_messages = list(persisted)
    context = AgentHookContext(
        iteration=1,
        messages=persisted,
        model_messages=model_messages,
    )
    await hook.before_iteration(context)

    assert persisted == [{"role": "user", "content": "diagnose"}]
    assert len(model_messages) == 2
    assert model_messages[-1]["role"] == "user"
    assert model_messages[-1]["_meta"]["context_meta"] == {
        "isMeta": True,
        "kind": "agent_status",
        "persistence": "model_only",
    }
    status = str(model_messages[-1]["content"])
    assert "iteration: 2/20" in status
    assert "tool_budget: 4/6 remaining=2" in status
    assert "repoops_search_workspace@" in status
    assert "=3" in status
    assert "evidence: persisted=1 observed_unique=1" in status
    assert "todo: completed=1/2 open=1" in status
    assert 'current_action: "验证 timeout 异常路径"' in status
    assert "approval_state: pending:1" in status
    assert "change_query_or_stop_identical_retry" in status
    assert "finalize_with_current_evidence" in status
    assert "wait_for_exact_later_turn_approval_before_write" in status


async def test_status_detects_consecutive_errors_and_no_progress(tmp_path) -> None:
    hook = _hook(tmp_path, budget=10)
    calls = [
        _call(f"digest-{index}", "repoops_daily_digest", {"repository": "acme/widget"})
        for index in range(3)
    ]
    await _complete_calls(
        hook,
        iteration=0,
        calls=calls,
        results=["Error: unavailable"] * 3,
        statuses=["error", "error", "error"],
    )

    context = AgentHookContext(iteration=1, messages=[], model_messages=[])
    await hook.before_iteration(context)
    status = str(context.model_messages[-1]["content"])

    assert "errors: total=3 consecutive=3" in status
    assert "delta_last_3_calls=0" in status
    assert "change_strategy_or_finalize_no_progress" in status


async def test_status_factory_honors_disable_and_per_turn_budget(tmp_path) -> None:
    disabled = RepoOpsStatusHookFactory(
        config=RepoOpsToolConfig(status_bar_enabled=False),
        max_iterations=20,
    )
    assert disabled(AgentTurnHookContext(workspace=tmp_path)) is None

    enabled = RepoOpsStatusHookFactory(
        config=RepoOpsToolConfig(status_bar_tool_budget=10),
        max_iterations=20,
    )
    hook = enabled(
        AgentTurnHookContext(
            workspace=tmp_path,
            attributes={"repoops_status_tool_budget": 8},
        )
    )
    assert hook is not None
    context = AgentHookContext(iteration=0, messages=[], model_messages=[])
    await hook.before_iteration(context)
    assert "tool_budget: 0/8 remaining=8" in str(context.model_messages[-1]["content"])


async def test_status_fingerprint_redacts_sensitive_argument_values(tmp_path) -> None:
    hook = _hook(tmp_path)
    calls = [
        _call(
            "secret-1",
            "repoops_search_workspace",
            {"repository": "acme/widget", "query": "timeout", "token": "first-secret"},
        ),
        _call(
            "secret-2",
            "repoops_search_workspace",
            {"repository": "acme/widget", "query": "timeout", "token": "second-secret"},
        ),
    ]
    await _complete_calls(
        hook,
        iteration=0,
        calls=calls,
        results=["one", "two"],
    )

    context = AgentHookContext(iteration=1, messages=[], model_messages=[])
    await hook.before_iteration(context)
    status = str(context.model_messages[-1]["content"])

    assert "first-secret" not in status
    assert "second-secret" not in status
    assert "repoops_search_workspace@" in status
    assert "=2" in status
