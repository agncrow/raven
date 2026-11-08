# A support agent — traces, evals and a page

Raven's agent example, Crow's fourth reference app until it moved here
(docs/agents-design.md, "Traces, evals and a page"): a model answers questions about a shop's orders with three tools
over a SQLite database — `order_status`, `search_orders` and `refund` —
the refund behind the program's gate (`allow = [refund_allowed]`: an
order that has shipped, or one of 100 or more, is refused before the tool
runs). Every run is written down as a trace — a row in `runs`, a row in
`steps` per model call and tool call — and a page shows the runs, their
steps, their tokens and their latency. The tests run the agent over
recorded fixtures with no model, in both modes, and a model-as-judge
grades its answers.

From this folder, `crow run main.co` serves it on port 3530: `/` asks the
agent, `/runs` shows the traces. The model is reached by the environment:
`AGENT_MODEL`, `openai://127.0.0.1:8765/mlx-community/Ministral-3-8B-Reasoning-2512-4bit`
unless set — a local mlx-lm server (`python -m mlx_lm server --model
mlx-community/Ministral-3-8B-Reasoning-2512-4bit --port 8765`); any
server speaking the OpenAI chat completions with tool calls serves, and
`anthropic://<model>` does too.

## The parts

| File | What it holds |
|---|---|
| `main.co` | `serve`, the two pages, the tables made and the orders seeded |
| `agent/store.co` | The database: `orders`, `runs`, `steps`; `setup` makes the tables and seeds the five orders the same every time (a refund made earlier is undone); `orders[c]` reads them whole; `crow test agent/store.co` |
| `agent/support.co` | The agent: the three tools (each one's comment is what the model is told of it; an order's number arrives as text), the gate, `run[question]` — `act` over them, at most six rounds — and `record`, which writes the run's row and its steps' rows; the evals at the end: exact checks on the steps frame (the tools called in order, the gate's refusal, the stop) and the judge's verdict; `crow test --fixtures agent/support.co` |
| `agent/judge.co` | The model as judge: `graded[m, question, answer, rubric]` asks for PASS or FAIL on the first line, `verdict` reads it; `crow test agent/judge.co` |
| `agent/trace.co` | The page over the traces: the runs newest first, the steps of the one opened, the tokens and the latency of each run charted; `crow test --fixtures agent/trace.co` |
| `agent/chat.co` | The page that asks: a question, the agent's answer, the tools it called, and the run's steps under it; `crow test --fixtures agent/chat.co` |

## The tests, without a model

The evals and the pages ask the model, so they replay what a recording
kept under `.crow/fixtures` — one file per request, named by its method,
URL and body (spec, Requests) — and read the orders from `agent.db`,
which `setup` seeds the same before every test:

```
crow test agent/store.co
crow test agent/judge.co
crow test --fixtures agent/support.co
crow test --fixtures agent/trace.co
crow test --fixtures agent/chat.co
crow test --fixtures --compiled agent/trace.co
crow test --fixtures --compiled agent/chat.co
```

(`crow test` runs one file's blocks: a part's tests run from the part;
`--compiled` runs each page's tests against the compiled app, the block
itself interpreted.)

The fixtures in this folder were recorded against the mlx-lm server
above. A changed prompt, tool comment or question is a changed request,
which the failing test names (``no fixture holds `POST
http://127.0.0.1:8765/v1/chat/completions` — `crow check
--record-fixtures` records it where the server is``). To record again:
start the server, seed the database, delete the fixtures and run the
recording, which runs every test and keeps what each request answered:

```
crow test agent/store.co               # makes and seeds agent.db
rm .crow/fixtures/*.json
crow check --record-fixtures main.co
```

Two things a recording does that the tests do not (crow-check.md): its
writes are recorded, not made, so the database must be seeded before it
starts; and a read statement is answered by the database once, its first
answer kept for every later read of the same text, whatever the
parameters — which is why the tools read the orders whole
(`store.orders`) and pick the row in Crow, so the recording and the
replay are told the same rows.
