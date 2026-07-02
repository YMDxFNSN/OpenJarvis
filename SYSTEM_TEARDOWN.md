# OpenJarvis — System Teardown

> A ground-up breakdown of every major system in this repository, written from
> both the **user's** point of view (what it does, how you drive it) and the
> **developer's** point of view (how it actually executes, and where you'd cut
> in to extend it). The goal of this document is to make the whole machine
> legible enough that any part of it can be confidently **recreated, replaced,
> or expanded**.
>
> Scale at time of writing: ~159K lines of Python across 33 subsystems, a
> 17-crate Rust workspace, and a React 19 + Tauri 2 desktop/web frontend.

---

## 0. The one-paragraph thesis

OpenJarvis is a framework for **local-first personal AI**. Its bet is that
on-device models are now good enough to handle most personal-assistant work, so
the software stack — not the model — is the missing piece. Three ideas run
through everything: (1) **shared primitives** for building on-device agents;
(2) **evaluation that treats energy, FLOPs, latency, and dollars as
first-class metrics** alongside accuracy (the "Intelligence Per Watt" thesis);
and (3) a **learning loop** that improves the system from your own local
interaction traces. It's meant to be to personal AI what PyTorch is to deep
learning: a research platform *and* a production foundation.

---

## 1. The architectural spine

Almost every subsystem hangs off five load-bearing primitives in
`src/openjarvis/core/`. Understand these and the rest of the codebase becomes
predictable.

### 1.1 The registry pattern (`core/registry.py`)
A single generic `RegistryBase[T]` with per-subclass isolated storage. Every
pluggable component type gets its own typed registry: `EngineRegistry`,
`AgentRegistry`, `ToolRegistry`, `MemoryRegistry`, `ChannelRegistry`,
`SkillRegistry`, `ConnectorRegistry`, `RouterPolicyRegistry`,
`BenchmarkRegistry`, `LearningRegistry`, `SpeechRegistry`, `TTSRegistry`,
`MinerRegistry`, and more.

- **Registration** is decorator-driven: `@AgentRegistry.register("native_react")`
  on a class body. Importing the module runs the decorator and populates the
  registry — which is why `agents/__init__.py`, `engine/__init__.py`, etc. are
  lists of guarded `import` statements. Discovery = import.
- **Developer takeaway:** to add *any* new backend (engine, tool, agent,
  channel, scorer…), you write a class implementing the relevant ABC, decorate
  it with `@XRegistry.register("key")`, and add one import line to that
  package's `__init__.py`. Nothing else in the system needs to change.

### 1.2 The event bus (`core/events.py`)
A thread-safe synchronous pub/sub (`EventBus`) plus a rich `EventType` enum.
This is how subsystems stay **decoupled**: the engine emits `INFERENCE_END`,
and telemetry, traces, and the dashboard all react without the engine knowing
they exist. The enum is a de-facto changelog of the project's build phases —
`TOOL_TIMEOUT` / `CAPABILITY_DENIED` / `TAINT_VIOLATION` (agent hardening),
`WORKFLOW_*` / `SKILL_EXECUTE_*` / `SESSION_*`, `A2A_TASK_*`, `OPERATOR_TICK_*`,
`OPTIMIZE_*`, `FEEDBACK_RECEIVED`.

- **Developer takeaway:** to observe or alter behaviour cross-cutting-ly
  (new metric, new audit trail, live UI), subscribe to an event rather than
  editing the emitter.

### 1.3 Canonical types (`core/types.py`)
The lingua franca every subsystem speaks: `Message`/`Role`/`Conversation`,
`ToolCall`/`ToolResult`, `ModelSpec`, `Trace`/`TraceStep`, `RoutingContext`,
and the very detailed `TelemetryRecord` (energy split into CPU/GPU/DRAM joules,
ITL percentiles, prefill/decode phase timing, tokens-per-joule, throughput-
per-watt). `TOKEN_COUNTING_VERSION` is versioned so the public leaderboard can
avoid mixing pre/post methodology-change records.

### 1.4 Configuration (`core/config.py`, ~70KB)
One giant nested dataclass tree, `JarvisConfig`, loaded from TOML
(`configs/openjarvis/config.toml` is the default). Top-level sections:
`engine`, `intelligence`, `learning`, `tools`, `agent`, `server`, `telemetry`,
`analytics`, `traces`, `channel`, `security`, `sandbox`, `memory`, `digest`,
`sessions`, and more. Each has sub-sections (e.g. `engine.ollama`,
`engine.vllm`, … one per backend; `channel.telegram`, `channel.discord`, …).
Config is the single dial-board for the whole system.

