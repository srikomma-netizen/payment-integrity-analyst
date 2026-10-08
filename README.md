# Payment Integrity Analyst Agent

A natural-language analyst for a healthcare payment-integrity mart: claims,
payments, providers, vendors, risk flags, and investigation cases. Built the
way a regulated team would ship it: the model decides *what* to query,
deterministic code decides *whether it may run*, the database decides *what
the numbers are*, and the model is only allowed to narrate numbers that exist.
PHI columns cannot be queried at all.

Everything in this repo is synthetic. No real PHI.

```
question ─▶ understand ─▶ guard ─▶ (SIU-lead approval?) ─▶ execute ─▶ compose ─▶ verify ─▶ answer
                │            │
                │            └─ rejected ─▶ back to understand with the reason (max 3 attempts)
                ├─ ambiguous ─▶ ask one clarifying question
                └─ not in catalog / not permitted ─▶ refuse
```

Typical questions: how many claims each risk rule flagged last month, the
false-positive rate of closed cases by rule, duplicate-payment overpayment
totals, investigation cycle time by outcome, which vendors had unverified
bank-change flags.

## What is in here

| Path | What it shows |
|---|---|
| `analyst/warehouse.py` | Synthetic SQLite payment-integrity mart (members, providers, vendors, claims, payments, risk_flags, cases, restricted investigator_notes), read-only executor with timeout and row cap |
| `analyst/catalog.py` | Semantic layer: table and column docs, metric definitions (flag rate, false-positive rate, duplicate overpayment, days to close, recoveries), PHI columns marked restricted and hidden from the model |
| `analyst/guard.py` | Deterministic SQL guard on a real parser: SELECT-only, table allow-list, role permissions, restricted PHI columns anywhere in the query, dangerous functions, LIMIT enforcement |
| `analyst/llm.py` | Claude via the official SDK with structured outputs, plus a deterministic fake behind the same interface |
| `analyst/workflow.py` | ~100-line state-graph runtime: typed state, routers, interrupts with checkpoints, resume, step limit |
| `analyst/agent.py` | The analyst graph: understand, guard, approve, execute, compose, verify, with self-correction loops |
| `analyst/evals/` | Golden set and runner. Scores result correctness against reference SQL, groundedness, PHI blocking, refusals, permissions, retries |
| `docnav/` | Agentic navigation of a payment-integrity policy (outline, search, read with cross-references) versus flat-chunk RAG, with a retrieval-recall A/B |
| `api/main.py` | FastAPI service: ask, list and inspect runs, approve or reject (SIU lead only), policy questions and outline, schema, evals, dashboard, cases, audit, and the web console |
| `analyst/insights.py` | Fixed, reviewed dashboard and case queries for the console (no restricted columns) |
| `web/` | The web console: `index.html`, `app.css`, `app.js` |
| `tests/` | 48 tests, all offline (including stubbed Claude adapters and the console API) |

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
  -d '{"question":"What is the false positive rate of closed cases by rule?"}'
```

## Web console

`uvicorn api.main:app` then open http://localhost:8000/. No build step: plain
HTML, CSS and an ES module in `web/`, served by the same FastAPI app. The
console is styled as an internal payment-integrity tool: navy navigation, a
standing PHI compliance banner, and the validated chart palette.

| Area | Screen | What it shows |
|---|---|---|
| Operations | **Overview** | KPI tiles with monthly sparklines (claims, paid, flagged, open cases, false-positive rate, recoveries), flagged claims by month, rule performance with false-positive meters, case outcomes, provider risk, the SIU queue, and recent agent activity. Built from fixed, reviewed queries, not the model |
| Operations | **Case worklist** | Severity-ranked investigations with search, status, severity, rule and owner filters, sortable columns, CSV export, and a case drawer: claim, pseudonymous member, risk signals with scores, payments with duplicate detection, member claim history, restricted-notes notice, and one-click agent follow-ups |
| Operations | **SIU approvals** | Held queries with the SQL to review; approve or reject resumes the paused run. The server rejects decisions from any other role (403) |
| Agent | **Ask the data** | Pipeline stepper (understand, guard, approve, execute, compose, verify) with per-attempt timeline, grounded answer, auto chart or sortable table, highlighted SQL, raw trace, run history |
| Agent | **Policy navigator** | Outline with the agent's reading order, hop-by-hop reading path, citations, and a section reader with "refers to" and "cited by" links |
| Governance | **Evaluation** | One-click golden-set run with KPIs, per-case results, capability roll-up, and the navigator-versus-flat-chunks retrieval chart |
| Governance | **Audit trail** | Every query, PHI block, guard rejection, approval request and decision, refusal and failure, filterable and exportable |
| Governance | **Data catalog** | Tables and columns exactly as the selected role sees them: locked tables, struck-through PHI columns, approval flags, metric definitions |

Also: a Ctrl+K command palette (pages, case ids, providers, example questions,
free-text questions), role switching from the sidebar, a collapsible sidebar,
light and dark themes, and a phone layout with an overlay menu.

## Roles

| Role | Can query | Approval |
|---|---|---|
| `analyst` | claims, payments, providers, vendors, members (pseudonymous columns only), risk_flags, cases | none |
| `siu_lead` | everything above plus `investigator_notes` | human approval before any notes query runs |

`mrn`, `dob`, `npi`, and `bank_account_last4` are restricted for every role.
They are absent from the schema the model sees and rejected by the guard if
referenced anywhere, including filters and subqueries.

## Design choices

1. **The guard is code, not a prompt.** `guard.py` parses the SQL with sqlglot
   and walks the AST. Prompt instructions are a hint; the parser is a control.
   PHI columns are also removed from the schema the model sees, so the model
   cannot even name them.
2. **Result-level evals, not SQL-string evals.** Two different queries can be
   equally correct. The golden set stores a *reference SQL*, runs both against
   the same mart, and compares the rows. It also scores PHI blocking,
   refusals, clarifications, permission denials, and self-correction, each
   independently, so a failure points at the stage that broke.
3. **Groundedness is checked, not assumed.** `verify` extracts every number in
   the narrative and confirms it exists in the result set. Unsupported numbers
   become a caveat on the answer.
4. **Human approval is a graph interrupt, not an if-statement.** Sensitive
   tables set a flag in the catalog; the guard turns it into a checkpoint; the
   run is persisted and resumed by run id with the SIU lead's decision.
5. **Agentic navigation over flat chunks when the system is not real-time.**
   `docnav` gives the model outline, search, and read tools, and `read` returns
   the section's cross-references. On expert-labelled policy questions the
   navigator reaches 1.00 mean retrieval recall where top-3 flat chunks reach
   0.57, because policy text says "as defined in Section 2.1" and a chunk
   cannot follow that. `read` also returns the sections that cite the one
   being read, so the agent can find a disposition rule that points back at
   a definition.

