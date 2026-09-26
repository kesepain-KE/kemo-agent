# kemo-agent

<p align="center">
  <img src="kemo-agent.jpg" alt="kemo-agent logo" width="200">
</p>

<p align="center">
  <a href="readme.md">简体中文</a> · <strong>English</strong>
</p>

<p align="center">
  <strong>A local multi-user Agent Runtime for the next generation of personal intelligence infrastructure.</strong>
</p>

<p align="center">
  Built around the Kemo Tidal Engram lifecycle memory system, kemo-agent orchestrates context, subagents, tools, environmental perception, external extensions, and cross-platform interaction,<br>
  enabling agents to develop long-term cognition, evolve continuously, schedule complex work, and connect with the real world.
</p>

<p align="center">
  <a href="https://github.com/kesepain-KE/kemo-agent"><img src="https://img.shields.io/badge/version-1.3.1-blue" alt="version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
  <a href="https://kesepain-ke.github.io/kemo-agent-doc/"><img src="https://img.shields.io/badge/docs-online-5966d9?logo=readthedocs&logoColor=white" alt="online documentation"></a>
</p>

---

## What if every conversation did not have to start over?

Many AI assistants exist only inside the current window.

Close the window, and the relationship resets. Your preferences, ongoing work, previous decisions, and the details you repeatedly emphasized may all disappear by the next time you meet.

kemo-agent is trying to build a different kind of agent.

It does not treat every exchange as an isolated question and answer. Instead, it connects the time you spend together into a continuous line. A goal discussed today can continue tomorrow. A plan left weeks ago can resurface when it becomes relevant. Information that truly matters can gradually become part of the context through which the agent understands you.

It is more than a window that answers questions. It is closer to a personal intelligence workspace that you control.

---

## Memory moves like the tide

Human memory is not a warehouse filled with untouched records.

Some words matter only in the moment and quietly fade as the tide recedes. Some things are mentioned again and again, leaving clearer traces with every rise and fall. Other people and decisions remain worth remembering even after a long time.

That is the experience **Kemo Tidal Engram** is designed to create.

kemo-agent does not try to remember everything mechanically. Over long-term use, it attempts to distinguish what relates to you, what still matters, and what should return at the right moment.

You always retain the ability to inspect, supplement, and correct its memories. Memory is not a hidden black-box judgment; it is something you and the agent maintain together.

> The tide carries brief echoes away, but leaves what truly matters on the shore.

---

## What can it help you do?

| Scenario | What kemo-agent provides |
|---|---|
| Everyday conversation | Continue with your communication style, preferences, and long-term interests without repeatedly explaining the background |
| Memory consolidation | Let important information remain, reinforce what is repeatedly mentioned, and allow outdated details to fade quietly |
| Complex tasks | Turn an ambiguous goal into a clear plan and keep track of its steps and results |
| Long-running projects | Preserve key decisions, unfinished work, and phase changes so work can resume at any time |
| Scheduled assistance | Execute tasks, organize information, or send reminders at an agreed time, even while you are offline |
| Knowledge collaboration | Work with personal or team materials so responses stay grounded in the real working environment |
| Deeper reasoning | Spend more effort on difficult problems while responding quickly to simple ones |
| Subagent collaboration | Delegate memory organization, planning, scheduling, and other specialized judgments to focused subagents |
| External access | Stay connected to the same agent through the web, command line, or messaging platforms |
| File exchange | Receive user materials and organize generated results and temporary files |
| Environmental awareness | Combine authorized information sources so the agent can understand its current environment |
| Extensible capabilities | Add tools, skills, perception sources, and external integrations as needed |

These capabilities are not isolated feature entries. They serve one shared goal: helping the agent understand what is happening and keep work moving forward.

---

## A complete personal intelligence workspace

The kemo-agent web interface is organized around real workflows rather than a single input box.

From one place, you can:

- hold streaming conversations and add text, image, audio, video, or file guidance while a run is in progress;
- search, switch, save, and manage conversation history;
- inspect and edit memories to maintain long-term cognition;
- manage personal, shared, and global knowledge;
- create, approve, pause, and resume task plans;
- schedule one-time or recurring tasks;
- inspect tools, skills, perception sources, and extension capabilities;
- manage uploaded files, generated outputs, and temporary content;
- check external messaging connections and current runtime status;
- keep separate data and workspaces for different users.

