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
  <a href="https://github.com/kesepain-KE/kemo-agent"><img src="https://img.shields.io/badge/version-1.2.2-blue" alt="version"></a>
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

### Requirements

- Python 3.10+
- Node.js (for building the frontend)
- Git

### Clone and deploy

```bash
git clone https://github.com/kesepain-KE/kemo-agent.git
cd kemo-agent
python setup.py
```

The setup script guides you through dependency installation, environment configuration, frontend building, and user creation. To accept all defaults and skip the interactive prompts, run:

```bash
python setup.py --yes
```

After setup, start the web interface:

```bash
python start_web.py
```

Open the default address:

```text
http://127.0.0.1:1357
```

If you prefer the command line, run:

```bash
python cli.py
```

To update the project:

```bash
python update.py
```

> The web interface is the recommended starting point. Users, conversations, memories, knowledge, tasks, and extension capabilities can all be inspected in one place.

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

Current version: `1.2.2`

### 1.2.2 update

This is a stability and maintenance release.

- `run/` is split into domain packages. Old flat import paths are no longer supported.
- Project-root detection, fallback Web ports, and local bridge port tracking are fixed.
- Task plans can be edited, retried, reset, inspected by revision, and safely rolled back.
- Obvious Token, API Key, Bearer credential, and private-key text is redacted before task-plan persistence.
- Each user can set a separate completion sound. It is used only by the Windows desktop Web client.
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
  Kemo Provider Gateway: unified multi-provider model discovery, streaming responses, tool calls, capability declarations, multimodal assets, and token metering, giving kemo-agent a consistent model-service boundary.

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
