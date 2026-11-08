# llm — reference

Import with `use llm`. A language model as a connection: one way to ask a model, the same in both modes, over two wire formats — Anthropic's Messages API, and the OpenAI-compatible chat completions that Ollama, vLLM, OpenRouter and LiteLLM serve too — so a program written against a local model runs unchanged against a hosted one. Written in Crow on the `sys` layer (`packages/llm/main.co`), a package called qualified — `llm.reach`, `llm.ask`, `asked`, `said`, `told`, `chat`, `follow`, `next`, `reply`, `embed`, `act` — each typed by its use; `crow test packages/llm/main.co` runs its own tests, and `tests/model.py` drives it against a server speaking both formats in both modes, the agent loop against runs that server scripts. Until 2026-11-07 it was one of crow's standard modules, and this page section 13's `### llm`. The design is docs/llm-design.md and, for the loop, docs/agents-design.md.

```
use llm

m = llm.reach['anthropic://claude-sonnet-5-5']
talk = llm.ask['What is the capital of Mongolia?']
r = llm.chat[m, talk, system = 'Answer in one word.']
log[r.text]                              --- Ulaanbaatar
log[r.usage.input]                       --- 21
```

| Function | Description |
|----------|-------------|
| `reach[url]` | A model as a connection: `anthropic://<model>` or `openai://<host>[:port]/<model>` (any server speaking the OpenAI chat completions: Ollama at `openai://localhost:11434/llama3.2`, vLLM, OpenRouter, LiteLLM). The key is read from the environment when a call is made — `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, or the variable `?key=NAME` names — and is in no value; a local host (`localhost`, `127.0.0.1`) needs none and is reached over `http`, any other over `https`. Answers the model's description, a parsed value: `m.provider`, `m.model`, `m.base`, `m.key` (the variable's name). An error value of kind `'llm'`: a URL of neither scheme, no model named, the key's variable unset for a remote host |
| `ask[text]` | A conversation of one user turn: a parsed value, a list of turns |
| `asked[talk, text]` | The conversation with another user turn added |
| `said[talk, reply]` | The conversation with the model's reply added — its text and its tool calls — as the next request needs it |
| `told[talk, call, text, failed = false]` | The conversation with a tool's result added: what the program computed for one of the reply's `calls`. `failed = true` marks the result a failure the model is to hear as one: the turn says so, Anthropic's block carries `is_error`, and the OpenAI-compatible turn its text alone, since that wire has no flag |
| `chat[m, talk]` | The model's reply: a parsed value with `text`, `calls` (each `id`, `name` and `args`, the arguments as the model wrote them, read as the program's use implies), `usage` (`input` and `output` tokens) and `stop` — `end`, `tool` (the model waits for its calls' results), `length` (its `tokens` reached) or `other`. By name: `system = text`, `tools = [f, g]` (the functions themselves, see below), `tokens = n` (1024), `temperature = t` (1.0). An error value of kind `'llm'` when no answer came, when the server's status is no success (its message quoted: `the model's server answered 401: invalid x-api-key`) or when its answer is no JSON |
| `follow[m, talk]` | The same request, the reply followed as the model writes it: a socket the pieces are read from; the same options and errors as `chat` |
| `next[s]` | The next piece of the reply's text, as the model writes it; empty text once the reply has ended. A tool call the model streams is gathered into `reply`, not shown. An error value of kind `'llm'` when the connection breaks |
| `reply[s]` | The whole reply the socket has carried so far, in `chat`'s shape — complete once `next` has answered empty text |
| `embed[m, texts]` | The texts' embeddings: a matrix, a row per text, from an OpenAI-compatible embeddings endpoint (`openai://host/<embedding model>`); an Anthropic connection is refused, since it serves none |
| `act[m, talk, tools = [], allow = [], rounds = 8, system = '', tokens = 1024, temperature = 1.0, budget = 0, keep = 0, compact = 0, skills = '', servers = []]` | The agent loop: the model runs its tools until it answers or a limit stops it. Each reply's calls are answered in order: the program's gates first (`allow = [g, h]`, each a function of the call — a parsed value with the call's `name`, `args` and `id` — answering a bool; every one must allow the call, and a gate that takes another count of parameters or answers another kind is refused with why before any model is asked), then the tool of the call's name, applied to its arguments read by each parameter's kind (`apply`, sys); a refusal, a name no tool has, an argument missing or of another kind and a tool's own failure are each told to the model as a failed result that says why, so every call is answered. At most `rounds` model calls and at most `budget` tokens in all (0 for none): a limit stops the run after the reply's calls are answered, so the conversation ends well-formed and can be continued. `keep = n` cuts the tool results of rounds older than the last n to a stub naming their size (`[a result of 2400 characters, cut]`, the turn marked `cut`), `compact = n` asks the model for a summary once a reply has read n tokens or more and goes on from the summary alone (a step of `what` `compact`), `skills = folder` names a folder of skills (below), and `servers = [s]` adds the tools each MCP server offers (`use mcp`), called on the server. Answers a record: `text` (the model's last words), `stop` (`end`, `length`, `other`, `rounds`, `budget`), `rounds`, `usage` (`input` and `output` tokens in all, a parsed value as a reply's), `talk` (the conversation as it ended) and `steps`, a frame with a row per model call, tool call and compaction — `step`, `what` (`chat`, `tool` or `compact`), `name` (the model's or the tool's), `input`, `output`, `ms`, and `how` (a chat's stop; a tool's `ok`, `failed`, `arguments`, `refused` or `unknown`). An error value of kind `'llm'` as `chat`'s, for `rounds` below one, for a gate that is none, for a skill that breaks the standard's rules, and for a tool's name two of the lists share |

`chat`, `follow`, `next`, `embed` and `act` are effects: a route's handler that calls one runs off the server's threads (crow's spec, Routes), and `crow check` sees the call.

**Tools are the functions.** `tools = [weather, search]` hands the functions themselves — a group (crow's spec, Specialization) — and the package writes each one's schema from its description (`describe[t]`, sys): its name, its parameters by name with the kinds inference found — an int `integer`, a float `number`, text `string`, a bool `boolean`, a list an `array` of its items' kind, a map, a record or a parsed value an `object` — and, as the description, the comment written above the function. A parameter's kind is what the program's calls of the function hand it, or, where the program never calls it (a tool only the model calls), what its body reads it as: handed to a builtin whose rows take one kind there, `upper[city]` reads it as text; read by its fields, `order.lines` and `order.id`, it is a parsed value — an `object` in the schema, handed as the model wrote it. (Only a call had typed one, and a tool read in its body was refused with the advice to read it there; the field read came 2026-10-20, when a gate that read the call's fields was refused the same way, its body doing what the advice said.) A function whose parameter nothing types is refused before the program runs, with the fix (`weather's parameter 'city' is typed by nothing — read it in the body, or call weather[…] once with a value of its kind`), and so is a `tools` that is not a list of functions written. The program makes the calls: the reply's `calls` name a tool and its arguments, the program dispatches on the name, reads the arguments as its use implies and hands each result back with `told`.

```
--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`city`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

talk = llm.ask['How warm is it in Lisbon right now?']
r = llm.chat[m, talk, tools = [weather]]
while r.stop eq 'tool'
    talk = llm.said[talk, r]
    for call in r.calls
        if call.name eq 'weather'
            talk = llm.told[talk, call, weather[call.args.city]]
    r = llm.chat[m, talk, tools = [weather]]
log[r.text]
```

**The loop is the package's.** Written by hand, that loop has three gaps every program would close again: no limit, a call the `if` does not handle left unanswered (the next request is refused), a tool that fails ending the run with nothing told to the model. `act[m, talk, tools = [weather]]` is the loop written once, in Crow, in the module: the dispatch is `apply` over the group (sys), which the compiler writes for any list of functions, so every call the model can cause is to a member of the list the program wrote, `crow check` sees each member called with its effects, and the limits, the failed results and the record of the steps are one code for every program (docs/agents-design.md). A tool's failure, a wrong argument and an unknown name are told to the model, each as a failed result whose text says why, since a model that hears why can try another way; `told[…, failed = true]` tells one by hand. The program's say between the model's decision and the tool's run is the gate: `allow = [g]` names functions of the call, each asked before a tool runs, and a call one of them refuses is told to the model as a failed result (`remove is not allowed by the program`) rather than run — the `if` the hand-written loop had, kept where the loop is the module's.

```
--- the weather may be asked for Lisbon alone
fn only_lisbon[call]
    if str[call.name] ne 'weather'
        return true
    return str[call.args.city] eq 'Lisbon'

done = llm.act[m, llm.ask['Is it warmer in Lisbon or Porto?'], tools = [weather], allow = [only_lisbon], rounds = 8]
log[done.text]            --- the answer
log[done.stop]            --- end, or the limit that stopped the run
log[done.steps]           --- a frame: every model call and tool call, in order
```

**A subagent is a tool that runs the loop.** A tool is a function the model calls, and a tool whose body runs `act` with a tool list of its own is a subagent: the parent model calls `research[question]`, `research` runs an inner loop — its own model, prompt, tools, gates and limits — and answers what the inner run found. Nothing of it is new: a function sees no top-level name (crow's spec, Function Scope), so the inner loop reaches its model itself; the loop is copied per tool list (crow's spec, Specialization), the inner copy reached through the outer copy's dispatch of `research` by name; and the tool's parameter is typed as any tool's is — `trim[question]` reads the question as text, where `ask[question]` takes any value and types nothing. The inner run's record is the tool's to read: `inner.text` answered is what the parent hears, as any tool's result, and a run that failed fails the tool — `return inner.text` of a failed run answers its failure — so the parent model hears a failed result that says why (`cannot reach the model at …`, told as a failure, `how` `failed` in the steps), or the tool reads `inner.reason` and answers in words of its own, failed or not. The parent's steps frame shows the delegation as one `tool` row named `research`, its `ms` covering the whole inner run; the inner run's steps are the tool's own, read from `inner.steps` and logged or kept where the tool decides (decided 2026-10-20: a subagent needs nothing new, and the delegating run is held in both modes — tests/llm_server.rs, parity 277).

```
--- what a smaller agent finds out about a question, with the weather as
--- its only tool
fn research[question]
    small = llm.reach['openai://localhost:11434/llama3.2']
    inner = llm.act[small, llm.ask[trim[question]], tools = [weather], rounds = 4]
    return inner.text

done = llm.act[m, llm.ask['Is it warm in Lisbon today?'], tools = [research]]
log[done.text]            --- what the parent answered, from what research found
log[done.steps.name]      --- the parent's steps: its model, research, its model
```

**Skills are know-how loaded on demand.** `skills = 'skills/'` names a folder in the Agent Skills format (agentskills.io): each skill a folder holding a `SKILL.md` — YAML frontmatter with its `name` (1 to 64 characters, lowercase letters, digits and single hyphens, neither first nor last, matching the folder) and its `description` (1 to 1024 characters), then the instructions — and, beside it, the files the instructions refer to. The loop reads every skill's frontmatter once, names each with its description in the system prompt, and offers the model two tools of the module's own: `skill[name]` answers a skill's instructions, `skill_file[name, path]` a file inside the skill (a path that leaves it is refused), so a hundred skills cost the model a line each until one is used. A skill that breaks a rule is the call's error value, naming the skill and the rule, before any model is asked; a program's tool named `skill` or `skill_file` is refused. Scripts are not run: running one is `shell`, which waits on the gate.

**A reply followed is a loop the program writes.** A route forwards the pieces as it reads them with the response `stream[feed]`, a page keeps them in a fact a view shows, a script logs them:

```
s = llm.follow[m, llm.ask['Tell me a story in three sentences.']]
piece = llm.next[s]
while piece.ok and len[piece] gt 0
    log[piece]
    piece = llm.next[s]
whole = llm.reply[s]
```

A conversation is a parsed value — a list of turns, each its `role` (`user`, `assistant`, `tool`) and `text`, a model's turn with its `calls`, a tool's with the `id` of the call it answers — because a map holds one kind and a turn holds several, and JSON is the language's shape for that; the builders keep every turn in the one shape both wire formats are written from, so a conversation started against one provider continues against the other. One reply shape the same way: what one provider answers and the other does not is not there until both mean it.

**Design Note: a model as a connection, named `reach`.** Considered: the model named on every call, as most SDKs take a model string (rejected: the URL parsed and the key's presence checked at every call, and a program's calls not read as calls on one model); `connect`, as `db` makes its handle (rejected: `connect` is `db`'s own form, and a program that imports both — an agent that keeps its notes in a database — would write every call qualified); `model` (rejected: the name most programs give the thing they fit). Decided: `reach[url]`, a handle made once; the connection holds no secret, since the key is read from the environment as each call is made, so what it holds is description and a parsed value is readable where a handle kind would be opaque (`m.model` in a log line), with no kind to thread through the compiler (design rule 21).

**Design Note: tools are the functions, the schema the compiler's.** Considered: a tool written by hand — a name, a description and a schema beside the function (rejected: the signature said twice, and the two drift); the module calling the tool for the program and sending the result back by itself, as agent SDKs do (rejected: a call the program never wrote, an effect `crow check` could not see, and a model deciding what runs with no `if` of the program's between); a description handed beside the tool, `describe = map[weather = '…']` (rejected: one thing in two places — the comment above the function is the sentence the code already carries). Decided: `tools = [f, g]`, the compiler writes each schema from the signature it inferred and the function's comment, and the program dispatches.

**Design Note: who runs the tools.** Considered: the program writes the loop, as before (kept, and the only way until 2026-10-20; as the only way rejected: three gaps written again in each program); the lowering expanding `act` into a loop over hidden rows of llm's, the dispatch written by the compiler per listed tool (rejected: the compiler holds the language and names no module, design rule 24); the module dispatching by name at run time over a table from names to function values (rejected: Crow has no such value — a list of functions is a group whose members are known before the program runs — and the checker could not tell which functions will run). Decided (2026-10-20): the module writes the loop in Crow, and the dispatch is `apply` over the group, which the compiler writes for any group. It answers this section's objections to a module calling the program's functions: every call the model can cause is to a member of a list the program wrote, the dispatch is over those members alone, and `crow check` sees each one called, with its effects. A gate the program hands the loop — `allow = [g]`, functions of the call answering a bool, every one asked before a tool runs — needed one rule of inference, since a gate's parameter is a parsed value its body reads by its fields, which had typed nothing (a parameter was typed by a call of it, or by a single-kind builtin read): a parameter read by its fields that nothing else types is a parsed value (Vijay, 2026-10-20). Considered for the gate: a plain function parameter (rejected: a function taking a function is lowered once, so one `act` would serve one tool list per program, and no subagent could have tools of its own); a required gate (rejected: a program with tools that reach nothing writes nothing). A run's record is a frame, one row per step, so traces go to ClickHouse and to charts with no conversion; its columns avoid `kind`, `ok`, `reason` and `trace`, which are every value's own.

**Design Note: a reply followed is a socket read piece by piece, not a feed.** Considered: a function handed to `follow` and called for each piece, as a response `stream[feed]` hands its feed a `put` (rejected: a builtin's row takes no function value — one handed to a row is typed by nothing — and a loop the program writes can stop, show, keep or forward a piece where a callback only receives it). Decided: `follow` answers a socket, `next` the pieces, `reply` the whole; the loop is the program's.

Not in this round, each a later note: images and documents in a turn, a conversation the server keeps (prompt caching, stored responses), batches, and `embed` for a provider that serves no OpenAI-compatible endpoint. A model's answer is kept and replayed as any request's is (crow's spec, Requests: `crow check --record-fixtures`, `crow test --fixtures`), so an agent's test runs without the model.
