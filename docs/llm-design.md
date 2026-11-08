# `use llm` — a model as a connection

The client for language-model APIs, the first piece of Crow's LLM work
(Vijay, 2026-10-11: "start with what you recommended"): the clients and
SDKs first, the model itself next (Pie's docs/gpt-design.md), the serving
software later. This note is the design; the spec's section 13 (`### llm`)
is the contract, and `src/modules/llm.co` the body.

> Moved from crow on 2026-11-07, when `llm` and `mcp` left crow's standard
> modules for this repository (crow's roadmap, P8). The note is the design
> as it was made: a path under `src/` or `crow_runtime/` is crow's, the
> module's body is `packages/llm/main.co` here, and `tests/llm_server.rs`
> and `tests/mcp.rs` are `tests/model.py` and `tests/mcp.py`. A program
> now writes `use llm` with the package installed, and calls it
> qualified (`llm.chat[…]`).

## What it is

A standard module written in Crow on the `sys` layer (design rule 20), as
`request`, the readers, `web.ws` and `web.mail` are: a program sees one
way to ask a model, the same in `crow run -i` and `crow run`, and every
line of the protocol is Crow the fuzzer and both backends run. It speaks
two wire formats — Anthropic's Messages API and the OpenAI-compatible
chat completions that Ollama, vLLM, OpenRouter and LiteLLM also serve —
behind one surface, so a program written against a local model runs
unchanged against a hosted one.

```
use llm

m = connect['anthropic://claude-sonnet-5-5']
chat = user['What is the capital of Mongolia?']
r = chat[m, chat, system = 'Answer in one word.']
log[r.text]                                  --- Ulaanbaatar
log[r.usage.input]                           --- 21
```

## The surface

| Function | What it does |
|---|---|
| `connect[url]` | A model as a connection: `anthropic://<model>` or `openai://<host>[:port]/<model>`. The key comes from the environment — `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, or the variable `?key=NAME` names — and a local host (`localhost`, `127.0.0.1`) needs none and is reached over `http`; any other host over `https`. Answers the model's description, a parsed value: `m.provider`, `m.model`, `m.base`, `m.key` (the variable's *name*; the key itself is read when a call is made and is in no value). An error value of kind `'llm'`: a URL of neither scheme, no model named, a key's variable unset for a remote host |
| `user[text]`, `user[chat, text]` | A conversation of one user turn; a conversation with another user turn added |
| `said[chat, reply]` | The conversation with the model's reply added, its text and its tool calls, as the next request needs it |
| `result[chat, call, text]` | The conversation with a tool's result added: what the program computed for the call the model made |
| `chat[m, chat]` | The model's reply to the conversation: a parsed value with `text`, `calls` (each `id`, `name`, `args` — the arguments as the model wrote them, a parsed value read as the program's use implies), `usage` (`input`, `output` tokens) and `stop` (`end`, `tool`, `length` or `other`). Options by name: `system = text`, `tools = [f, g]`, `tokens = n` (1024), `temperature = t` (1.0). An error value of kind `'llm'` when no answer came, when the server's status is no success (its message quoted: `the model's server answered 401: invalid x-api-key`), or when its answer is no JSON |
| `stream[m, chat]` | The same request, its reply streamed: a socket the pieces are read from, the same options. An error value of kind `'llm'` as `chat`'s |
| `next[s]` | The next piece of the reply's text as the model writes it; empty text once the stream has ended. A tool call's arguments are gathered, not shown: they are in `reply`. An error value of kind `'llm'` when the connection breaks |
| `reply[s]` | The whole reply the stream carried so far — `chat`'s shape — complete once `next` has answered empty text |
| `embed[m, texts]` | The texts' embeddings, a matrix with a row per text, from an OpenAI-compatible embeddings endpoint (`openai://host/<embedding model>`); an Anthropic connection is refused, since it serves none |

Every function works identically in both modes. The module's functions
are its declaration (docs/extending-crow.md, phase 2): a program calls
`reach`, `ask`, `asked`, `said`, `told`, `chat`, `follow`, `next`, `reply`
and `embed`, each typed by its use, with `chat`'s and `follow`'s options
as their defaults (`system = ''`, `tools = []`, `tokens = 1024`,
`temperature = 1.0`); a helper begins with an underscore. Their effects
are their bodies' (`request` and the socket's reads), which the call
graph carries: a route's handler calling one runs off the server's
threads.

### Tools are the functions

```
--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`city`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

chat = user['How warm is it in Lisbon right now?']
r = chat[m, chat, tools = [weather]]
while r.stop eq 'tool'
    chat = said[chat, r]
    for call in r.calls
        if call.name eq 'weather'
            chat = result[chat, call, weather[call.args.city]]
    r = chat[m, chat, tools = [weather]]
log[r.text]
```

`tools = [weather, search]` hands the functions themselves. The compiler
writes each one's schema from what it already knows: the function's name,
its parameters by name with the kinds inference found for them — an int is
an `integer`, a float a `number`, text a `string`, a bool a `boolean`, a
list an `array` of its items' kind, a record an `object` of its fields, a
parsed value an `object` — and, as the description, the comment written
above the function (`--- the current weather in a city, in °C`), the
sentence the code already carries. A function whose parameter nothing
types is refused before the program runs, with the fix: `weather's
parameter 'city' is typed by nothing — read it in the body, or call
weather[…] once with a value of its kind`.

The program makes the call: the model's `calls` name a tool and its
arguments, the program dispatches on the name, reads the arguments as its
use implies (`call.args.city` is text where `weather` takes text) and
hands the result back with `result`. The module never calls a function of
the program: a call the program did not write would be one `crow check`
could not see, and the program's own `if` is where it decides what a model
may make it do.

### Streaming is a loop the program writes

```
s = stream[m, user['Tell me a story in three sentences.']]
piece = next[s]
while piece.ok and len[piece] gt 0
    log[piece]
    piece = next[s]
r = reply[s]
```

A route forwards the pieces as it reads them, with the response `stream`
the server already has (`fn feed[put, s]` putting each piece); a page
keeps them in a fact a view shows. The pieces are text; a tool call the
model streams is gathered into `reply`, so a program that reads text sees
only text.

### Conversations are parsed values

A conversation is a parsed value — a list of turns, each `role` (`user`,
`assistant`, `tool`) with its `text`, a model's turn with its `calls`, a
tool's with the `id` of the call it answers — because a map holds one kind
and a turn holds several; JSON is the language's shape for that, and a
turn read by a program reads as its use implies. The builders (`user`,
`said`, `result`) keep every turn in the one shape both wire formats are
written from, so a conversation started against one provider continues
against the other.

## The wire

**Anthropic** (`POST {base}/v1/messages`, `x-api-key`, `anthropic-version:
2023-06-01`): `system` top-level, each turn's `content` a list of blocks
— `text`, `tool_use` (the model's calls, `id`, `name`, `input`) and
`tool_result` (`tool_use_id`, `content`); tools as `name`, `description`,
`input_schema`; the reply's `content` blocks read back into `text` and
`calls`, `stop_reason` `end_turn`/`tool_use`/`max_tokens` into `end`/
`tool`/`length`; streaming as SSE events `message_start`,
`content_block_start`, `content_block_delta` (`text_delta` and
`input_json_delta`), `message_delta` (the stop reason and output usage),
`message_stop`.

**OpenAI-compatible** (`POST {base}/v1/chat/completions`, `authorization:
Bearer`): `messages` with the system turn first, the model's turns'
`tool_calls` (`id`, `function.name`, `function.arguments` as JSON text) and
`tool` turns (`tool_call_id`); tools as `{"type": "function", "function":
{name, description, parameters}}`; `choices[0].message` read back,
`finish_reason` `stop`/`tool_calls`/`length`; streaming as SSE `data:`
lines of `choices[0].delta` (`content`, `tool_calls` with argument
fragments) ending with `data: [DONE]`. Embeddings: `POST
{base}/v1/embeddings`, `data[i].embedding`.

`chat` and `embed` send through `request` (one client, one timeout policy,
a rehearsal's recording); `stream` sends through the protocol layer
`request` stands on — `open`, `send`, `read_head` — and reads the body in
pieces, dechunking as it goes, keeping what a read took past an event on
the socket (`unread`) and the reply gathered so far in the connection's
notes (`note`/`noted`), as the WebSocket client and the database drivers
keep theirs.

## Design notes

**A model as a connection.** Considered: the model named on every call
(`chat['anthropic://claude-sonnet-5-5', chat]`), as many SDKs take a model
string. Decided: `connect`, as `db`'s `connect[url]` and `web.mail`'s
`smtp` make a handle once; the URL is parsed and the key's presence
checked where the program says it, and a program's calls read as calls on
one model.

**The connection is a parsed value, not a handle kind.** Considered: a
kind of its own (`Ty::Llm`, as `Mailer` is), threaded through the table,
the backends and the runtime. Decided: the connection holds no secret —
the key is read from the environment as each call is made, and is in no
value a program can print — so what it holds is description, and a parsed
value is readable where a handle is opaque (`m.model` in a log line), with
nothing new to thread (design rule 21 holds the thread to every variant).

**Tools are the functions, the schema the compiler's.** Considered: a
tool written by hand — a name, a description and a schema beside the
function (rejected: the signature said twice, and the two drift); the
module calling the tool for the program and sending the result back by
itself, as "agent" SDKs do (rejected: a call the program never wrote, an
effect `crow check` could not see, and a model deciding what runs with no
`if` of the program's between). Decided: `tools = [f, g]`, the compiler
writes the schema from the signature it inferred and the comment above
the function, and the program dispatches, which is one `if` per tool and
the place a program decides what a model may make it do. (Revisited
2026-10-20, docs/agents-design.md, "Who runs the tools": the loop `act`
dispatches with `apply` over the list of functions the program hands it,
which answers the three objections — every call the model can cause is to
a member of a list the program wrote, `crow check` sees each member
called with its effects, and the program's gates, `allow = [g]`, are asked
before any tool runs. The loop written by hand stays a way.)

**Streaming is a socket read piece by piece.** Considered: a feed function
handed to `stream` and called for each piece, as a response `stream[feed]`
hands its feed a `put` (rejected: a builtin's row takes no function value
today — a function handed to a row is typed by nothing — and a loop the
program writes can stop, show, keep or forward a piece where a callback
only receives it). Decided: `stream` answers a socket, `next` the pieces,
`reply` the whole; the loop is the program's.

**The description is the comment above the function.** Considered: a
description handed beside the tool, `tools = [weather], describe =
map[weather = '…']` (rejected: one thing in two places). Decided: the
comment the code already carries, kept by the parser for every function
(`fn_docs`), is the description; a function with none has none, which
both APIs allow.

**One reply shape.** The two formats' replies are read into one parsed
value — `text`, `calls`, `usage`, `stop` — so a program's reads do not
name a provider; what one provider answers and the other does not (a
cache token count, a log probability) is not there until both mean it.

**Not in this round**, each a later note: images and documents in a turn;
a conversation kept by the server (Anthropic's prompt caching, OpenAI's
responses); batches; a model's answer recorded as a fixture so an agent's
test replays without the model — the next piece, on `request`'s rehearsal
(`crow check --record-fixtures`; built 2026-10-12, docs/rag-design.md); the
`models` to list; an `embed` for Anthropic-side providers (Voyage) once one
speaks a second format.

## What holds it

- The package's own tests (`crow test packages/llm/main.co`): a URL read
  apart, the key's variable named, a conversation written into each wire
  format and read back, an SSE stream of each provider's events parsed
  from bytes, a tool call gathered from its fragments, each reply shape
  read from each provider's JSON, a schema written from a signature.
- `tests/model.py`: a server speaking both wire formats — a reply, a
  tool call, a streamed reply, a streamed tool call, a 401, a body that is
  no JSON, a connection that closes mid-stream — and a program driven
  against it in both modes, its lines compared (crow's
  `tests/llm_server.rs` until the move).
- The refusals (`tests/errors`): a tool that is no function, a tool's
  parameter typed by nothing, a misuse inside the package named at the
  program's line.
- The book: `docs/book/src/llm.md`, every function shown and run (a
  server nobody answers shows the error path).

## The pieces

- `packages/llm/main.co` — the package: its functions are its
  declaration, typed by their use; a name that begins with an underscore
  is the package's own, and a misuse inside it is refused at the
  program's line, naming the function the program called (crow's spec,
  Importing). Until 2026-11-07 it was crow's `src/modules/llm.co`, one
  entry in `src/modules/mod.rs`.
- crow — `describe` and `apply` (sys's forms), through which a tool's
  schema is written and a model's call answered; `request`, `send`,
  `recv`, `stream_of` and the sockets' notes, which `chat`, `follow`,
  `next` and `embed` stand on. Nothing in crow's compiler names llm.
