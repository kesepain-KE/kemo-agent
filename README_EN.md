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
  <a href="https://github.com/kesepain-KE/kemo-agent"><img src="https://img.shields.io/badge/version-0.10.0-blue" alt="version"></a>
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

Current version: `0.10.0`

The core system now forms a complete working loop. Conversations, memory, subagent collaboration, task planning, scheduling, perception, extensions, the web interface, the CLI, and messaging-platform access are connected and can be used in everyday operation.

Available today:

- a complete web conversation interface with streaming interaction and multimodal mid-run guidance;
- a per-user SQLite history store with transactional commits, table-backed content search, and cursor pagination;
- a per-user SQLite Tidal Engram store that transactionally keeps content, lifecycle state, daily weight evidence, and hot-view sources; weight changes still require traceable matches in the user's archived words before memories advance through tiers or fade over time;
- temporary important memory maintained as a rebuildable hot view, derived one-way from the three temporary tiers without feeding weight back into its source memories;
- personal, shared, and global knowledge layers grounded in real source material;
- task plans that move from creation and approval to step-by-step execution, with pause, resume, and audit support;
- one-time and recurring scheduled tasks that wake automatically at the agreed time;
- subagent collaboration for memory organization, planning, summarization, and scheduling;
- multiple built-in tools and skills with room for further extension;
- dynamic reasoning-effort choices derived from gateway model capabilities when using the Kemo protocol, while other Provider protocols retain their existing configuration flow;
- non-strict parameter mode for ordinary plugin tools with open objects or optional fields, while structured-output tools retain strict validation;
- pre-execution tool-argument integrity checks for both Kemo and Chat providers; truncated, content-filtered, or malformed Chat tool calls terminate explicitly as incomplete instead of executing partial arguments;
- separate contract-test baselines for skills, extensions, perception modules, external message routes, subagents, and user templates, covering their basic framework inputs and outputs;
- ZIP upload for user-created skills, with recursive `SKILL.md` discovery and transactional installation;
- shared identity and memory across the web interface, CLI, and messaging platforms;
- isolated workspaces and configuration for multiple users;
- separate management of uploaded files and agent-generated content, with bounded image, audio, and video previews;
- cross-tier memory search plus a ten-item moving conversation navigator that centers the active round at 180° and can continue loading earlier history;
- a layered Web backend organized around routes, domain services, and shared contracts while retaining compatibility entry points;
- runtime status and maintenance interfaces with background tasks operating automatically.

Areas still being refined:

- a more intuitive and less error-prone experience for creating custom extensions;
- stability and resource behavior during long-running operation;
- smoother installation, update, and migration workflows;
- continued refinement for more real-world scenarios.

If you are trying an early release, reports about problems, usability feedback, and the work you genuinely want an agent to take on are all welcome.

---

## Related projects

- [votx-agent](https://github.com/kesepain-KE/votx-agent)  
  An independently maintained Agent project with no inheritance relationship to kemo-agent.

- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api)  
  A model-service adapter designed to work with kemo-agent.

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
