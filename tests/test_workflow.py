from analyst.workflow import END, CheckpointStore, Graph, RunState


def test_linear_graph_runs_to_done():
    g = Graph(entry="a")
    g.add_node("a", lambda s: (s.log("a"), s)[1]).add_edge("a", "b")
    g.add_node("b", lambda s: (s.log("b"), s)[1]).add_edge("b", END)
    s = g.run(RunState(question="q"))
    assert s.status == "done" and s.trace == ["-> a", "a", "-> b", "b"]


def test_router_and_step_limit():
    g = Graph(entry="loop", max_steps=5)
    g.add_node("loop", lambda s: s).add_edge("loop", lambda s: "loop")
    s = g.run(RunState(question="q"))
    assert s.status == "failed" and "step limit" in s.error


def test_node_exception_becomes_failed_run():
    def boom(s):
        raise RuntimeError("kaboom")
    g = Graph(entry="x").add_node("x", boom).add_edge("x", END)
    s = g.run(RunState(question="q"))
    assert s.status == "failed" and "kaboom" in s.error


def test_interrupt_checkpoint_and_resume():
    store = CheckpointStore()

    def gate(s):
        if s.approved is None:
            s.status = "awaiting_approval"
        return s

    def work(s):
        s.answer = "did the work"
        return s

    g = Graph(entry="gate")
    g.add_node("gate", gate).add_edge("gate", "work")
    g.add_node("work", work).add_edge("work", END)

    paused = g.run(RunState(question="q"), store)
    assert paused.status == "awaiting_approval" and paused.answer is None
    assert store.get(paused.run_id) is paused

    resumed = g.resume(paused.run_id, store, approved=True)
    assert resumed.status == "done" and resumed.answer == "did the work"


def test_resume_rejected_ends_run():
    store = CheckpointStore()

    def gate(s):
        s.status = "awaiting_approval"
        return s
    g = Graph(entry="gate").add_node("gate", gate).add_edge("gate", END)
    paused = g.run(RunState(question="q"), store)
    rejected = g.resume(paused.run_id, store, approved=False)
    assert rejected.status == "rejected"
