# Structured planning (Checkpoint 05)

The application now supports two explicit planning modes. Offline mode remains the default: it creates the existing deterministic CSV pipeline and makes no provider calls. Optional OpenAI mode runs planning in the worker and executes only registered deterministic CSV agents after the proposed graph passes validation. It does not add research, code execution, reporting, or LLM-based specialist agents.

## Optional operator configuration

Keep the following variables server-side in the untracked `.env` file. Compose passes them to the API and worker. Never place a provider key in a `VITE_*` variable, a prompt, frontend code, or Git.

```dotenv
LLM_PLANNER_ENABLED=false
OPENAI_API_KEY=
LLM_MODEL=
LLM_INPUT_PRICE_PER_MILLION=
LLM_OUTPUT_PRICE_PER_MILLION=
LLM_MAX_OUTPUT_TOKENS=2048
```

To enable paid planning, supply an API key, a Chat Completions model that supports JSON mode and `max_completion_tokens`, and its current USD input/output token prices per million tokens; then set `LLM_PLANNER_ENABLED=true` and recreate the API and worker. No model or price is guessed by the application. Obtain the correct values from the official [model documentation](https://developers.openai.com/api/docs/models) and [pricing](https://developers.openai.com/api/docs/pricing). Price snapshots are operator-maintained estimates, not a provider-enforced invoice cap. Model access and compatibility depend on the provider account.

The provider adapter sends requests only to `https://api.openai.com/v1/chat/completions`, verifies TLS, disables redirects and environment proxy inheritance, disables provider storage, requests the default service tier, and does not implement automatic HTTP retries. No remote URL can be supplied by a workflow.

## User choice and data sent

The dashboard starts in **Offline pipeline** mode. When configured, a workspace owner/member can explicitly select **OpenAI planner**, choose a token budget, estimated-cost ceiling and deadline, and click **Plan and execute**. Viewers cannot submit either mode.

Only task instructions, CSV column names and row count, registered agent metadata, and the proposal schema are sent to OpenAI. CSV row values stay local. Instructions and column names are untrusted data and cannot grant new tools or permissions. Instructions may themselves contain information entered by the user; do not place credentials or confidential content in instructions you intend to send to a provider.

The browser never receives the key. `GET /api/v1/planner/config` reports availability, model name and policy limits, not credentials. Disabling the provider leaves offline mode operational. A requested LLM run fails explicitly if unavailable; it does not silently fall back to an offline plan.

## API and planning state

`POST /api/v1/workflows` accepts the existing workspace/prompt/CSV fields plus:

```json
{
  "planner_mode": "llm",
  "planning_max_tokens": 20000,
  "planning_max_cost_usd": 0.05,
  "planning_deadline_seconds": 60
}
```

`planner_mode` defaults to `offline`. Server caps are 64,000 planning tokens, USD 1 estimated planning cost, and a 180-second planning deadline per workflow. Output per provider request defaults to 2,048 tokens and can be configured between 256 and 4,096. These are planning limits; specialist CSV agents use no model tokens. There is no global daily/monthly account quota or production rate limiting yet.

LLM submission returns 202 before any provider call, with no executable tasks yet. The worker transitions the workflow to `planning`, profiles the CSV locally, checks the configured snapshot and limits, and commits a reservation before sending a request. The planning deadline includes time spent waiting in the queue.

Workflow details and `GET /api/v1/workflows/{id}/plan` expose the private planning status, attempt ledger, accounted tokens/cost, and accepted plan. Both require workspace membership. Raw provider responses, invalid plans and provider exception messages are not copied into events or API errors. Input-validation errors do not echo passwords or raw request values.

## Validation and bounded repair

The API uses JSON mode and application-side strict validation. JSON mode alone does not guarantee schema adherence; see the official [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat) and [Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

The proposal must name protocol 1.0, a DAG of at most 25 registered/version-compatible tasks, explicit parent bindings, and a final verification task. Every task must contribute to that verified result. Unknown capabilities, tools/permission fields, missing/duplicate dependencies, cycles, incompatible versions, unverified outputs, and unrelated hidden tasks are rejected. Provider array order is not execution order: accepted tasks are inserted in dependency order and the final output is selected by the validated final task ID.

A malformed JSON, invalid graph or truncated response may trigger one fresh proposal request. At most two provider calls are allowed, including that repair. Every call must independently fit the remaining token/cost/deadline limits. Provider authentication, rate-limit, timeout, refusal and unverifiable-usage errors fail without automatic retries. Failed planning inserts no executable tasks and never publishes a partial proposal.

## Accounting, interruption and cancellation

Before a request, the worker reserves a conservative text-byte token estimate plus protocol headroom and the maximum allowed output. This is a heuristic, not a measured tokenizer count. The adapter rejects usage exceeding its reservation bounds. Cost calculations use Decimal arithmetic and the stored operator price snapshot, rounded up. Known usage replaces the reservation with a token-based estimate; missing/uncertain usage retains the full reservation. Provider billing may differ if pricing, model accounting or assumptions change.

A reservation is committed before any network request. If the worker restarts after a possibly billable request, the workflow fails with `planning_interrupted`; that request is not replayed. A crash before any reservation can safely requeue. The task/result envelopes still retain only the latest specialist-agent attempt; planning has its own persisted two-attempt ledger.

Cancellation prevents subsequent provider calls and prevents a returned plan from creating tasks. A request already in flight may still be charged; reported usage is recorded, or an uncertain reservation is retained. Cancellation is not a refund or a provider-side request-abort guarantee.

HTTP timeouts are capped at 30 seconds per phase, with additional wall-deadline checks before dispatch, between response chunks, and before plan publication. These checks do not forcibly preempt all blocking I/O. Concurrent worker leases, global spend controls, provider failover and stronger execution supervision remain later work.

## Migrations and evidence

Revision `0004` adds nullable planning metadata and accepted-plan columns. Existing task rows/results are preserved; no provider is enabled by migration. Apply `alembic upgrade head` before starting updated services (Compose's migration service handles this).

`backend/tests/fixtures/planning_golden.json` contains 20 replay cases covering valid plans, renamed/out-of-order nodes, and adversarial/invalid outputs. Tests use fake providers and HTTP mock transports. They demonstrate validation and control flow, not live-model planning quality. No paid provider calls were made during Checkpoint 05 development. A live smoke test remains an explicit opt-in after configuring a model and budget.
