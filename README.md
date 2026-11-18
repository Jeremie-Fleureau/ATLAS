# ATLAS

**ATLAS** is a CLI-first local AI agent harness inspired engineered specifically for workstations with limited VRAM, high-capacity quad-channel system RAM, and local OpenAI-compatible inference backends (`llama-server` and `strata`).

Instead of treating LLM requests as uniform cloud calls, ATLAS treats **hardware residency**, **slot concurrency limits**, and **token context budgets** as first-class scheduling primitives—maximizing continuous batching throughput and co-executing GPU and CPU models simultaneously without memory thrashing.

---

## Key Features

* **Hardware Residency Arbiter:** Manages VRAM and System RAM locks so models never thrash mid-task. Supports zero-swap simultaneous co-execution (e.g., Tier A/C on GPU alongside Tier B in system RAM).
* **Dynamic KV-Cache Budgeter & Compaction:** Automatically scales per-agent context limits based on active slot counts and compresses older conversation turns into dense State & Findings Digests when approaching capacity.
* **4 Specialized Execution Modes:**
  1. *Classic Single-Agent:* Sequential execution with full MCP tools and self-check.
  2. *Classic Multi-Agent:* Tier B RAM Orchestrator dispatches tasks to parallel Tier A/C VRAM Workers.
  3. *Draft & Refine:* Fast parallel VRAM Drafters generate bulk output, followed by a smart RAM/GPU Refiner that applies targeted patches.
  4. *Swarm Mode:* Sandboxed peer agents coordinate via a shared message board and private DMs toward an upfront deliverable contract.
* **Dual Reasoning Engines:** Linear **Chain-of-Thought (CoT)** with self-check and concurrency-gated **Tree-of-Thoughts (ToT)** sampling candidate paths scored by a Decision Judge.
* **Textual TUI:** Multi-pane interactive terminal UI featuring live agent telemetry, VRAM/RAM pool status, streaming outputs, collapsible thoughts, and mid-run steering.

---

## Hardware Tiers & Residency Profiles

ATLAS categorizes your local models into four intelligence and residency tiers (`config/profiles.yaml`):

| Tier | Model Profile & Residency | Slot Concurrency | Optimal Role |
| :--- | :--- | :--- | :--- |
| **Tier A** | ~9B Dense (`Full VRAM`) | High (`8+` slots) | Fast parallel Workers, Drafters, Swarm Peers |
| **Tier B** | 35B+ MoE (`Quad-Channel System RAM`) | Sequential (`1` slot) | Orchestrator, Refiner, Decision Judge, Synthesizer |
| **Tier C** | ~27B Dense Q4 (`Full VRAM`) | Limited (`1–4` slots) | Smart GPU Solver / Refiner |
| **Tier D** | Large MoE (`Hybrid VRAM + RAM`) | Sequential (`1` slot) | Deep context tasks (`128k+` tokens) |

## Residency-Aware Scheduler Architecture
```
                         ┌────────────────────────────┐
                         │   ATLAS Task & Mode Plan   │
                         └─────────────┬──────────────┘
                                       ▼
                     ┌───────────────────────────────────┐
                     │    Hardware Residency Arbiter     │
                     │  Tracks: [VRAM_POOL] & [RAM_POOL] │
                     └───┬─────────────┬─────────────┬───┘
                         │             │             │
        ┌────────────────┘             │             └────────────────┐
        ▼                              ▼                              ▼
┌──────────────────┐              ┌─────────────────┐             ┌─────────────────┐
│   VRAM_POOL      │◄─ Co-Runs ──►│   RAM_POOL      │             │ EXCLUSIVE LOCK  │
│ Tier A (8+ slot) │              │ Tier B (1 slot) │             │ Tier D (1 slot) │
│   OR             │              │ 35B+ MoE        │             │ VRAM + RAM      │
│ Tier C (1-4slolt)│              │ High Context    │             │ 128k+ Context   │
└──────────────────┘              └─────────────────┘             └─────────────────┘
```
