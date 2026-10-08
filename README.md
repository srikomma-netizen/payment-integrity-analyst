# payment-integrity-analyst

Ask questions about healthcare claims and investigation data in plain English and
get answers back as SQL, a table and a short narrative.

I've spent a lot of time on payment-integrity and fraud analytics, and the part
that always worried me about text-to-SQL is trusting it with sensitive data. This
project is me working through that: the model only plans the query, and plain
code decides whether it's allowed to run.

All data here is synthetic. No real members, providers or PHI.

```
question -> understand -> guard -> (SIU lead approval?) -> execute -> compose -> verify
                ^           |
                +-----------+  guard rejection or SQL error goes back as feedback (max 3 tries)
```

## Running it

Run these from the repo folder. The `uvicorn` and `scripts/` commands need it; the `python -m` ones work anywhere once the package is installed.

```bash
pip install -r requirements.txt
pip install -e .                            # so the python -m commands work from any folder
uvicorn api.main:app --reload        # web console at http://localhost:8000
python scripts/demo.py               # same flows in the terminal
python -m analyst.evals.run_evals    # golden-set evals
python -m pytest -q
```

Without an API key it runs against a scripted stand-in model, which is what the
tests use. Set `GEMINI_API_KEY` to use Gemini (`GEMINI_MODEL`, default
`gemini-2.5-flash`), or `ANTHROPIC_API_KEY` to use Claude (`
`). Gemini
wins if both are set; `LLM_PROVIDER=gemini|anthropic|fake` forces one.

## What's in here

```
analyst/
  warehouse.py   synthetic SQLite mart: claims, payments, providers, vendors,
                 risk_flags, cases, investigator_notes
  catalog.py     table/column docs, metric definitions, which roles see what
  guard.py       SQL guard (sqlglot): SELECT only, allow-listed tables, no
                 restricted columns anywhere, LIMIT enforced
  llm.py         Claude adapter (structured outputs) + the scripted stand-in
  workflow.py    small state-graph runner with checkpoint/resume
  agent.py       the analyst graph and the groundedness check
  insights.py    fixed queries behind the dashboard and case worklist
  evals/         golden questions and the eval runner
docnav/          agent that reads the policy doc section by section
api/main.py      FastAPI service + serves the console
web/             the console (plain HTML/CSS/JS, no build step)
```

## The console

- Overview: claim volume, flag rate, open cases, false-positive rate by rule, provider risk.
- Case worklist: filterable list of investigations, with a detail panel per case.
- Ask the data: shows each step of the run, the SQL, the result as a chart or table, and the trace.
- SIU approvals: queries on investigator notes wait here until an SIU lead approves them.
- Policy navigator: shows which sections of the policy the agent read and in what order.
- Evaluation / Audit trail / Data catalog: eval results, every query and what the guard did with it, and the schema as each role sees it.

Ctrl+K opens a command palette for jumping to pages, cases or asking a question.

## Roles and PHI

| Role | Can query | Needs approval |
|---|---|---|
| analyst | everything except investigator notes, pseudonymous member fields only | no |
| siu_lead | also investigator notes | yes, for notes |

`mrn`, `dob`, `npi` and `bank_account_last4` are blocked for everyone. They're left
out of the schema the model sees, and the guard rejects any query that mentions
them, including in filters and subqueries.

## Notes on a few choices

- The guard returns the SQL it validated, and that's what runs. Otherwise you
  check one string and execute another.
- Evals compare result rows, not SQL text. Two different queries can both be
  right. Each golden case has a reference query; both run against the same data.
- Numbers in the answer are checked against the rows. If the model writes a
  number that isn't in the result, the answer gets a caveat instead of going out
  silently.
- Approval is a pause in the graph, not an if-statement. The run is saved and
  resumed by id once someone decides. The API also refuses decisions from
  anyone who isn't an SIU lead.
- Policy questions use an agent that follows references instead of grabbing
  the top 3 chunks. Policy text says things like "as defined in Section 2.1",
  and a chunk can't follow that. On 5 labelled questions, flat chunks found
  57% of the needed sections and the navigator found all of them.
- Dashboard numbers don't come from the model. They're fixed queries, so they
  come out the same every time.

## Things I'd do next

- Embedding search over the catalog once there are more than a handful of tables.
- Run the evals against Claude on a schedule, not just the stand-in.
- Small-cell suppression in the executor, so tiny groups can't be used to re-identify someone.
- Swap SQLite for BigQuery. sqlglot already handles the dialect; the executor needs a bytes-billed cap.
