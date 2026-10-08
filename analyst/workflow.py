"""Tiny langgraph-style runner with checkpoint/resume."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

END = "__end__"


@dataclass
class RunState:
    question: str
    role: str = "analyst"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    status: str = "running"            # running | awaiting_approval | done | refused | needs_clarification | failed
    next_node: str = ""                # resume() picks up here
    attempts: int = 0
    feedback: str | None = None        # set -> back to the planner
    approved: bool | None = None       # None = not asked yet
    plan: dict[str, Any] | None = None
    sql: str | None = None
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    answer: str | None = None
    caveats: list[str] = field(default_factory=list)
    grounded: bool | None = None
    error: str | None = None
    trace: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.trace.append(msg)


Node = Callable[[RunState], RunState]
Router = Callable[[RunState], str]


class CheckpointStore:
    """In-memory checkpoint store."""

    # TODO: redis/postgres before running more than one worker

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

    def put(self, state: RunState) -> None:
        self._runs[state.run_id] = state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def all(self) -> list[RunState]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)


class Graph:
    def __init__(self, entry: str, *, max_steps: int = 25) -> None:
        self.entry = entry
        self.max_steps = max_steps
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, str | Router] = {}

    def add_node(self, name: str, fn: Node) -> "Graph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str | Router) -> "Graph":
        self._edges[src] = dst
        return self

    def _next(self, current: str, state: RunState) -> str:
        edge = self._edges.get(current, END)
        return edge(state) if callable(edge) else edge

    def run(self, state: RunState, store: CheckpointStore | None = None) -> RunState:
        """Run until END or an approval interrupt. Also used to resume."""
        state.next_node = state.next_node or self.entry
        steps = 0  # per call, resumes get a fresh budget
        while state.next_node != END:
            if steps >= self.max_steps:
                state.status, state.error = "failed", f"step limit {self.max_steps} exceeded"
                break
            steps += 1
            name = state.next_node
            node = self._nodes[name]
            state.log(f"-> {name}")
            try:
                state = node(state)
            except Exception as e:  # fail the run, not the server
                state.status, state.error = "failed", f"{name}: {type(e).__name__}: {e}"
                state.next_node = END
                break
            if state.status == "awaiting_approval":
                # pick next node before saving so resume knows where to go
                state.next_node = self._next(name, state)
                if store:
                    store.put(state)
                return state
            state.next_node = self._next(name, state)
        if state.status == "running":
            state.status = "done"
        if store:
            store.put(state)
        return state

    def resume(self, run_id: str, store: CheckpointStore, *, approved: bool) -> RunState:
        """Apply a human decision to a paused run."""
        state = store.get(run_id)
        if state is None:
            raise KeyError(run_id)
        if state.status != "awaiting_approval":
            raise ValueError(f"run {run_id} is not awaiting approval (status={state.status})")
        state.approved = approved
        state.status = "running"
        state.log(f"human decision: {'approved' if approved else 'rejected'}")
        if not approved:
            state.status, state.next_node = "rejected", END
            store.put(state)
            return state
        return self.run(state, store)
