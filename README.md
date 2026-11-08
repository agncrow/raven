# Raven

What calls a language model, written in Crow: a model as a connection,
tools from other programs and a Crow program served as one, and the agent
loop that runs a program's own functions as a model's tools — every call
answered through the program's gates, every limit stated. The packages
speak Anthropic's Messages API and the OpenAI-compatible chat completions
(Ollama, vLLM, OpenRouter, LiteLLM), and the Model Context Protocol over
HTTP and stdio. Every answer is the same in `crow run -i` and `crow run`,
and no test needs a model: stand-in servers speak both wire formats and
the protocol, and the examples answer from recorded fixtures.

| Package | What it is |
|---|---|
| [`packages/llm`](packages/llm/REFERENCE.md) | a model as a connection: conversations, replies, tools as the functions themselves, a reply followed piece by piece, embeddings, and the agent loop `act` — gates, limits, compaction, skills, subagents, MCP servers' tools |
| [`packages/mcp`](packages/mcp/REFERENCE.md) | the Model Context Protocol, revision 2026-07-28: a client of other programs' tools over Streamable HTTP and stdio, and a Crow program's functions served as tools, in a route or over its own standard streams |

A program installs a package as any dependency (llm brings mcp with it)
and calls it qualified:

```
deps = map[llm = 'github.com/agncrow/raven/packages/llm@v0.1.0']
```

```
use llm

--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`trim[city]`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

m = llm.reach['anthropic://claude-sonnet-5-5']
done = llm.act[m, llm.ask['Is it warmer in Lisbon or Porto?'], tools = [weather], rounds = 6]
log[done.text]
log[done.steps]
```

| Example | What it is |
|---|---|
| [`examples/agent`](examples/agent/README.md) | a support agent over a shop's orders: three tools, a gate on refunds, the runs traced to a database, evals with a model as the judge, and pages that ask the agent and show its traces |
| [`examples/rag`](examples/rag/README.md) | a page that answers from a folder of notes: chunks embedded and ranked with BM25 beside them, the model asked with what was found — over Pie's `vectors` and `retrieval` packages, so it needs a [Pie](https://github.com/agncrow/pie) checkout beside this one (`../pie`) |

`./test.sh [crow]` runs every suite against a crow binary: the packages'
tests and examples, the book's programs, the parity programs and the
refusals in both modes, the packages driven against stand-in servers
(`tests/model.py`, `tests/mcp.py`, Python's standard library alone), and
the examples' tests from their fixtures.

The book's chapters are [docs/book/src/llm.md](docs/book/src/llm.md) and
[docs/book/src/mcp.md](docs/book/src/mcp.md), every block run in both
modes; the design is [docs/llm-design.md](docs/llm-design.md) and, for the
loop, [docs/agents-design.md](docs/agents-design.md). `llm` and `mcp` were
Crow's standard modules until 2026-11-07, when they moved here: Crow keeps
the language and the `sys` layer they stand on (`describe` and `apply`,
`request`, sockets and processes), and names neither.

Raven is licensed under either of the MIT license
([LICENSE-MIT](LICENSE-MIT)) or the Apache License 2.0
([LICENSE-APACHE](LICENSE-APACHE)), at your option, as Crow is.
