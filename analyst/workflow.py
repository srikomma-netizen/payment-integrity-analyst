"""A small, explicit state-graph runtime.

This is the pattern LangGraph implements, written out in ~100 lines so the
control flow is easy to follow:

  * typed state that every node reads and returns
  * nodes are plain functions  state -> state
  * edges are either fixed or a router function  state -> next node name
  * a node can *interrupt* (human approval); the run is checkpointed and
    resumed later with the human's decision
  * step limit so a routing bug can never loop forever

No framework dependency means the tests exercise exactly what runs in prod.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

END = "__end__"


@dataclass
class RunState:
    question: str
    role: str = "analyst"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"            # running | awaiting_approval | done | refused | needs_clarification | failed
    next_node: str = ""
    attempts: int = 0
    feedback: str | None = None
    approved: bool | None = None
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
    """In-memory checkpoint store. Swap for Redis/Postgres in production;
    the graph only needs get/put."""

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

    def put(self, state: RunState) -> None:
        self._runs[state.run_id] = state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)


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
        """dst is either a node name / END, or a router function."""
        self._edges[src] = dst
        return self

    def _next(self, current: str, state: RunState) -> str:
        edge = self._edges.get(current, END)
        return edge(state) if callable(edge) else edge

    def run(self, state: RunState, store: CheckpointStore | None = None) -> RunState:
        state.next_node = state.next_node or self.entry
        steps = 0
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
            except Exception as e:  # a node crash is a failed run, not a crashed server
                state.status, state.error = "failed", f"{name}: {type(e).__name__}: {e}"
                state.next_node = END
                break
            if state.status == "awaiting_approval":
                # interrupt: persist and hand control to a human
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