Even very long conversations do not require loading the entire history at once. The web interface displays recent content first and loads older messages when you scroll upward, keeping long-running conversations lightweight.

<p align="center">
  <img src="kemo-web-UI.png" alt="kemo-agent web interface" width="720">
</p>

---

## The same agent through more than one interface

You can have an extended conversation in the browser, handle local work quickly from the command line, or send a message from a connected messaging platform.

The interface may change, but the user identity, conversation history, memories, and authorized resources still belong to the same person. kemo-agent aims to reduce the fragmentation of having to “meet again” whenever you switch platforms, allowing the agent to become a persistent personal interface.

Scheduled tasks are part of that continuity as well. While you are offline, the agent can wake at an agreed time, complete the work entrusted to it, and leave behind a result you can review later.

---

## Complex work can be completed gradually

When a goal requires multiple steps, kemo-agent can confirm a plan with you before taking action.

You can see where the task is, what has been completed, and what remains. You can also pause midway, reconsider the direction, and decide whether to continue.

The emphasis is not on uncontrolled “full automation,” but on a collaboration process that remains understandable, interruptible, and resumable.

Some work should be completed immediately, some needs several rounds of interaction, and some belongs at a future time. kemo-agent is designed to let all three rhythms coexist naturally in one workspace.

---

## Your data should remain under your control

kemo-agent is local-first.

Conversations, memories, knowledge, tasks, and user files are managed in your workspace, where they can be inspected, backed up, and migrated. Different users remain clearly separated, and each user controls which resources may be used.

The project does not claim that every model service is inherently private. What is sent to an external service depends on the provider, configuration, and authorization scope you choose. kemo-agent's role is to return as much choice and visibility to the user as possible.

---

## Get started

