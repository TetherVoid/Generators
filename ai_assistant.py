"""Advanced AI Work Assistant (legal automation starter).

This module provides a practical foundation for a personal work assistant that can:
- Run terminal and PowerShell commands with policy guardrails.
- Read/write/list files and directories.
- Download files from allowed domains.
- Execute JSON plans with approvals and auditable logs.
- Store execution history in SQLite.
- Run a simple interactive command-chat shell for daily operations.

Design goals:
- Legal + explicit automation only.
- Policy-first execution.
- Plugin-like tool registry for extensibility.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_BLOCKED_COMMAND_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bformat\b",
    r"\bmkfs\b",
    r"\bdel\s+/f\b",
    r"\bcurl\b.+\|\s*(bash|sh|pwsh|powershell)",
    r"\bInvoke-Expression\b",
]

DEFAULT_REQUIRE_APPROVAL_PATTERNS = [
    r"\bapt\b",
    r"\byum\b",
    r"\bchoco\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
    r"\bgit\s+push\b",
]


@dataclass
class StepResult:
    step_name: str
    tool: str
    status: str
    output: str
    requires_approval: bool = False


class PolicyError(ValueError):
    """Raised when a requested action violates policy."""


class AssistantPolicy:
    """Runtime policy for tool permissions and command restrictions."""

    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        allowed_download_domains: list[str] | None = None,
        blocked_command_patterns: list[str] | None = None,
        require_approval_patterns: list[str] | None = None,
    ) -> None:
        self.allowed_tools = allowed_tools or [
            "terminal",
            "powershell",
            "read_file",
            "write_file",
            "append_file",
            "list_dir",
            "download",
        ]
        self.allowed_download_domains = allowed_download_domains or [
            "github.com",
            "raw.githubusercontent.com",
            "pypi.org",
            "files.pythonhosted.org",
        ]
        self.blocked_command_patterns = (
            blocked_command_patterns or DEFAULT_BLOCKED_COMMAND_PATTERNS
        )
        self.require_approval_patterns = (
            require_approval_patterns or DEFAULT_REQUIRE_APPROVAL_PATTERNS
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssistantPolicy":
        return cls(
            allowed_tools=data.get("allowed_tools"),
            allowed_download_domains=data.get("allowed_download_domains"),
            blocked_command_patterns=data.get("blocked_command_patterns"),
            require_approval_patterns=data.get("require_approval_patterns"),
        )

    @classmethod
    def from_file(cls, path: str | None) -> "AssistantPolicy":
        if not path:
            return cls()
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    def assert_tool_allowed(self, tool: str) -> None:
        if tool not in self.allowed_tools:
            raise PolicyError(f"Tool '{tool}' is not allowed by policy.")

    def assert_command_allowed(self, command: str) -> None:
        for pattern in self.blocked_command_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise PolicyError(f"Blocked by command policy pattern: {pattern!r}")

    def command_needs_approval(self, command: str) -> bool:
        return any(
            re.search(pattern, command, flags=re.IGNORECASE)
            for pattern in self.require_approval_patterns
        )

    def assert_download_allowed(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower().split(":")[0]
        if parsed.scheme not in {"http", "https"}:
            raise PolicyError("Only http/https downloads are allowed.")
        if domain not in self.allowed_download_domains:
            raise PolicyError(
                f"Domain '{domain}' is not in allowed_download_domains policy."
            )


class MemoryStore:
    """SQLite-backed execution memory for auditing/history."""

    def __init__(self, db_path: str = "assistant_memory.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at REAL NOT NULL,
                  run_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record(self, run_type: str, payload: dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO runs (created_at, run_type, payload_json) VALUES (?, ?, ?)",
                (time.time(), run_type, json.dumps(payload)),
            )
            conn.commit()
        finally:
            conn.close()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, created_at, run_type, payload_json FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "created_at": row[1],
                    "run_type": row[2],
                    "payload": json.loads(row[3]),
                }
            )
        return out


class WorkAssistant:
    """Advanced assistant engine with policy, tools, and memory."""

    def __init__(
        self,
        policy: AssistantPolicy | None = None,
        timeout_seconds: int = 180,
        memory_db_path: str = "assistant_memory.db",
        workspace_root: str = ".",
    ) -> None:
        self.policy = policy or AssistantPolicy()
        self.timeout_seconds = timeout_seconds
        self.memory = MemoryStore(memory_db_path)
        self.workspace_root = Path(workspace_root).resolve()
        self.tools: dict[str, Callable[[dict[str, Any]], str]] = {
            "terminal": self._tool_terminal,
            "powershell": self._tool_powershell,
            "read_file": self._tool_read_file,
            "write_file": self._tool_write_file,
            "append_file": self._tool_append_file,
            "list_dir": self._tool_list_dir,
            "download": self._tool_download,
        }

    def _resolve_workspace_path(self, path: str) -> Path:
        target = (self.workspace_root / path).resolve()
        if not str(target).startswith(str(self.workspace_root)):
            raise PolicyError("Path escapes workspace root.")
        return target

    def _run_subprocess(self, cmd: list[str]) -> str:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return f"[exit {proc.returncode}]\n{combined}".strip()
        return combined.strip()

    def _tool_terminal(self, args: dict[str, Any]) -> str:
        command = args["command"]
        self.policy.assert_command_allowed(command)
        return self._run_subprocess(["bash", "-lc", command])

    def _tool_powershell(self, args: dict[str, Any]) -> str:
        command = args["command"]
        self.policy.assert_command_allowed(command)
        return self._run_subprocess(["powershell", "-NoProfile", "-Command", command])

    def _tool_read_file(self, args: dict[str, Any]) -> str:
        path = self._resolve_workspace_path(args["path"])
        return path.read_text(encoding="utf-8")

    def _tool_write_file(self, args: dict[str, Any]) -> str:
        path = self._resolve_workspace_path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.get("content", ""), encoding="utf-8")
        return f"Wrote {path}"

    def _tool_append_file(self, args: dict[str, Any]) -> str:
        path = self._resolve_workspace_path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(args.get("content", ""))
        return f"Appended {path}"

    def _tool_list_dir(self, args: dict[str, Any]) -> str:
        path = self._resolve_workspace_path(args.get("path", "."))
        entries = sorted(p.name for p in path.iterdir())
        return "\n".join(entries)

    def _tool_download(self, args: dict[str, Any]) -> str:
        url = args["url"]
        output_path = self._resolve_workspace_path(args["output_path"])
        self.policy.assert_download_allowed(url)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(url, headers={"User-Agent": "WorkAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
            payload = response.read()
        output_path.write_bytes(payload)
        return f"Downloaded {len(payload)} bytes -> {output_path}"

    def execute_step(
        self,
        step_name: str,
        tool: str,
        args: dict[str, Any],
        dry_run: bool,
        approved: bool,
    ) -> StepResult:
        self.policy.assert_tool_allowed(tool)

        command_for_review = args.get("command", "") if tool in {"terminal", "powershell"} else ""
        needs_approval = bool(command_for_review) and self.policy.command_needs_approval(
            command_for_review
        )

        if needs_approval and not approved:
            return StepResult(
                step_name=step_name,
                tool=tool,
                status="approval_required",
                output="Step blocked: command requires explicit approval.",
                requires_approval=True,
            )

        if dry_run:
            return StepResult(
                step_name=step_name,
                tool=tool,
                status="dry-run",
                output=f"Would run {tool} with args={json.dumps(args)}",
                requires_approval=needs_approval,
            )

        try:
            runner = self.tools[tool]
            output = runner(args)
            status = "ok"
        except Exception as exc:  # noqa: BLE001 - keep plan resilient
            output = str(exc)
            status = "error"

        return StepResult(
            step_name=step_name,
            tool=tool,
            status=status,
            output=output,
            requires_approval=needs_approval,
        )

    def execute_plan(
        self,
        plan: dict[str, Any],
        dry_run: bool = True,
        approved: bool = False,
    ) -> list[StepResult]:
        steps = plan.get("steps", [])
        results: list[StepResult] = []

        for idx, step in enumerate(steps, start=1):
            result = self.execute_step(
                step_name=step.get("name", f"Step {idx}"),
                tool=step["tool"],
                args=step.get("args", {}),
                dry_run=dry_run,
                approved=approved,
            )
            results.append(result)

        self.memory.record(
            run_type="plan",
            payload={
                "plan_name": plan.get("name", "Unnamed"),
                "dry_run": dry_run,
                "approved": approved,
                "results": [r.__dict__ for r in results],
            },
        )
        return results

    def run_chat(self) -> None:
        print("Advanced Work Assistant chat mode")
        print("Type /help for commands. Type /exit to quit.")

        while True:
            try:
                raw = input("assistant> ").strip()
            except EOFError:
                print()
                break

            if not raw:
                continue
            if raw == "/exit":
                break
            if raw == "/help":
                print("Commands:")
                print("  /tools")
                print("  /history [limit]")
                print("  /run <tool> <json_args>")
                print("  /exit")
                continue
            if raw == "/tools":
                print("Available tools:", ", ".join(sorted(self.tools.keys())))
                continue
            if raw.startswith("/history"):
                parts = raw.split()
                limit = int(parts[1]) if len(parts) > 1 else 10
                for item in self.memory.recent(limit):
                    print(json.dumps(item, indent=2))
                continue
            if raw.startswith("/run "):
                # Format: /run terminal {"command":"pwd"}
                parts = shlex.split(raw)
                if len(parts) < 3:
                    print("Usage: /run <tool> <json_args>")
                    continue
                tool = parts[1]
                args_text = raw.split(tool, 1)[1].strip()
                args_text = args_text[args_text.find("{") :]
                try:
                    args = json.loads(args_text)
                except json.JSONDecodeError as exc:
                    print(f"Invalid JSON args: {exc}")
                    continue

                result = self.execute_step(
                    step_name="chat_step",
                    tool=tool,
                    args=args,
                    dry_run=False,
                    approved=False,
                )
                self.memory.record(
                    run_type="chat",
                    payload={"tool": tool, "args": args, "result": result.__dict__},
                )
                print(f"[{result.status}] {result.output}")
                continue

            print("Unknown command. Use /help")


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def print_results(plan_name: str, results: list[StepResult]) -> None:
    print(f"Plan: {plan_name}\n")
    for i, r in enumerate(results, start=1):
        approval_flag = " [approval-needed]" if r.requires_approval else ""
        print(f"[{i}] {r.step_name} ({r.tool}) -> {r.status}{approval_flag}")
        print(r.output)
        print("-" * 80)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advanced AI Work Assistant")
    parser.add_argument(
        "mode",
        choices=["plan", "chat", "history"],
        help="Run mode: execute a JSON plan, start interactive chat, or show history.",
    )
    parser.add_argument("plan_path", nargs="?", help="Path to plan JSON when mode=plan")
    parser.add_argument("--policy", help="Path to policy JSON")
    parser.add_argument("--execute", action="store_true", help="Execute instead of dry-run")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve commands that require explicit approval",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--memory-db", default="assistant_memory.db")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--limit", type=int, default=10, help="history mode row limit")
    args = parser.parse_args(argv)

    policy = AssistantPolicy.from_file(args.policy)
    assistant = WorkAssistant(
        policy=policy,
        timeout_seconds=args.timeout,
        memory_db_path=args.memory_db,
        workspace_root=args.workspace,
    )

    if args.mode == "chat":
        assistant.run_chat()
        return 0

    if args.mode == "history":
        items = assistant.memory.recent(args.limit)
        print(json.dumps(items, indent=2))
        return 0

    if args.mode == "plan":
        if not args.plan_path:
            print("plan mode requires plan_path", file=sys.stderr)
            return 2
        plan = load_json(args.plan_path)
        results = assistant.execute_plan(
            plan=plan,
            dry_run=not args.execute,
            approved=args.approve,
        )
        print_results(plan.get("name", "Unnamed"), results)
        has_errors = any(r.status == "error" for r in results)
        return 1 if has_errors else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
