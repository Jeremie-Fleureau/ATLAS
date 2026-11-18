# atlas/cli/app.py
from __future__ import annotations
import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from atlas.cli.commands import SlashCommandHandler
from atlas.core.context import ContextBudgeter
from atlas.core.events import AtlasEvent, EventBus, EventType
from atlas.core.router import SlotAwareRouter
from atlas.modes.base import BaseAgent


class AtlasApp(App):
    """ATLAS Hermes-style multi-pane TUI."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #status_banner {
        height: 3;
        background: $boost;
        color: $text;
        content-align: left middle;
        padding: 0 2;
        border-bottom: solid $primary;
    }
    #main_workspace {
        height: 1fr;
    }
    #chat_pane {
        width: 60%;
        border-right: solid $primary;
        padding: 1;
    }
    #right_column {
        width: 40%;
    }
    #agent_table {
        height: 50%;
        border-bottom: solid $accent;
    }
    #event_drawer {
        height: 50%;
        padding: 0 1;
    }
    #prompt_input {
        dock: bottom;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear Chat"),
    ]

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.event_bus = EventBus()
        self.router = SlotAwareRouter(config_path, self.event_bus)
        self.budgeter = ContextBudgeter(self.router)
        self.commands = SlashCommandHandler(self)
        self.active_agents: dict[str, BaseAgent] = {}
        self._agent_rows: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status_banner")
        with Horizontal(id="main_workspace"):
            yield RichLog(id="chat_pane", highlight=True, markup=True, wrap=True)
            with Vertical(id="right_column"):
                yield DataTable(id="agent_table")
                yield RichLog(id="event_drawer", highlight=True, markup=True, wrap=True)
        yield Input(
            placeholder="Enter task prompt, /command, or mid-run steering message...",
            id="prompt_input",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.event_bus.subscribe(self.on_atlas_event)
        table = self.query_one("#agent_table", DataTable)
        table.add_columns("Agent", "Role", "Tier", "Reason", "Context", "Status")
        self.update_header_bar()

        chat = self.query_one("#chat_pane", RichLog)
        chat.write(
            "[bold cyan]ATLAS Harness v0.1 initialized.[/] Type [bold]/help[/] or enter a task."
        )
        chat.write(
            "Co-execution enabled: [green]VRAM (Tier A 9B / Tier C 27B)[/] + [yellow]System RAM (Tier B 35B+ MoE)[/] can run simultaneously."
        )

    def update_header_bar(
        self,
        vram_tier: str = "IDLE",
        vram_busy: int = 0,
        ram_tier: str = "IDLE",
        ram_busy: int = 0,
    ) -> None:
        banner = self.query_one("#status_banner", Static)
        banner.update(
            f"[bold]Mode:[/] [cyan]{self.router.current_mode.upper()}[/]  |  "
            f"[bold]Reasoning:[/] [magenta]{self.router.reasoning_mode.upper()}[/]  |  "
            f"[bold green]VRAM Pool:[/] {vram_tier} ({vram_busy} active)  |  "
            f"[bold yellow]RAM Pool:[/] {ram_tier} ({ram_busy} active)"
        )

    def broadcast_steering(self, directive: str) -> int:
        for agent in self.active_agents.values():
            agent.steer(directive)
        return len(self.active_agents)

    async def on_atlas_event(self, event: AtlasEvent) -> None:
        chat = self.query_one("#chat_pane", RichLog)
        drawer = self.query_one("#event_drawer", RichLog)
        table = self.query_one("#agent_table", DataTable)

        if event.event_type == EventType.POOL_STATUS:
            p = event.payload
            self.update_header_bar(
                vram_tier=p["vram_tier"],
                vram_busy=p["vram_busy"],
                ram_tier=p["ram_tier"],
                ram_busy=p["ram_busy"],
            )

        elif event.event_type == EventType.AGENT_STATE:
            p = event.payload
            aid = p["agent_id"]
            ctx_str = f"{p['ctx_used']}/{p['ctx_limit']}"
            row_vals = (aid, p["role"], p["tier"], p["reasoning"], ctx_str, p["status"])
            if aid not in self._agent_rows:
                table.add_row(*row_vals, key=aid)
                self._agent_rows[aid] = aid
            else:
                for col_idx, val in enumerate(row_vals):
                    table.update_cell_at(( list(self._agent_rows.keys()).index(aid), col_idx ), val)

        elif event.event_type == EventType.THOUGHT_CHUNK:
            p = event.payload
            chat.write(f"[dim italic]💭 [{p['agent_id']} thought]: {p['thought']}[/]")

        elif event.event_type == EventType.STREAM_CHUNK:
            p = event.payload
            chat.write(f"[bold green][{p['agent_id']} ({p['role']})]:[/] {p['text']}")

        elif event.event_type == EventType.TOOL_CALL:
            p = event.payload
            drawer.write(
                f"[bold cyan]{event.timestamp} [MCP][/] {p['agent_id']} -> [yellow]{p['tool']}[/]({p['args']})"
            )

        elif event.event_type == EventType.SWARM_MSG:
            p = event.payload
            drawer.write(
                f"[bold magenta]{event.timestamp} [SWARM {p['channel']}][/] {p['sender']}: {p['text']}"
            )

        elif event.event_type == EventType.CHECKPOINT:
            p = event.payload
            drawer.write(
                f"[bold green]{event.timestamp} [CHECKPOINT {p['action']}][/] {p['label']}"
            )

        elif event.event_type == EventType.SYSTEM_LOG:
            p = event.payload
            drawer.write(f"[dim]{event.timestamp} [{p['level']}] {p['message']}[/]")

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        user_text = message.value.strip()
        message.input.value = ""
        if not user_text:
            return

        chat = self.query_one("#chat_pane", RichLog)

        if user_text.startswith("/"):
            reply = await self.commands.handle(user_text)
            chat.write(f"[bold yellow]{reply}[/]")
            return

        # If agents are currently active, treat plain input as mid-run steering
        if self.active_agents:
            count = self.broadcast_steering(user_text)
            chat.write(f"[bold magenta][Steered {count} running agent(s)]:[/] {user_text}")
            return

        chat.write(f"\n[bold white]You:[/] {user_text}")
        asyncio.create_task(self._dispatch_task(user_text))

    async def _dispatch_task(self, prompt: str) -> None:
        """Dispatches task according to the active execution mode."""
        mode = self.router.current_mode

        if mode == "single":
            agent = BaseAgent("single-0", "solver", self.router, self.budgeter, self.event_bus)
            self.active_agents[agent.agent_id] = agent
            try:
                await agent.run("You are ATLAS in Mode 1 (Single Agent).", prompt)
            finally:
                self.active_agents.pop(agent.agent_id, None)

        elif mode == "multi":
            # Tier B Orchestrator in RAM + parallel Tier A Workers in VRAM
            orch = BaseAgent("orch-0", "orchestrator", self.router, self.budgeter, self.event_bus)
            workers = [
                BaseAgent(f"worker-{i}", "worker", self.router, self.budgeter, self.event_bus)
                for i in range(1, 4)
            ]
            for a in [orch, *workers]:
                self.active_agents[a.agent_id] = a
            try:
                plan = await orch.run(
                    "You are the Tier B Orchestrator. Break the user request into 3 self-contained subtask specs.",
                    prompt,
                    max_steps=1,
                )
                await asyncio.gather(
                    *(
                        w.run(
                            f"You are {w.agent_id}. Execute your assigned part of the plan.",
                            f"Global Task: {prompt}\nOrchestrator Plan:\n{plan}",
                            max_steps=2,
                        )
                        for w in workers
                    )
                )
            finally:
                for a in [orch, *workers]:
                    self.active_agents.pop(a.agent_id, None)

        else:
            # Fallback starter runner for 'draft' and 'swarm' modes
            lead_role = "drafter" if mode == "draft" else "peer"
            peers = [
                BaseAgent(f"{lead_role}-{i}", lead_role, self.router, self.budgeter, self.event_bus)
                for i in range(1, 5)
            ]
            for p in peers:
                self.active_agents[p.agent_id] = p
            try:
                await asyncio.gather(
                    *(
                        p.run(f"You are {p.agent_id} operating in {mode.upper()} mode.", prompt, max_steps=2)
                        for p in peers
                    )
                )
            finally:
                for p in peers:
                    self.active_agents.pop(p.agent_id, None)