> 📖 For complete installation, configuration, usage, and extension-development guidance, visit the **[kemo-agent online documentation](https://kesepain-ke.github.io/kemo-agent-doc/)**. The documentation site is currently available in Chinese.

### Choose an installation method

Windows, Linux, npm, and Docker share the framework version. The tentative next release is **1.3.1**; the compatible gateway baseline remains **0.8.2**.

> **Publication prerequisite:** The remote commands below work only after the installer scripts and the corresponding distribution artifacts are published. Native installation needs `kemo-agent-release-<version>.zip` and its `.zip.sha256` in a Release; npm needs a published distribution package; Docker needs a published image tag. Pushing code alone does not publish these artifacts. This guide does not claim they are already available online. The Release ZIP is not GitHub's automatically generated source archive.

| Method | Host requirements |
|---|---|
| Windows / Linux one-command deployment | Python 3.10+; working `venv` / `ensurepip` on Linux; network access to install application dependencies |
| npm | Node.js 18+, npm, Python 3.10+, and a working Python virtual environment |
| Docker | Docker Engine and Docker Compose; no host Python / Node.js required |
| Source development | Python 3.10+, Git, Node.js and npm for frontend builds |

The Release includes a prebuilt frontend, so native Windows / Linux clients do not need Git or Node.js. Remote installers execute code; download and review them first if preferred.

### Windows: install, start, and update

Install in PowerShell:

```powershell
irm https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/windows/install.ps1 | iex
```

The installer installs files and dependencies but does not start a persistent service. The default directory is `%USERPROFILE%\.kemo-agent`. For everyday use:

```powershell
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
# Check only; this does not install updates
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" check
# Stop the running application before updating and restarting
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" update --yes
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
```

If only the `py` launcher is available, replace `python` with `py -3`. First startup initializes configuration and the user. The installer does not install system Python, change PATH, or create a system service.

### Linux: install, start, and update

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/linux/install.sh | sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
```

For routine checks and updates:

```sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" check
# Stop the running application first
python3 "$HOME/.kemo-agent/deploy/deploy.py" update --yes
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
```

The default directory is `~/.kemo-agent`. macOS can reuse the Unix entry point, but on-device validation has not been completed.

### npm: installation and everyday use

```sh
npm install -g @kesepain/kemo-agent
kemo
```

There is no `postinstall`; the first `kemo` invocation deploys and starts the application. To upgrade, stop the application first, then run:

```sh
npm install -g @kesepain/kemo-agent@latest
kemo
```

`kemo check` / `kemo update` compare against the framework bundled in the locally installed npm package, not the latest GitHub Release. The default root is `~/.kemo-agent` (the user home directory on Windows). Use `KEMO_INSTALL_ROOT` for a separate directory; npm cannot take over an existing native-channel installation.

### Docker: one-command startup and updates

Run in a dedicated Compose project directory that you will keep using; do not overwrite an existing `docker-compose.yml`:

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/docker/docker-compose.yml -o docker-compose.yml && docker compose up -d
```

The default image is `ghcr.io/kesepain-ke/kemo-agent:latest`; the publisher must supply that tag. Alternatively, set `KEMO_VERSION` to a published framework version. For everyday use, stay in the same directory:

```sh
docker compose logs -f
# Update: stop, pull, and start the new image
docker compose stop
docker compose pull
docker compose up -d
```

A named volume mounted at `/data` stores persistent data. New images synchronize managed application files on startup while preserving user data. **Do not update using `docker compose down -v`.** Keep the Compose project name and directory unchanged to avoid accidentally selecting a new, empty volume. The default binding is local-only at `127.0.0.1:1357`; public access is not configured automatically.

### Source installation (developers)

```bash
git clone https://github.com/kesepain-KE/kemo-agent.git
cd kemo-agent
python setup.py
# Use python setup.py --yes to accept initialization defaults
python start_web.py
```

`setup.py` guides dependency installation, environment configuration, frontend builds, and user creation. Source installations can also use `python cli.py` for the CLI; stop the application before updating with `python update.py`.

**Do not alternate between `update.py` and `deploy/deploy.py` for the same installation.** Deployment refuses nonempty unmanaged directories and installations owned by another channel. Choose custom paths at first installation; do not target your development checkout.

The default web address is `http://127.0.0.1:1357`. Start with the web interface and configure model services, the user, and access credentials.

See the [deployment guide](deploy/README.md) for options, custom paths, offline archives, recovery, and publication, and the [deployment knowledge reference](global_knowledge/deployment-and-release.md) for the agent-facing contract. Both detailed guides are currently in Chinese.

---

## What we want it to become

kemo-agent is not trying to become an omnipotent system that makes every decision for the user.

It aims to become a stable foundation for personal intelligence:

- the longer you work together, the better it understands your habits and boundaries;
- before tackling complex work, it reaches an agreement with you;
- when action is needed, it shows the process and result clearly;
- when work must wait, it remembers to continue in the future;
- capabilities can grow while control remains with the user;
- your data can remain even when you change models or connection methods.

A genuinely long-term intelligent relationship should not depend on one impressive answer. It should emerge from countless instances of reliable, restrained, and continuous collaboration.

---

## Current status

Current version: `1.3.1` (tentative; not yet published)

Confirmed compatible Kemo gateway: `kemo-adapter-api 0.8.2` (Kemo 1.0 wire protocol matched).

### 1.3.1 planned release

- Adds independent Windows, Linux, npm, and Docker deployment entry points sharing the framework version and a standard Release archive.
- Documents one-command installation, routine startup, updates after stopping the application, transaction recovery, and publication while retaining source installation.
- Retains the 1.3.0 capabilities and the `kemo-adapter-api 0.8.2` compatibility baseline. Remote artifacts must be built and published separately.

### 1.3.0 update

This is the formal release that consolidates long-term intelligence, conversation lifecycle, module extensibility, and consistent Web interaction.

- Tidal Engram memories are split by whether a fact can be updated, invalidated, weighted, and retrieved independently. Historical weighting is bound to the original round, Shanghai calendar date, and retrieval reference, producing an auditable chain for creation, reinforcement, revision, daily locking, and idempotent receipts. Oversized promotions are still split transactionally, while skill creation, editing, and upgrades offer to merge related memories and keep the originals by default.
- Conversation continuation, leases, closing, and offline memory transfer are aligned across Web, App, CLI, external messaging, and Cron. Closed-session queue compensation, idle-session sweeping, deletion fences, and a bounded runtime cache prevent false resurrection, permanently hanging sessions, and quadratic archive rewrites.
- Recoverable Provider and subagent failures now converge on a run-level consecutive-failure budget. Network recovery, tool-argument repair, and outer retries retain separate boundaries so nested retry paths cannot grow without limit.
- Task plans, scheduled tasks, and real execution history now have independent containers, six-item pagination, detailed previews, stable ordering, system-task isolation, and richer recurrence rules. The chat start page only shows an active plan owned by the current valid conversation and no longer surfaces orphaned cards from cleaned archives.
- Expand and Sense modules move to the 2.0 component-panel contract with template defaults, user configuration pages, presets, secret fields, action controls, hot discovery, and quick configuration switching. The built-in Kemo App, gateway, and knowledge-graph modules expose their intended controls and online checks.
- Web responses can embed declarative cards, charts, tables, layouts, forms, follow-up suggestions, and site-local media directly in the answer. Unknown component names safely fall back to a generic data card while preserving streaming closure, text fallback, and bounded action semantics.
- Historical archives are sorted newest first and can be filtered through an inline Shanghai-calendar picker. Follow-up/guidance messages, file sorting, task panels, knowledge editing and preview, Expand/Sense panel spacing, and runtime logs received a broader consistency pass.
- Core implementations continue to split god modules above 800 lines around low coupling, high cohesion, and stable public entry points, with corresponding cache, background queue, logging, template, and contract tests.
- `kemo-agent 1.3.0` and `kemo-adapter-api 0.8.2` have completed Kemo 1.0 wire-protocol matching across model capabilities, streaming responses, tool calls, multimodal assets, Usage, Embedding, Rerank, resume behavior, and unified terminal states.

### 1.2.9 update

This release focuses on memory evolution, session lifecycle and write reliability.

- Memory fragments are now split into two granularity classes: profile and trait memories may merge and update within the same dimension, while facts and rules stay as minimal fragments. The criterion is whether a fragment still stands on its own after being separated. Extraction counts independent facts rather than conversation rounds, and the per-round ceiling is raised.
- When a promotion would exceed the granularity ceiling of the target tier, the fragment is split into several children inside a single transaction: children inherit the original expiry, start with zero weight, produce no weighting events, and any invalid child rolls the whole batch back while keeping the source. Fragment merging now only happens between updated versions of the same fact, and a merge never crosses the ceiling, which prevents endless split-and-merge cycles.
- When a user actively creates or edits a skill, related memories are surfaced with a prompt asking whether to merge; memories are kept by default. Background skill creation stays silent.
- CLI exit and Cron task finalisation now close their sessions and register memory extraction. Previously only Web, App and external message routes closed sessions, so CLI and Cron conversations hung forever and never reached memory.
- A new idle session sweep system task closes sessions untouched for more than a day. Running, queued, locked sessions and those holding a Web lease are skipped, and memory extraction is always queued before closing.
- The runtime workspace now lives in a bounded in-process cache; each round commits only to the archive. A missing cache entry is rebuilt on demand from the archive tail, and cross-process writes invalidate stale entries through a version fence. Long conversations no longer rewrite the full body every round, which removes quadratic disk writes.
- Window trimming now detects the shifted case where the old tail becomes the new prefix, deleting only the dropped head and appending the new tail, and falls back to a full table rebuild when the shift cannot be identified safely.
- Fixed fragment misalignment in the Web expand and perception injection preview. The preview used to assemble its own fragment and slice with a cumulative cursor, which was shorter than the real fragment by the framework header lines, so every block after the first rendered the tail of its predecessor. Fragment positions now come from the authoritative side and the preview only looks them up by identifier.
- The runtime log panel moved to a card layout and expands its available width on wide screens. Terminal logs became a summary card plus read-only console with a responsive layout. Terminal log entries now separate standard output from standard error and add loading window and item counts while staying redacted.

### 1.2.8 update

This release focuses on multi-user Web workspaces and runtime reliability.

- Task plans now continue across Run boundaries after a per-round tool limit, while pause, cancel, failure, and completion states remain authoritative.
- Subagent progress is shown beneath the matching `subagent_dispatch` tool card; the follow-up queue supports next-turn guidance, ordering, cancellation, and retry.
- Prompt and definition standards are aligned across Sense, Expand, Plugin, Skill, manuals, personas, and the global knowledge base.
- Files can be sorted by name, newest update, or size; expired temporary important memories remain visible with a short invalidation reason.
- System status exposes all, backend, thread, terminal, and message log views, backed by bounded TTL/LRU/quota read caching to reduce disk pressure.
- Scheduled-task conversation history defaults to seven days and is globally configurable; multi-user Web workspaces support new user tabs and startup conversation-space inspection.
- Stop/pause races across long-task handoffs no longer leave the send controls locked, and the public version and project knowledge documentation are synchronized.

### 1.2.7 update

This release hardens the Chat Completions compatibility transport, improves transfer reliability, and stabilizes CI on Windows runners.

- Streaming tool-call aggregation in the Chat transport is now idempotent: repeated `id`/`name` frames no longer produce duplicated identifiers; malformed `index` values fall back safely instead of aborting the stream; a complete JSON arguments object from a compatible service is adopted as-is; `raw_arguments` is forwarded for faithful multi-round tool loops.
- The Chat transport no longer injects `reasoning_effort`, `reasoning_enabled`, or `stream_options.include_usage` into upstream requests; several OpenAI-compatible services rejected those fields. The Chat provider now declares reasoning as unsupported.
- Bounded pre-output network recovery: at most 2 attempts, only while nothing has been emitted; `Retry-After` is honored up to 10 seconds; 401/403/409/400 fail immediately with zero retries; after any text, reasoning, or tool fragment the stream never replays; an exhausted budget is marked final so the outer runtime cannot multiply retries.
- Explicit tool-unsupported fallback: when a 400 error clearly states that tools are not supported, the transport strips all tool fields and retries exactly once; plain 400 errors never trigger the fallback.
- A streaming request that receives a plain JSON response is parsed via a Content-Type fallback without resending the request.
- Terminal subagent-task pruning no longer uses the random task id as a tie-breaker, keeping cleanup order stable when Windows clocks assign identical timestamps.
- Windows CI stability fixes: path comparisons use `samefile`, file-identity checks no longer depend on `st_dev`/`st_ino`, background-pruning assertions wait for visibility, and the kemo-graph sync test injects failures deterministically.

### 1.2.5 update

This is a release-readiness stability patch focused on deletion, recovery, knowledge boundaries, memory extraction, and Web rendering.

- Deleted conversations now keep a durable delete fence. A late terminal commit from an in-flight Run cannot recreate the session, history windows, or active binding.
- Knowledge-index discovery rejects both symbolic links and Windows junctions, so a link cannot inject files from outside the project tree.
- The `on_commit` round-memory extraction call now uses the real extractor parameter contract, allowing candidates to be produced during normal runs.
- App bridge recovery may query all active Runs for one device with `client_id` alone; a request without either device or conversation scope is still rejected.
- Bare links in Web Markdown now stop at adjacent Chinese punctuation or delimiters instead of absorbing the following prose; long links in chat wrap safely and retain a clear keyboard-focus indicator.
- Regression coverage for the backend boundaries passes the release check, while the Web-link fix passes the complete frontend test suite and production build.

### 1.2.4 update

This is a subagent connectivity and runtime-stability update.

- Web subagent model settings now explain the three profiles in plain language: default for ordinary subagents, cheap for summaries/context compression/temporary-memory work, and reasoning for task plans, self-improvement, and deeper analysis.
- The retry bubble is cleared as soon as the next attempt produces real reasoning, text, or tool progress, and is also cleared by a successful terminal event.
- Subagents can bind to an external kemo-agent, another Agent service, or a local adapter through an authorized Expand module's `agent_bridge.json`; `subagent_dispatch` lists and synchronously calls the binding.
- External bindings reuse Expand process isolation, allowlists, timeout, cancellation, path, and result-size limits. Remote URLs, access tokens, and passwords remain inside the trusted adapter configuration or environment.
- Input and output are validated against the declared JSON Schemas. Background `wait=false` is intentionally unavailable until a shared persistent task-state contract exists.

### 1.2.3 update

This is a stability patch for long-running work, tool calls, and process boundaries.

- Provider tool arguments are parsed and schema-checked before execution; invalid arguments end in an explicit `incomplete` state.
- Parallel tool calls are committed as a batch, so one malformed call cannot leak other calls from the same batch.
- Background Shell jobs enforce their deadline in a detached worker; output is still drained when log writing fails.
- PID identity and start information are checked before cancellation; uncertain identities are rejected, and public results do not expose host absolute paths.
- Cancellation, Provider failures, and bounded retries archive the current text and reasoning idempotently, preventing duplicate content in one round.
- Provider diagnostics recognize more sensitive-field aliases and enforce recursion/node budgets; prefixed JSON errors are sanitized before return.
- Main-agent and subagent output-parse failures use a bounded retry budget of up to five attempts; context-compression memory extraction is reused across those attempts to avoid duplicate writes, and an exhausted `context_manage` repair is not multiplied by the outer loop.
- Persisted Provider response identifiers are sanitized and length-bounded, limiting oversized or sensitive values in history.

### 1.2.2 update

This is a stability and maintenance release.

- `run/` is split into domain packages. Old flat import paths are no longer supported.
- Project-root detection, fallback Web ports, and local bridge port tracking are fixed.
- Task plans can be edited, retried, reset, inspected by revision, and safely rolled back.
- Obvious Token, API Key, Bearer credential, and private-key text is redacted before task-plan persistence.
- Each user can set separate sounds for successful completion and final failure. They are used only by the Windows desktop Web client; mobile clients do not show or play them.
- Submitted attachment references are removed immediately to avoid reusing the same `asset_id`.
- Mid-run guidance uploads use `purpose=input`.
- Package-layout, project-path, fallback-port, and user-template tests were added.

The `kemo_app` bridge version is `1.1.5`. External plugins that still import paths such as `run.agent_runner` or `run.task_plan_store` must move to the new `run.<domain>` entry points.

`1.0.0` marks the first complete release of the kemo-agent core ecosystem, while `1.0.1` performs the first framework-wide stability review. `1.0.2` repairs critical Tidal Engram behavior, `1.0.3` introduces configurable request-level dynamic snapshots, `1.0.4` improves tool-call continuity and multi-entry history, and `1.0.5` adds independent user-level master gates for extension and perception Prompt injection. `1.1.0` completes the Android mobile loop, `1.1.1` isolates App conversations under `source=app`, and `1.1.2` hardens task plans, memory, long waits, the App bridge, and Web interaction. `1.2.0` is the long-task release: a user can explicitly enable long-task mode for one `user + source + session_id` conversation space; when a run reaches its per-run tool-call ceiling, kemo-agent commits that run and continues in a new run under the same session lock while non-terminal `long_task_update` events report the original request, cumulative elapsed time, run and continuation counts, tool calls, Provider requests, and token usage. Disabling the preference lets the current run settle without starting another continuation, while cancellation stops the entire logical task. Conversation spaces and Web/App sources remain isolated, and automatic continuation never bypasses context protection, Provider failures, plan-approval boundaries, or ordinary cancellation. Automatic, manual, and Provider-limit compression now reports progress above the composer; summary readiness and background memory analysis of trimmed rounds remain distinct stages. The preference lives in the existing session record rather than global or user configuration. `1.2.1` is a runtime-reliability patch: history content, rounds, indexes, and session state now use stricter transactional and cross-process write boundaries; system Cron adds a single-leader lease, in-memory runtime checkpoints, and aggregated success logs; the main agent and subagents share batch tool-argument validation and safe recovery; the Web capability-reference drawer now covers extensions, skills, and plugins; and the `kemo_app` 1.1.4 bridge adds detached-run snapshots, lifecycle locking, PID/instance reconciliation, and temporary-backoff self-healing. Future releases will continue to focus on adjacent integrations, performance, and long-term reliability.

Available today:

- a complete web conversation interface with streaming interaction and multimodal mid-run guidance;
- a per-user SQLite history store with transactional commits, table-backed content search, and cursor pagination;
- a per-user SQLite Tidal Engram store that transactionally keeps content, lifecycle state, daily weight evidence, and hot-view sources; archived user evidence now finds existing fragments through confidence-gated keyword coverage before applying at most one weight increase per day, reducing duplicate-fragment growth;
- temporary important memory maintained as a rebuildable hot view, derived one-way from the three temporary tiers without feeding weight back into its source memories; both the Prompt injection budget and the independent runaway-output guard are currently 20,000 characters;
- personal, shared, and global knowledge layers grounded in real source material;
- task plans that move from creation and approval to step-by-step execution, with pause, resume, and audit support; successful creation terminates only the current conversation run rather than letting it bypass the plan state machine or pausing another session;
- an explicitly enabled, conversation-scoped long-task mode that can continue across runs after the per-run tool ceiling while showing the original request, total elapsed time, run count, tool activity, and token usage above the composer;
- visible context-compression progress above the composer, with queued memory analysis continuing after the current run commits and validly producing no new fragments when trimmed rounds contain no durable candidates;
- one-time and recurring scheduled tasks that wake automatically at the agreed time;
- background collection for extension and perception data, with every logical Provider request reloading the latest published snapshots without adding collection latency to model calls;
- subagent collaboration for memory organization, planning, summarization, and scheduling;
- multiple built-in tools and skills with room for further extension, including `wait_for_condition` for bounded two-hour waits that wake early on process, path, or port conditions;
- dynamic reasoning-effort choices derived from gateway model capabilities when using the Kemo protocol, while other Provider protocols retain their existing configuration flow;
- non-strict parameter mode for ordinary plugin tools with open objects or optional fields, while structured-output tools retain strict validation;
- pre-execution tool-argument integrity checks for both Kemo and Chat providers; truncated, content-filtered, or malformed Chat tool calls terminate explicitly as incomplete instead of executing partial arguments;
- separate contract-test baselines for skills, extensions, perception modules, external message routes, subagents, and user templates, covering their basic framework inputs and outputs;
- ZIP upload for user-created skills, with recursive `SKILL.md` discovery and transactional installation;
- shared identity and memory across the web interface, Android App, CLI, and messaging platforms, with conversation histories isolated by their real `source` and non-Web histories available as read-only Web archives;
- isolated workspaces and configuration for multiple users;
- separate management of uploaded files and agent-generated content, with bounded image, audio, and video previews;
- explainable scored memory search across all four lifecycle tiers, with batch queries loading each tier once and returning matched fields, terms, coverage, and scores; plus a ten-item moving conversation navigator that centers the active round at 180° and can continue loading earlier history;
- a layered Web backend organized around routes, domain services, and shared contracts while retaining compatibility entry points;
- runtime status and maintenance interfaces with background tasks operating automatically.

Areas still being refined:

- a more intuitive and less error-prone experience for creating custom extensions;
- stability and resource behavior during long-running operation;
- smoother installation, update, and migration workflows;
- continued refinement for more real-world scenarios.

If you are trying an early release, reports about problems, usability feedback, and the work you genuinely want an agent to take on are all welcome.

---

## The Kemo ecosystem

kemo-agent is not an island. Around it, several independently maintained projects cooperate through stable protocols to form the Kemo ecosystem:

- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api)
  Kemo Provider Gateway: the compatibility baseline is `0.8.2`; the planned 1.3.1 release retains the protocol match confirmed for 1.3.0. It provides unified multi-provider model discovery, streaming responses, tool calls, capability declarations, multimodal assets, and token metering, giving kemo-agent a consistent model-service boundary.