### 1.5 The Rust bridge (`_rust_bridge.py`)
The **single** doorway between Python and the compiled `openjarvis_rust`
extension. Modules never `import openjarvis_rust` directly — they import
helpers here. Rust is treated as *mandatory* for modules that have a Rust
implementation (hard `ImportError` if missing), while a few modules (e.g.
`security.ssrf`) consult `RUST_AVAILABLE` to keep a Python fallback reachable.

---

## 2. Two runtime tiers, one request lifecycle

There are **two** ways the system runs, sharing the same primitives:

| Tier | Entry | Use | Wiring |
|---|---|---|---|
| **`Jarvis` SDK** (`sdk.py`) | `from openjarvis import Jarvis` | Lightweight, single-agent, embeddable | Lazy: engine + security + telemetry only when first used |
| **`JarvisSystem`** (`system/core.py`) | Built by `SystemBuilder` | Fully-composed system (server, channels, scheduler, workflows, MCP, operators…) | Eager, explicit composition of every subsystem |

### The end-to-end path of a single query
Tracing `jarvis ask "…"` (or `Jarvis().ask(...)`):

1. **CLI** (`cli/__init__.py`) — Click group; `ask` subcommand parses flags,
   builds config, resolves tools.
2. **Engine discovery** (`engine/_discovery.py`) — probes every registered
   engine *concurrently* (`ThreadPoolExecutor`) via `health()`, sorts the
   config-default engine first, returns the first usable one (`can_serve(model)`
   guards against picking a cloud fallback for a model whose client isn't
   installed).
3. **Security wrap** (`security.setup_security`) — wraps the raw engine in a
   `GuardrailsEngine` (secret/PII scanners), attaches a `CapabilityPolicy`
   (RBAC) and an `AuditLogger`.
4. **Telemetry wrap** (`telemetry/instrumented_engine.py`) — wraps *that* in an
   `InstrumentedEngine` that opens an energy-monitor sampling context around
   every `generate()`/`stream()` and emits `TELEMETRY_RECORD`.
5. **Model routing** (`_resolve_model`) — explicit model > config default >
   first available > fallback. (Learned/heuristic routers live in `learning/`.)
6. **Memory context** (optional) — retrieves relevant chunks and injects them as
   messages before generation.
7. **Agent vs. direct** — with `agent=`, dispatches into `AgentRegistry`; the
   agent runs its loop (see §4). Without, calls the engine directly.
8. **Result** — content + usage + telemetry bubble back up; the event bus has
   already fanned the record out to telemetry store, traces, and any dashboard.

Every layer is a transparent wrapper implementing the same `InferenceEngine`
ABC — so security and telemetry compose without the agent or SDK knowing.

---

## 3. The intelligence & engine layer

### Engines (`engine/`) — *the "where does a token come from" layer*
- **User POV:** you point OpenJarvis at whatever's running locally (Ollama by
  default) or a cloud key, and it "just finds it." `jarvis model` /
  `jarvis doctor` show what's live.
- **Developer POV:** `InferenceEngine` ABC (`engine/_stubs.py`) with
  `generate` / `stream` / `stream_full` / `list_models` / `health` /
  `can_serve`. Backends: `ollama` (default, richest), `cloud` (63KB — OpenAI,
  Anthropic, Google, etc.), `litellm`, `gemma_cpp`, `apple_fm_shim`,
  `nexa_shim`, plus an `_openai_compat` base that most OpenAI-compatible
  servers (vLLM, SGLang, LM Studio, exo, uzu, lemonade…) derive from with three
  class attributes. `multi.py` fans across several. A neat trick in discovery:
  a Pearl **mining sidecar** can dynamically synthesize and register a vLLM
  engine class at runtime pointed at the miner's endpoint.
- **Expansion seam:** new backend = subclass the OpenAI-compat base (or the
  ABC), register it, add a host entry to `_HOST_MAP`.

### Intelligence (`intelligence/model_catalog.py`, 33KB)
A curated catalog of `ModelSpec`s (param counts, context length, quantization,
VRAM needs, provider, whether an API key is required). This is the knowledge
the router and `jarvis model` reason over.

---

