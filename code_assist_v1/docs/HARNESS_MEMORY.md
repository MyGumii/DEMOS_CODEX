# Harness And Long-Term Memory

Second Brain Agent uses three separate layers:

- **Wiki/Second Brain**: canonical business knowledge and specs.
- **Long-term Memory**: user/project preferences and recurring work habits.
- **Agent Harness**: request-level planning for context, tools, and validation.

## User-Scoped Storage

Long-term memory is stored per user under:

```text
code_assist_v1/user_data/<user_id>/memory/
  memories.jsonl
  events.jsonl
```

It is separate from:

```text
knowledge/<user_id>/
code_assist_v1/user_data/<user_id>/sessions/
code_assist_v1/user_data/<user_id>/skills/
code_assist_v1/user_data/<user_id>/workspace/
```

## Mem0-Like Flow

The long-term memory layer follows a simplified Mem0-style cycle.

```text
Before answer:
  user query
  → search active user/project memories
  → inject relevant memories as low-priority context

After/around user turn:
  explicit memory intent
  → "기억해", "앞으로", "항상", "remember"
  → save active preference/project memory

  stable preference/project signals
  → "나는 ... 선호해", "이 프로젝트에서는 ...", "우리는 ... 하기로 했다"
  → save active user/project memory without requiring a magic keyword

  transient questions and one-off commands
  → "어떻게", "알려줘", "고쳐줘", "가능해?"
  → do not save unless the user explicitly asks to remember it

Official knowledge:
  specs, meeting decisions, rules
  → should be promoted to Second Brain Wiki candidates, not silently treated as memory
```

Long-term memory is not a source of truth. Wiki/spec/source trace wins when there is a conflict.

The UI toggle controls retrieval for the current answer only. Turning it off
does not delete stored memories.

## User Guidance

Long-term memory is meant for stable behavior that should carry across chats. Users do not need to use a magic phrase every time, but memory works best when the sentence clearly states the scope.

Good candidates:

```text
나는 답변을 한국어로 짧게 받는 걸 선호해.
이 프로젝트에서는 Codex bridge를 기본 실행 방식으로 둔다.
우리는 code_assist_v1 포트를 10011로 쓰기로 했다.
WinError 2가 나면 codex.CMD 전체 경로를 먼저 확인한다.
```

Do not auto-save ordinary questions or one-off commands:

```text
Codex bridge는 어떻게 실행해?
이 파일을 지금만 고쳐줘.
이건 임시로 테스트 중이야.
```

If a user does not want a turn to be remembered, they can say:

```text
이건 기억하지마.
저장하지마.
```

If a previous memory became wrong, the user should override it explicitly:

```text
이전 포트 규칙은 취소하고, 앞으로 code_assist_v1은 10011을 기본 포트로 써.
```

Use Long-term Memory for:

- answer style, language, report format, preferred tools
- project conventions, default commands, common failure lessons
- recurring decisions that help future coding work

Use Second Brain for:

- official business rules
- approved specs
- source-traced domain knowledge
- meeting decisions that require canonical evidence

Use Workspace Project Context for:

- files that should only affect the current project/session
- unapproved drafts
- temporary documents uploaded for the current task

## Harness Plan

Each chat request gets a lightweight plan:

```text
modes:
  coding
  second_brain
  spec_driven
  memory_aware

context_order:
  current_chat
  workspace_files
  pinned_wiki_or_thoughts
  wiki_pages
  compiled_evidence
  graph_relations
  user_project_memory
  skills

validation_gate:
  requirements
  constraints
  acceptance_criteria
  tests_or_smoke_check
  wiki_source_trace
```

The plan is injected as a system context block named:

```text
Agent Harness Runtime Plan
```

## APIs

Memory:

```text
GET  /api/code/memory/list
POST /api/code/memory/search
POST /api/code/memory/create
POST /api/code/memory/update
POST /api/code/memory/delete
```

Harness:

```text
POST /api/code/harness/plan
GET  /api/code/harness/status
```