- [kemo-graph](https://github.com/kesepain-KE/kemo-graph)
  A knowledge-graph and RAG retrieval project that can be attached to kemo-agent as an external document station: after registering a document library, you query, sync, and maintain it on demand through `expand_call`, without replacing the framework's built-in knowledge base or memory.

- [kemo-agent-app](https://github.com/kesepain-KE/kemo-agent-app)
  The Android client of the kemo ecosystem: after connecting to a deployed kemo-agent and Kemo gateway, conversations, tasks, files, extension perception, runtime status, and agent configuration can all continue on your phone. It communicates through the `kemo_app` bridge inside kemo-agent (HTTP/SSE/WebSocket) with two-level authentication and transport.

- [kemo-agent-doc](https://github.com/kesepain-KE/kemo-agent-doc)
  The VitePress documentation site for kemo-agent: installation, configuration, usage, and extension development guides, deployed as [online documentation](https://kesepain-ke.github.io/kemo-agent-doc/).

### Other projects

- [votx-agent](https://github.com/kesepain-KE/votx-agent)
  An independently maintained Agent project with no inheritance relationship to kemo-agent.

---

## Maintainer

[@kesepain](https://github.com/kesepain-KE)

---

## Contributing

kemo-agent is still at a very early stage. Bug reports, usability feedback, documentation improvements, and code contributions are all welcome.

Recommended workflow:

1. Fork this repository.
2. Create a feature branch.
3. Make the change and perform the necessary verification.
4. Open a Pull Request explaining what changed and why.

---

## License

This project is licensed under the [Apache License 2.0](LICENSE).