## 4. The agent layer (`agents/`)

- **User POV:** eight built-in agents across three execution modes —
  on-demand (`simple`, `orchestrator`, `native_react`, `native_openhands`,
  `deep_research`), scheduled (`morning_digest`), continuous
  (`operative`, `monitor_operative`). Pick one with `--agent` or a preset.
- **Developer POV:** `BaseAgent` ABC (`agents/_stubs.py`) gives every agent
  event emission, message assembly (with persona injection), a `_generate`
  helper, `<think>`-tag stripping, and length-continuation handling.
  `ToolUsingAgent` adds a `ToolExecutor`, `max_turns`, and a **loop guard**
  (detects repeated/looping tool calls and compresses context).
  `native_react.py` is the canonical, readable example: a Thought → Action →
  Observation loop that parses structured model output, executes one tool per
  turn through the executor, and feeds the observation back.
- **Notable members:** `orchestrator` (auto tool selection), `claude_code` /
  `opencode` / `openhands` (adapters that shell out to external coding agents
  via Node runners), the `hybrid/` package (local+cloud paradigms — Minions,
  Conductor, Archon, Advisors, SkillOrchestra, ToolOrchestra), `rlm` (a
  REPL-driven "reasoning language model"), and `proactive_agent`.
- **Expansion seam:** subclass `BaseAgent`/`ToolUsingAgent`, register it, drop
  an import in `agents/__init__.py`. System prompts and few-shot exemplars are
  externally overridable via `prompt_loader` + `$OPENJARVIS_HOME`.

---

## 5. The capability layer — how agents gain powers

### Tools (`tools/`) — ~40 registered tools
- **User POV:** enable with `--tools web_search,shell_exec,…`; some (file
  write, shell) require confirmation.
- **Developer POV:** `BaseTool` ABC (`spec` + `execute`) → OpenAI
  function-calling JSON. The star is **`ToolExecutor`** (`tools/_stubs.py`),
  which is a full security gate on every call: JSON arg parsing → boundary
  guard on external tools → **RBAC capability check** → **taint/sink policy**
  → confirmation prompt → timeout-bounded execution in a worker thread →
  auto-taint detection on results → `TOOL_CALL_START/END` events. Tools span
  compute (`calculator`, `code_interpreter[_docker]`, `repl`, `think`), I/O
  (`file_read/write`, `shell_exec`, `http_request`, `apply_patch`, `git_tool`),
  knowledge (`web_search`, `retrieval`, `knowledge_search/sql`, `pdf_tool`),
  media (`image_tool`, `audio_tool`, `text_to_speech`), and meta
  (`skill_manage`, `mcp_adapter`, `agent_tools`, `channel_tools`).

### Skills (`skills/`) — the agentskills.io layer
- **User POV:** `jarvis skill install hermes:arxiv`, `jarvis skill sync`.
  Import ~150 skills from Hermes, ~13,700 from OpenClaw, or any GitHub repo.
  A skill teaches an agent *how* to use tools; **every skill is itself a tool**.
- **Developer POV:** `SkillManager` discovers `skill.toml`/`SKILL.md`
  manifests, wraps each as a `SkillTool` (a `BaseTool`), and exposes an
  `<available_skills>` XML catalog to the model. Skills are **pipelines of tool
  steps** (with sub-skill delegation), executed by `SkillExecutor`. Two feats
  worth noting: skills can be **auto-discovered from your traces**
  (`discover_from_traces` mines recurring tool sequences into new manifests)
  and **optimized** (overlays inject improved descriptions + few-shot examples).
- **Expansion seam:** the `sources/` + `importer.py` machinery is where new
  skill registries plug in.

### Memory & retrieval (`memory/` + `tools/storage/`)
- **User POV:** `jarvis memory index <path>` builds a searchable store;
  conversations grow long-term memory automatically.
- **Developer POV:** two complementary systems. (1) **Retrieval** in
  `tools/storage/`: pluggable backends — `sqlite`, `faiss_backend` (dense),
  `bm25`, `colbert_backend`, `dense`, plus a `hybrid` fusion and a
  `knowledge_graph`; `ingest.py`/`chunking.py` handle document ingestion.
  (2) **Automatic memory** in `memory/`: `MemoryService` runs a `FactExtractor`
  on a **background thread**, subscribing to `CHAT_EXCHANGE_COMPLETED` so fact
  extraction never blocks the chat/serve hot path; extracted facts persist to a
  `FactStore`.

