# Advanced AI Work Assistant + Utilities

This project now includes an **advanced, policy-driven automation assistant** that you can run locally for legal and explicit work automation.

## What you now get

- `ai_assistant.py`: a modular assistant engine with:
  - Tool registry (`terminal`, `powershell`, `read_file`, `write_file`, `append_file`, `list_dir`, `download`)
  - Policy system for permissions and command restrictions
  - Approval gates for higher-impact commands
  - SQLite memory for run history / auditability
  - Plan execution mode + interactive chat-shell mode
- `assistant_policy.json`: configurable permission/safety policy
- `sample_plan.json`: advanced starter plan
- `random_code_generator.py`: existing random code utility

## Important boundaries

You asked for something very powerful. This implementation is designed to be powerful **and** legal/safe:

- It supports terminal + PowerShell automation.
- It supports downloading files and file-system operations in your workspace.
- It does **not** include illegal bypassing behavior.
- It enforces policy checks before running tools.

If you want production-grade “does everything”, the right path is controlled expansion: add plugins, auth, approvals, and observability—not unrestricted command execution.

## Quick start

### 1) Dry-run a plan (safe preview)

```bash
python ai_assistant.py plan sample_plan.json --policy assistant_policy.json
```

### 2) Execute the plan

```bash
python ai_assistant.py plan sample_plan.json --policy assistant_policy.json --execute
```

### 3) Start interactive chat-shell mode

```bash
python ai_assistant.py chat --policy assistant_policy.json
```

Inside chat mode:

- `/help`
- `/tools`
- `/run terminal {"command":"pwd"}`
- `/history 5`
- `/exit`

### 4) Show execution history

```bash
python ai_assistant.py history --memory-db assistant_memory.db --limit 10
```

## Plan schema (advanced)

```json
{
  "name": "Daily workflow",
  "steps": [
    {
      "name": "Check directory",
      "tool": "terminal",
      "args": {"command": "pwd"}
    },
    {
      "name": "Create report",
      "tool": "write_file",
      "args": {"path": "outputs/report.txt", "content": "Done"}
    }
  ]
}
```

## Policy schema

`assistant_policy.json` controls capabilities:

- `allowed_tools`
- `allowed_download_domains`
- `blocked_command_patterns`
- `require_approval_patterns`

Commands matching `require_approval_patterns` are blocked unless you run with `--approve`.

## Core architecture

1. **AssistantPolicy**: validates tools, commands, and download domains.
2. **WorkAssistant**: dispatches tools via a tool registry.
3. **MemoryStore**: persists plan/chat runs to SQLite.
4. **CLI modes**:
   - `plan`: run JSON plan
   - `chat`: interactive operations shell
   - `history`: audit recent runs

## Roadmap to “ChatGPT + ops copilot in one”

To reach your full vision, add these next:

1. **LLM Brain Layer**
   - Provider adapters (OpenAI, Anthropic, local models)
   - Prompt routing + context compression
2. **Plugin Marketplace**
   - Email, calendar, Slack/Discord, Notion, Jira, browser, CRM
3. **Identity + Access**
   - Role-based permissions
   - User/team scoped policy profiles
4. **Approval Workflows**
   - Multi-step approvals for finance, deployments, admin changes
5. **Schedulers + Long-running Jobs**
   - queued tasks, retries, periodic jobs
6. **Observability + Compliance**
   - structured logs, traces, alerts, immutable audit trails

If you want, next step I can build **Phase 3** in this repo:
- natural-language task parsing
- pluggable LLM adapter interface
- action planner that converts chat requests into plan steps automatically.
