import json
from pathlib import Path

from ai_assistant import AssistantPolicy, PolicyError, WorkAssistant


def test_blocked_command_pattern_raises():
    policy = AssistantPolicy()
    assistant = WorkAssistant(policy=policy, memory_db_path=":memory:")

    result = assistant.execute_step(
        step_name="danger",
        tool="terminal",
        args={"command": "rm -rf /"},
        dry_run=False,
        approved=True,
    )
    assert result.status == "error"
    assert "Blocked by command policy pattern" in result.output


def test_execute_step_dry_run():
    assistant = WorkAssistant(memory_db_path=":memory:")
    result = assistant.execute_step(
        step_name="preview",
        tool="terminal",
        args={"command": "echo hi"},
        dry_run=True,
        approved=False,
    )
    assert result.status == "dry-run"


def test_approval_required_without_flag():
    assistant = WorkAssistant(memory_db_path=":memory:")
    result = assistant.execute_step(
        step_name="install",
        tool="terminal",
        args={"command": "pip install requests"},
        dry_run=False,
        approved=False,
    )
    assert result.status == "approval_required"
    assert result.requires_approval is True


def test_write_and_read_file(tmp_path: Path):
    assistant = WorkAssistant(memory_db_path=str(tmp_path / "memory.db"), workspace_root=str(tmp_path))
    write_result = assistant.execute_step(
        step_name="write",
        tool="write_file",
        args={"path": "out/test.txt", "content": "hello"},
        dry_run=False,
        approved=True,
    )
    assert write_result.status == "ok"

    read_result = assistant.execute_step(
        step_name="read",
        tool="read_file",
        args={"path": "out/test.txt"},
        dry_run=False,
        approved=True,
    )
    assert read_result.output == "hello"


def test_path_escape_blocked(tmp_path: Path):
    assistant = WorkAssistant(memory_db_path=str(tmp_path / "memory.db"), workspace_root=str(tmp_path))
    result = assistant.execute_step(
        step_name="escape",
        tool="read_file",
        args={"path": "../secret.txt"},
        dry_run=False,
        approved=True,
    )
    assert result.status == "error"
    assert "Path escapes workspace root" in result.output


def test_policy_disallows_tool():
    policy = AssistantPolicy(allowed_tools=["read_file"])
    assistant = WorkAssistant(policy=policy, memory_db_path=":memory:")
    try:
        assistant.execute_step(
            step_name="nope",
            tool="terminal",
            args={"command": "pwd"},
            dry_run=False,
            approved=True,
        )
        raise AssertionError("Expected PolicyError")
    except PolicyError:
        pass


def test_sample_plan_shape():
    plan = json.loads(Path("sample_plan.json").read_text(encoding="utf-8"))
    assert "steps" in plan
    assert isinstance(plan["steps"], list)
    assert all("tool" in s for s in plan["steps"])


def test_assistant_policy_file_shape():
    policy = json.loads(Path("assistant_policy.json").read_text(encoding="utf-8"))
    assert "allowed_tools" in policy
    assert "blocked_command_patterns" in policy