### Connectors (`connectors/`) — ~30 data sources
Read-side integrations that feed personal data in: Gmail/IMAP, Google
Calendar/Contacts/Drive/Tasks, Outlook, Slack, Notion, Obsidian, Dropbox,
iMessage, Apple Health/Contacts/Notes/Music, Oura, Strava, Spotify, Weather,
News RSS, Hacker News, GitHub notifications, Granola, WhatsApp. A shared
`pipeline.py` + `sync_engine.py` + `embedding_store.py` + `hybrid_search.py`
turn any connector's data into retrievable, embedded context. OAuth is
centralized (`oauth.py`, `google_auth.py`) — one Google consent covers several
services (`jarvis connect gdrive`).

### Channels (`channels/`) — ~33 messaging surfaces
Write/interactive side: Telegram, Discord, Slack, Teams, Matrix, Signal,
WhatsApp (Baileys Node bridge), iMessage, Email, IRC, Reddit, Mastodon, Nostr,
Twitter/Twitch, Line, Viber, Messenger, Zulip, Rocket.Chat, Feishu, Mattermost,
Google Chat, Twilio SMS, webchat/webhook. `BaseChannel` ABC has a **carefully
documented `send` contract** (destination id vs. reply reference) hardened by
real bugs. `JarvisSystem.wire_channel` binds a channel to the agent runtime
with **per-conversation session isolation** (`"<channel>:<conversation_id>"`).

### Interop protocols (`mcp/`, `a2a/`)
- **MCP** (`mcp/`): full Model Context Protocol client + server with four
  transports (`stdio`, `SSE`, `StreamableHTTP`, in-process). Lets OpenJarvis
  consume external MCP tool servers *and* expose itself as one.
- **A2A** (`a2a/`): Google's Agent-to-Agent protocol — `AgentCard`, tasks,
  client/server, and an `A2AAgentTool` so a remote agent shows up as a local
  tool. This is how OpenJarvis agents talk to other agents.

---

## 6. The research & measurement layer — the distinctive part

### Telemetry & energy (`telemetry/`)
The crown jewel and the reason "Intelligence Per Watt" is more than a slogan.
`InstrumentedEngine` transparently wraps every engine and, per call, records a
full `TelemetryRecord`: latency, TTFT, throughput, **energy in joules split
across CPU/GPU/DRAM**, power, GPU util/mem/temp, **prefill vs. decode phase
energy**, inter-token-latency percentiles (p90/p95/p99), tokens-per-joule, and
throughput-per-watt. Energy comes from **multi-vendor monitors** —
`energy_nvidia`, `energy_amd`, `energy_apple`, `energy_rapl` (Intel/CPU) —
selected by `energy_monitor.py`. Supporting modules: `flops.py` (FLOP
accounting), `itl.py`, `phase_metrics.py`, `steady_state.py`, `aggregator.py`,
`store.py` (SQLite). `jarvis telemetry` surfaces it.

### Traces (`traces/`)
`TraceCollector` subscribes to the bus and assembles per-query `Trace` objects
(route → retrieve → generate → tool_call → respond steps). `TraceStore`
(SQLite) persists them; `analyzer.py` mines them. **Traces are the fuel for the
learning loop and for skill auto-discovery.**

### Evaluations (`evals/`, ~37K LOC — the single largest subsystem)
- **User POV:** `openjarvis-eval` / `jarvis eval` run standardized benchmarks
  and report accuracy *and* cost/energy/latency.
- **Developer POV:** `evals/core/` is a harness (runner, agentic_runner,
  backend, scorer, environment, pricing, tracker, export). ~43 **datasets**
  (GAIA, SWE-bench, SWEfficiency, GPQA, SuperGPQA, MMLU-Pro, HLE, SimpleQA,
  FRAMES, Math500, LiveCodeBench, TerminalBench, τ-bench, ToolOrchestra,
  and OpenJarvis-native ones like daily_digest, morning_brief, email_triage,
  browser_assistant, security_scanner) each paired with a **scorer**
  (exact-match, MCQ, LLM-judge, or a real harness like `swebench_harness`).
  `trackers/` push to W&B / Google Sheets. `comparison/` benchmarks OpenJarvis
  against other frameworks.
- **Expansion seam:** a new benchmark = a dataset module + a scorer module,
  both registered.

