# Finance Analyst Agent

A natural-language analyst for a finance data mart, built the way a regulated
team would ship it: the model decides *what* to query, deterministic code
decides *whether it may run*, the database decides *what the numbers are*, and
the model is only allowed to narrate numbers that exist.

Everything in this repo is synthetic.

```
question ─▶ understand ─▶ guard ─▶ (human approval?) ─▶ execute ─▶ compose ─▶ verify ─▶ answer
                │            │
                │            └─ rejected ─▶ back to understand with the reason (max 3 attempts)
                ├─ ambiguous ─▶ ask one clarifying question
                └─ not in catalog / not permitted ─▶ refuse
```

## What is in here

| Path | What it shows |
|---|---|
| `analyst/warehouse.py` | Synthetic SQLite accounting mart (GL, invoices, vendors, budgets, restricted payroll), read-only executor with timeout and row cap |
| `analyst/catalog.py` | Semantic layer: table and column docs, business metric definitions, role-based visibility, schema retrieval per question |
| `analyst/guard.py` | Deterministic SQL guard on a real parser: SELECT-only, table allow-list, role permissions, restricted columns, dangerous functions, LIMIT enforcement |
| `analyst/llm.py` | Claude via the official SDK with structured outputs, plus a deterministic fake behind the same interface |
| `analyst/workflow.py` | ~100-line state-graph runtime: typed state, routers, interrupts with checkpoints, resume, step limit |
| `analyst/agent.py` | The analyst graph: understand, guard, approve, execute, compose, verify, with self-correction loops |
| `analyst/evals/` | Golden set and runner. Scores result correctness against reference SQL, groundedness, refusals, permissions, retries |
| `docnav/` | Agentic document navigation (outline, search, read with cross-references) versus flat-chunk RAG, with a retrieval-recall A/B |
| `api/main.py` | FastAPI service: ask, inspect run, approve or reject, policy questions |
| `tests/` | 40 tests, all offline (including stubbed Claude adapters) |

## Run it

```bash
pip install -r requirements.txt
python scripts/demo.py                  # offline walkthrough with the fake LLM
python -m analyst.evals.run_evals       # eval report
python -m docnav.baseline               # flat chunks vs navigator
python -m pytest -q
uvicorn api.main:app --reload           # then POST /ask, /policy/ask
```

To use Claude, set `ANTHROPIC_API_KEY` (model defaults to `claude-opus-5-5`,
override with `ANALYST_MODEL`). Everything else is unchanged: same graph,
same guard, same evals.

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"Which cost centers were over budget in Q1 2025, and by how much?"}'
```

## Design choices

1. **The guard is code, not a prompt.** `guard.py` parses the SQL with sqlglot
   and walks the AST. Prompt instructions are a hint; the parser is a control.
   Restricted columns are also removed from the schema the model sees, so the
   model cannot even name them.
2. **Result-level evals, not SQL-string evals.** Two different queries can be
   equally correct. The golden set stores a *reference SQL*, runs both against
   the same warehouse, and compares the rows. It also scores refusals,
   clarifications, permission denials, and self-correction, each independently,
   so a failure points at the stage that broke.
3. **Groundedness is checked, not assumed.** `verify` extracts every number in
   the narrative and confirms it exists in the result set (with rounding and
   "96.6k" tolerance). Unsupported numbers become a caveat on the answer.
4. **Human approval is a graph interrupt, not an if-statement.** Sensitive
   tables set a flag in the catalog; the guard turns it into a checkpoint; the
   run is persisted and resumed by run id with the reviewer's decision. The
   same mechanism works for any node.
5. **Agentic navigation over flat chunks when the system is not real-time.**
   `docnav` gives the model outline, search, and read tools, and `read` returns
   the section's cross-references. On expert-labelled questions the navigator
   reaches 100% retrieval recall where top-3 flat chunks reach 73%, because
   policy text says "as defined in Section 2.1" and a chunk cannot follow that.