### Learning (`learning/`, ~14K LOC) — the trace→learn→eval loop
`LearningOrchestrator` runs one cycle: mine traces → update routing → evolve
agent configs → optionally LoRA-fine-tune → **gate acceptance on an eval score**
(only keep changes that measurably improve). Sub-areas:
- `routing/` — heuristic + **learned** router, query `complexity` scoring,
  reward models (choose the cheapest model that will still succeed).
- `optimize/` — configuration optimization: `llm_optimizer`, a `search_space`,
  `trial_runner`, a `store`, plus personal/feedback tuning.
- `agents/` — `dspy_optimizer`, `gepa_optimizer`, `ace_optimizer` (prompt/skill
  optimization), `skill_discovery`, `skill_optimizer`, `agent_evolver`.
- `spec_search/`, `training/` (SFT/GRPO/LoRA), `intelligence/`.
- **User POV:** `jarvis optimize skills`, `jarvis bench skills`.

### Mining — "Pearl" (`mining/`)
Distributed/federated inference-mining providers (`vllm_pearl`, `cpu_pearl`,
`apple_mps_pearl`) run as Docker/subprocess **sidecars**, contributing to
pools and feeding the public **savings leaderboard**. `jarvis mine` / `jarvis
pearl` drive it. Packages aren't on PyPI yet — `_install.py` builds them from a
pin on first `mine init`.

### Analytics (`analytics/`) & Bench (`bench/`)
`analytics/` is opt-in **product** telemetry (PostHog) with `redaction.py` +
`identity.py` — distinct from the scientific `telemetry/`. `bench/` is
micro-benchmarking of `energy` / `latency` / `throughput` with a `_stats` helper.

---

## 7. The platform & operations layer

- **Server (`server/`)** — `jarvis serve`: a FastAPI app exposing an
  OpenAI-compatible API plus custom routes (agents, connectors, research,
  approvals, uploads, webhooks, analytics, **savings/leaderboard**,
  cost_calculator), WebSocket/stream bridges for token streaming, a
  `channel_bridge`, a `cloud_router`, auth middleware, and a web dashboard.
- **Security (`security/`)** — the most defense-in-depth subsystem: secret &
  PII scanners → `GuardrailsEngine` (redaction) → `CapabilityPolicy` (RBAC per
  agent/tool) → `AuditLogger` → `BoundaryGuard` (scans outbound external tool
  args) → **taint tracking** (`taint.py`, source→sink policy) → `injection_
  scanner` (prompt-injection, Rust-backed) → `ssrf` checks → `rate_limiter` →
  `subprocess_sandbox` → `signing` (ed25519) → `file_policy`. All toggled from
  `config.security`.
- **Sandbox (`sandbox/`)** — WASM (`wasm_runner`, wasmtime) and Docker
  (`runner`) execution with `mount_security` for untrusted code/tools.
- **Scheduler (`scheduler/`)** — cron-like (`croniter`) task scheduler + store,
  driving scheduled agents (e.g. the morning digest). `jarvis scheduler`.
- **Workflow (`workflow/`)** — a declarative **DAG engine** (`graph`, `builder`,
  `loader`, `engine`) for multi-step pipelines with `WORKFLOW_*` events.
  `jarvis workflow` / `jarvis compose`.
- **Speech (`speech/`)** — STT (`faster_whisper`, `openai_whisper`, `deepgram`)
  and TTS (`cartesia`, `kokoro`, `openai`) behind a registry + discovery.
- **Sessions (`sessions/`)** — persistent conversation state with
  **compression/consolidation** when history grows past a threshold.
- **Daemon (`daemon/`)** — the background service behind `jarvis start/stop/
  status`, a `gateway`, and `session_expiry` cleanup.
- **Operators (`operators/`)** — configurable long-running "operator" personas
  (data-driven definitions + loader + manager) with `OPERATOR_TICK_*` events.
- **System (`system/`)** — `SystemBuilder` (25KB) composes everything into a
  `JarvisSystem`; `QueryOrchestrator` runs queries through it; `bundles.py`
  groups related handles (SecurityContext, Observability, AgentRuntime,
  Scheduling) as ergonomic property views.

---

## 8. Native, frontend, and delivery

### Rust workspace (`rust/`) — 17 crates, MSRV 1.88
`openjarvis-core / engine / agents / tools / learning / telemetry / traces /
security / mcp / sessions / workflow / skills / recipes / templates / a2a /
scheduler`, all surfaced to Python through **`openjarvis-python`** (pyo3
extension → the `openjarvis_rust` module the bridge imports). The crates mirror
the Python package layout, so hot paths (security scanning, retrieval,
optimization store, engine result parsing) can be moved to Rust module-by-module
behind the `_rust_bridge` seam without changing Python call sites.

### Frontend (`frontend/`) & Desktop (`desktop/`)
React 19 + Vite 6 + TypeScript + Tailwind 4 + shadcn/base-ui, `zustand` state,
`react-router`, `recharts` (telemetry/savings dashboards), `katex` + `rehype/
remark` (math + markdown chat rendering), PWA. Packaged as a **Tauri 2** desktop
app (`frontend/src-tauri` + `desktop/src-tauri`) with autostart, global
shortcut, notifications, and auto-updater plugins → `.exe`/`.dmg`/`.deb`/
`.rpm`/`.AppImage`.

### Build, CI, deploy
- **Packaging:** `hatchling` + `hatch-vcs` (version from git tags), with
  `maturin` for the Rust extension. `force-include` bundles the Node runners
  (Claude Code runner, WhatsApp Baileys bridge) and install/deploy scripts into
  the wheel.
- **CI (`.github/workflows/`):** `ci`, `bash-tests`, `frontend`, `desktop`,
  `docs`, `pypi-publish`, `installer-integration`, `autotag`, `track-clones`,
  plus Claude issue/review bots.
- **Tests:** ~624 files under `tests/`, gated by pytest markers
  (`nvidia`, `amd`, `apple`, `cloud`, `docker`, `live`, `slow`, `hub`,
  `modal`, …) so the default lane stays hermetic.
- **Deploy (`deploy/`):** `docker`, `systemd`, `launchd`, `windows`, `posthog`.
- **Docs:** MkDocs Material (`mkdocs.yml`), auto-generated API reference.

---

## 9. How the pieces connect (mental model)

```
                     ┌────────────── event bus (decoupling) ──────────────┐
                     │                                                     │
  CLI / SDK / Server ─▶ SystemBuilder ─▶ JarvisSystem ─▶ QueryOrchestrator │
        │                                     │                            │
        ▼                                     ▼                            ▼
   engine discovery                     agent loop  ◀── tools ◀── skills   telemetry
        │                                     │         │          │        traces
        ▼                                     │      ToolExecutor  │        learning
  raw InferenceEngine                         │      (RBAC/taint/  │           ▲
        │  wrapped by                         │       timeout)     │           │
        ▼                                     ▼                    ▼           │
  GuardrailsEngine ─▶ InstrumentedEngine   memory/retrieval    connectors      │
     (security)          (energy/metrics) ─────────────────────────────────────┘
                                              channels ◀─▶ sessions
                                              MCP / A2A (interop)
```

Everything pluggable is a **registry** entry implementing an **ABC**; everything
observable is an **event**; everything configurable is a **`JarvisConfig`**
field; every hot path has a **Rust seam**.

---

## 10. Where you'd expand it (highest-leverage seams)

Because the architecture is uniform, "make it more capable" has concrete,
low-risk entry points rather than a rewrite:

1. **New engine backend** — subclass `_OpenAICompatibleEngine`, register, add a
   `_HOST_MAP` entry. Unlocks any new local runtime instantly.
2. **New agent paradigm** — subclass `ToolUsingAgent`, register; reuse loop
   guard, executor, persona injection for free.
3. **New tool or skill** — a `BaseTool` subclass, or a `skill.toml` pipeline;
   both inherit the full security gate automatically.
4. **New channel/connector** — implement the ABC, register; `wire_channel`
   handles sessions and routing.
5. **New benchmark** — dataset + scorer modules; instantly comparable on the
   energy/cost axes the whole project is built around.
6. **New learning policy or router** — register in `LearningRegistry` /
   `RouterPolicyRegistry`; the trace→learn→eval loop picks it up.
7. **Move a hot path to Rust** — implement in the mirror crate, expose via
   `openjarvis-python`, route Python through `_rust_bridge`. No call-site churn.
8. **Cross-cutting behaviour** (new metric, audit, live UI) — subscribe to an
   event instead of editing emitters.

The invariants to preserve when extending: **register, don't hardcode;
emit/subscribe, don't couple; configure, don't branch; wrap the engine, don't
fork it.**
