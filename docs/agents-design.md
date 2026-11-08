# Agents in `use llm` — the loop, and what plugs into it

The second piece of Crow's LLM work, after the client (docs/llm-design.md):
an agent is a model running tools in a loop until it answers, and a
harness is the program around that loop — dispatch, limits, permissions,
context, records. Vijay (2026-10-18): "enhance the llm module to create
agents, harness skills etc.", the ideas explained first (the guide "Agents
in Crow", 2026-10-18, revised 2026-10-20 for the compiler's freeze), then
(2026-10-20) "build the missing pieces with your recommendations where you
need my decisions". This note is the design; the spec's `### llm` section
is the contract, and `src/modules/llm.co` the body.

> Moved from crow on 2026-11-07, when `llm` and `mcp` left crow's standard
> modules for this repository (crow's roadmap, P8). The note is the design
> as it was made: a path under `src/` or `crow_runtime/` is crow's, the
> module's body is `packages/llm/main.co` here, and `tests/llm_server.rs`
> and `tests/mcp.rs` are `tests/model.py` and `tests/mcp.py`. A program
> now writes `use llm` with the package installed, and calls it
> qualified (`llm.chat[…]`).

## What it is

Crow could run an agent before this round: `chat` with `tools = [f, g]`,
the model's `calls` dispatched by the program's own `if`, each result
handed back with `told`, in a `while` the program writes (spec, Tools are
the functions). Written by hand, that loop has three gaps every program
closes again — no limit, a call the `if` does not handle left unanswered
(the next request is refused), a tool that fails ending the run with
nothing told to the model — and limits, error results and a record of the
steps get written again, differently, each time. `act` closes them once:

```
use llm

--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`trim[city]`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

m = reach['anthropic://claude-sonnet-5-5']
done = act[m, ask['Is it warmer in Lisbon or Porto?'], tools = [weather], rounds = 8]
log[done.text]            --- the answer
log[done.stop]            --- end, or the limit that stopped the run
log[done.steps]           --- a frame: every model call and tool call, in order
```

Everything in it is module code written in Crow (design rule 20), on two
operations of the standard library's own (`describe` and `apply`, sys:
phase 4 of the compiler's freeze, docs/extending-crow.md section 7): the
compiler learns nothing of llm's (design rule 24). One bug of the
compiler's was fixed on the way, named below.

## The surface

| Function | What it does |
|---|---|
| `act[m, talk, tools = [], allow = [], rounds = 8, system = '', tokens = 1024, temperature = 1.0, budget = 0, keep = 0, compact = 0, skills = '', servers = []]` | The model runs its tools in a loop until it answers or a limit stops it. Each reply's calls are answered in order: the program's gates first (`allow = [g]`, each a function of the call answering a bool, every one asked), then the tool of the call's name, applied to its arguments read by each parameter's kind; a refusal, a name no tool has, an argument missing or of another kind and a tool's own failure are each told to the model as a failed result that says why, so every call is answered. At most `rounds` model calls, at most `budget` tokens in all (0 for none): the limit stops the run after the reply's calls are answered, so the conversation ends well-formed and can be continued. Answers a record: `text` (the model's last words), `stop` (`end`, `length`, `other`, `rounds`, `budget`), `rounds`, `usage` (`input`, `output` tokens in all), `talk` (the conversation as it ended) and `steps`, a frame with a row per model call and per tool call: `step`, `what` (`chat`, `tool`), `name` (the model's or the tool's), `input`, `output`, `ms`, `how` (a chat's stop; a tool's `ok`, `failed`, `arguments`, `unknown`). An error value of kind `'llm'` as `chat`'s, and for `rounds` below one |
| `told[talk, call, text, failed = false]` | As before; `failed = true` marks the result a failure the model is to hear as one: Anthropic's block carries `is_error`, the OpenAI-compatible turn its text alone (the wire has no flag) |

## Design notes

**Who runs the tools.** Considered: (A) the program writes the loop, as
before (kept as a way; rejected as the only way: the three gaps, written
again in each program); (B) the lowering expands `act` into a loop over
hidden rows of llm's, the dispatch written by the compiler per listed tool
(the guide's first recommendation, 2026-10-18 afternoon; rejected that
evening by design rule 24: the compiler names no module); (C) the module
dispatches by name at run time over a table from names to function values
(rejected: Crow has no such value — a list of functions is a group whose
members are known before the program runs — and the checker could not
tell which functions will run). Decided (2026-10-20): B in the form phase
4 made possible — the module writes the loop in Crow, and the dispatch is
`apply` over the group, which the compiler writes for any group of
functions. It answers the three objections of the spec's note "tools are
the functions": every call the model can cause is to a member of a list
the program wrote, and the dispatch is over those members alone; `crow
check` sees each member called, with its effects, since the dispatch is
written calls; and the program's own `if` is the list it wrote — and the
gate, below, once it can be typed. The hand-written loop stays what it is:
`chat`, `said`, `told` are the loop's own pieces.

**The gate, and the rule it needed.** The guide's `allow`: a function of
the call the program hands the loop, asked before every tool runs. Two
shapes were built and each met the compiler as it was. As a plain
function parameter (`act[m, talk, allowed, tools = …]`) it is typed by
the loop's own application of it, but a function taking a function is
lowered once (spec, Specialization: "a function taking a function is not
copied per kind"), so one `act` would serve one tool list per program —
no subagent with tools of its own beside its parent. As a group applied
by name (`allow = [g, h]`, each asked, which composes gates for free) the
module hands the call as a parsed value, and a parameter was typed only
by a call of it or by a single-kind builtin read in its body
(`upper[city]`): a body that reads the call's fields (`call.name eq
'weather'`) typed nothing, so `describe` refused the gate as "typed by
nothing". The rule that closes it is small and general, and Vijay asked
for it (2026-10-20): a parameter whose fields the body reads, and nothing
else types, is a parsed value — `param_reads` notes a field read as
`Json`, where it notes `upper[x]` as text — which also lets a tool take
an object (`ship[order]` reading `order.lines`), refused the same way
before. Built so: `allow = [g]`, each gate checked at the start (one
parameter, a bool answered, else the call's error says so), each applied
by name before a tool runs with the call under its parameter's name, a
refusal told to the model as a failed result (`how` `refused`). Deferred
still: a reach on `describe` (what a function touches), which a gate that
refuses effectful tools by default would need.

**The outcome told to the model.** A bad argument, a tool's failure and an
unknown name are all told, each as a failed result whose text says why —
`apply`'s own reason for an argument, the tool's reason, `no tool is
named nothing` — rather than ending the run, since a model that hears why
can try another way (the guide, §2–3). The text is the same on both
wires; Anthropic's carries `is_error` beside it. `apply` answers a value
encoded as text, so a text's own words are what the model reads
(`_plain_of`), any other value as its JSON.

**A run's record is a frame.** Considered: a list of records. Decided: a
frame, one row per step, so traces go to ClickHouse and to charts with
no conversion; its columns avoid `kind`, `ok`, `reason` and `trace`,
which are every value's own (a column named `kind` is unreadable, since
`f.kind` is the frame's status). The run's answer is a record (`Done`),
since a parsed value holds no frame: `done.steps` is the frame,
`done.talk` the conversation, `done.usage` a parsed value as a reply's.

**Where it lives.** The loop in `use llm` (and context and skills, when
they come); MCP as `use mcp`, since a server needs no model. A module
written in Crow declares its functions by writing them (phase 2), so a
module is one file and one line of the manifest.

**Names.** `act`, `rounds`, `budget` — short lowercase words, none a
builtin's or an export's (`budget`, `keep` and `step` are options of
other rows, which take nothing from a parameter's name). `tokens` is the
reply's limit, as `chat`'s; `budget` the run's. A helper of the loop was
first named `_applied`, which the streaming code already had: a second
function of one name in a module reads as the first, its parameters
"undefined variables" — the compiler does not name the collision.

**Context: cut, then compact.** `keep = n` replaces the tool results of
rounds older than the last n with a stub naming their size — a result
once used is what fills a window (the guide, §6) — and marks the turn
`cut`, so a cut turn is never cut again and a program reading the
conversation back knows. `compact = n` asks the model, once a reply has
read n tokens or more, for a summary of the run for itself (the task,
what was done and found, what remains), with no tools so it answers in
words, and goes on from a conversation of that summary alone; the step is
recorded as a `compact` row. The summary is asked in one user turn of a
conversation of its own, holding the transcript (who spoke, the calls, the
results): a user turn added after a tool's results breaks the alternation
of roles the wires require, which the first run against a real model
showed ("conversation roles must alternate"). Both happen at a round's start, after the
previous reply's calls were answered, so the conversation sent is always
well-formed. Considered and left to the program: notes kept in a file
(two lines of the program's own tools), and clearing by age in tokens
rather than rounds (a round is what the model sees as a unit).

**Skills.** The Agent Skills format as published (agentskills.io): the
module reads each `SKILL.md`'s frontmatter by the standard's rules —
`name` 1 to 64 characters of lowercase letters, digits and single hyphens,
neither first nor last, matching the folder; `description` 1 to 1024 —
and refuses a folder that breaks one before any model is asked, naming
the skill and the rule. Progressive disclosure is two tools of the
module's own: `skill[name]` answers the instructions, `skill_file[name,
path]` a file inside the skill, a path that leaves it refused; the system
prompt lists each skill's name and description. Scripts are not run: that
is `shell` behind the gate, which waits on the gate. Considered: `crow
check` holding the folder to the rules (rejected: the checker is the
compiler's, which names no module; the module checks, and a test block
can hold a folder with no model).

**MCP.** A module of its own, `use mcp` (spec, section 13), the client on
`request`, the server a function a POST route calls, the 2026-07-28
revision as published: stateless, `_meta` on every request, the headers
the transport mirrors, the errors it names. `servers = [s]` on `act` lists
each server's tools into the schemas the model is told and calls them on
the server; a name two tools share — a program's, a skill's, a server's
— is refused at the start, as the protocol advises against aggregating
colliding names. The schema writer is written twice (llm's for a model,
mcp's for `tools/list`): a helper is a module's own, and an export only
programs can call would be documented for nothing.

**A subagent is a tool that runs the loop.** The guide's claim — a
subagent needs nothing new, since a tool is a program function and a
function may run `act` with a tool list of its own — was verified
2026-10-20 in both modes (spec, llm: A subagent is a tool that runs the
loop), and found nothing to fix in the compiler: the parent model calls
`research[question]`, `research` runs an inner `act` over its own model
and tools and answers the inner run's text. Three things the shape rests
on, each already the language's: a function sees no top-level name, so
the inner loop reaches its model itself (`reach` in the body); the loop
is copied per tool list, and the inner copy is reached only through the
outer copy's dispatch of `research` by name — the path parity 274–276
had found three bugs on, which holds here; and the tool's parameter is
typed by its body, where `ask[question]` types nothing (`ask` takes any
value): `trim[question]` reads it as text. Decided, by recommendation:
the inner run's record is the tool's to read — `return inner.text` hands
the parent what the run found, and a run that failed fails the tool, so
the parent model hears a failed result that says why (the loop's own
rule for a tool's failure, nothing added), or the tool reads
`inner.reason` and answers in words of its own; the parent's steps frame
shows the delegation as one `tool` row whose `ms` covers the whole inner
run, and the inner run's steps are the tool's own (`inner.steps`), logged
or kept where the tool decides — no nesting of frames, since a frame's
cell holds no frame and a trace that joins them is the evals' work.
Considered: the inner run's steps carried into the parent's frame
(rejected: a tool's rows beside a model's would need a `depth` column
every reader skips, and a tool that is not a subagent has none); a
subagent as a form of its own (`agent[m, tools = …]` handed as a tool;
rejected: a tool is a function, and this one is a function that calls
`act` — one way).

**MCP over stdio (2026-10-20).** The revision's other transport
(https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio):
the client launches the server as a subprocess; messages are
newline-delimited JSON-RPC with no embedded newlines, request metadata in
the body's `_meta` alone ("there is no header layer"); the server may
log to standard error and must write nothing but messages to standard
output; the client ends the server by closing its input, waiting, then
forcing it (TERM before KILL), and a server should exit when its input
closes; a client should restart a server that exits unexpectedly; a
dual-era client probes with `server/discover` and falls back to
`initialize` on any error that is not a recognized modern one, and a
modern-only server should name the versions it speaks in its answer to
`initialize`. What it needed was "a handle for a process held open", and
the handle is the sys layer's socket: `start[command]` (crow_runtime's
`sys::start`) runs the command through `sh -c`, as `shell` does, with its
standard input and output piped and its standard error inherited, a
thread handing the output over a channel so a read can give up after its
seconds, and answers a socket — the stream (`send`, `recv`, `close`) and
the bytes read past the last message (`leftover`, `unread`) that a
line-framed protocol needs between calls. No handle kind was added: a
kind is a `Value` variant, a `Kind`, an `Arg` and an arm in every rule
that meets them, which is a compiler change, and a process is bytes in
and bytes out. `close` of its stream (or the last value holding it gone)
runs the transport's shutdown: the input closed, 1.5 s for the exit, TERM
to the process group — the child is its group's leader, so `sh` and what
it runs both get it — 0.5 s more, KILL. The runtime keeps one process per
command on each thread, while the thread lives (a served program's
handlers run on threads of their own, and two exchanges with one process
would take each other's lines): `start` answers the running one again,
and starts the command anew once it ended, which is the restart the
transport asks for; at the program's end the pipes close and a server
that honours the transport exits. The surface: `server[command = '…']`,
the command as `shell` takes it (considered and rejected: the transport
in the URL's text, a second export, a program and a list of arguments —
the spec's Design Note "a process is a socket" has each with why);
`server` stays a parsed value and the process starts at the first message
(a record holding the socket was rejected because `act` reads its servers
as their JSON — a list option's `[]` default makes a copy whose items
have no kind — and a handle is no JSON), so `discover`, `catalog`,
`tool` and `servers = [s]` are unchanged; the ids count up on the
process's notes (`note`, `noted`), so a late response to a request whose
wait ran out is passed over, as notifications are; a line that is no
JSON message is refused with its words. `listen[tools = [f, g]]` is the
server side: `answer`'s logic factored into `_answered` (a message in,
a status and a body out, the header checks only where the transport
mirrors them), a loop over `input[]` writing each answer with `log` —
`input` flushes what `log` holds before it waits, so an answer is on the
wire before the next line is read — until the input ends; a legacy
`initialize` is refused naming the revision spoken, on both transports.
The client is modern-only and does not probe: `discover` is the
program's to call, and a legacy server's own error is what it gets.
Found on the way, none a compiler bug: a local named like a parameter of
the same function (`given` as `_answered`'s message and as the arguments
handed to `_call_result`) is the parameter — the refusal came out at the
`apply` far below it; a shell stand-in reading JSON must read it compact
and spaced, since `encode[map]` writes no space after a colon.

**Not in this round** (the guide's §15, in order): parallel calls of one
reply (a loop with effects runs in order); runs that resume (the note
below); a stdio server's exchanges as fixtures (a rehearsal runs the
process for real, as it opens a socket for real). The dual-era fallback
to `initialize` is not built and will not be: the client speaks the
2026-07-28 revision alone, and a server of an earlier revision answers it
with an error (Vijay, 2026-10-20: no backward compatibility). Traces, evals and an agent served as a page are the next
section, `examples/agent` (2026-10-20).

**Durable runs (a design note, nothing built; a decision for Vijay).**
What holds already: a run ends well formed at every stop — a limit stops
it after the reply's calls are answered — and `done.talk` is the whole
conversation as it ended, a parsed value a program keeps where it likes
(a fact, a table's cell, a file) and hands back: `act[m, done.talk,
tools = …]` goes on from a `rounds` stop, `act[m, asked[done.talk,
'…'], …]` continues the conversation. So resuming after a stop needs
nothing new. What does not hold: a process killed mid-round loses the
round in flight (the model's reply, the tool results told so far), and
nothing records a run until it ends, since the steps frame is answered
at the end. Considered: (1) `trail = conn`, the loop writing each step's
row to a table as it happens (rejected: the module would fix the table's
shape, and every program would pay a database effect inside the loop,
which `crow check` would see in every run); (2) `trail = path`, a file
appended per step (rejected for the same reason, with the format the
module's); (3) the program writing the loop by hand with its own writes
between the steps (kept as the escape, and the three gaps again);
(4) `trail = f`, a function of the step the loop calls after each step —
a parsed value holding the step's row and the talk as it stands — so the
program writes it where it wants (a table, a file, a fact) and resumes by
handing the last talk its trail kept back to `act`. Recommended: (4), the
gate's shape again (a function applied by name, typed by the fields its
body reads, under the rule of 2026-10-20), so it needs nothing new in the
compiler and composes with traces (a trail that inserts the row is the
live trace). What a resume cannot know is whether a tool's effect ran
before the crash and its result was lost: the trail is written after the
step, so a resumed run replays from the last complete step, and a tool
whose effect must not run twice checks for its own work (an order
already shipped) — the tool's business, as the gate's refusal is the
program's. Open: whether `trail = f` is wanted now or waits for an app
that asks for it; the traces example (examples/agent) writes its runs
after the fact and would be the first user.

## Traces, evals and a page

The fourth reference app, `examples/agent`: a support agent over a
shop's orders, built on `act` with nothing added to the module. What the
roadmap listed under "Evals and traces" — runs as frames in a database
with a page, LLM-as-judge in `test` blocks over fixtures — and "agents
served as pages", decided by recommendation where the design left them
open.

**The agent.** Five orders in SQLite (`orders`: id, customer, item,
amount, status), made and seeded by the app's own `CREATE TABLE` and
`INSERT OR REPLACE` at every start and before every test, so a refund an
earlier run made is undone and every run reads the rows the fixtures were
recorded over. Three tools — `order_status[number]`, `search_orders[
customer]`, `refund[number]` — and one gate, `refund_allowed[call]`: a
refund of an order that has shipped, or of one of 100 or more, is refused
before the tool runs (`how` `refused` in the steps), every other call
allowed. `run[question]` is `act` over them, six rounds at most,
temperature 0, and then the trace. Two things the writing found. A plain
function sees no top-level connection (`'book' is a module variable,
which a function does not see`), and a tool is handed nothing but the
model's arguments, so each tool opens the shop's own (`store.shop[]`; one
handle per SQLite file in a process, so it costs nothing). And a tool's
parameter is typed by what the body reads it as, where a mask comparison
(`found[found.id eq id]`), arithmetic with a literal, a position and a
comparison all type nothing — only a builtin whose row takes one kind at
that position (`trim[number]`, `repeat['x', n]`) or a default literal
does — so an order's number arrives as text and `int[trim[number]]`
makes the number, which reads as what a model hands over anyway (its
arguments are JSON, and the reader of an int argument takes `"2"` as it
takes `2`). Whether a comparison with a typed column should type the
parameter is a question for the inference rule, not the example.

**A trace is two tables.** `runs` (id, `at` as ISO text — SQLite has no
moment, and `moment[column]` reads it back for the chart — question,
model, answer, stop, rounds, input, output, ms) and `steps` (run, then
the loop's own columns: step, what, name, input, output, ms, how). The
frame `act` answers is written as it is: `steps.run = id`, then
`insert[c, 'steps', steps]` — one statement, no loop over rows — which is
what the decision to make the record a frame was for. The run's
milliseconds are the sum of its steps'. Considered: a trace written by the
loop itself (rejected: the module names no table and no database — a
program says where its traces go, in one function); a `traces` table of
JSON (rejected: a frame of steps answers a frame of steps; a row per step
is what a chart and a `GROUP BY` read). The page (`agent/trace.co`)
reads them as derivations held per session (`run_id` is the session's):
the runs newest first with `Open`, the steps of the run open, and two
charts over the runs oldest first — tokens read and written, and
milliseconds — on the `at` column read as moments. Not built: ClickHouse,
which the same `query` and `insert` reach (`clickhouse://` in place of
`sqlite://`, the tables made the same way), and a dashboard of costs
across models, since there is one model and no price.

**Evals are test blocks over fixtures.** The questions are four and short
(the model is slow): an order's status, a refund the gate refuses, a
refund that goes through, a customer's orders. Each eval runs `run[…]`
and checks the frame exactly — `r.tools` (the tool rows' names, in
order) equals `['order_status']`, the refund row's `how` is `refused` or
`ok`, the order's status after, the stop is `end`, the step count — then
asks the judge: `judge.graded[m, question, answer, rubric]`, a `chat`
told to reply PASS or FAIL on its first line and one sentence why,
`verdict` reading the first line. The judge stays in the example
(decided, by recommendation): it is one `chat` with a system prompt and
a line of parsing, a rubric is the program's as a system prompt is, and a
module function would fix the reply's form for every program; a program
that wants a score asks for one the same way. Recording: `crow check
--record-fixtures main.co` once against the real model (mlx-lm 0.32.2
serving `mlx-community/Ministral-3-8B-Reasoning-2512-4bit` on 127.0.0.1:
8765; 14 requests — two per run, four runs, four verdicts, and the
page's run, which records its own because a tool's name differs by
entry, below), replayed by `crow test --fixtures` in both modes with no
model. What the recording taught: its writes are recorded and not made,
so the database is seeded before it starts, and a read statement is
answered by the database once, its first answer kept for every later
read of the same text whatever the parameters (`rehearsal::read_real`)
— a tool asking `WHERE id = ?` would be told one order's row for every
id, and the model's second request, which carries the tool's result,
would never match in the replay. So the tools read the orders whole
(`store.orders[c]`, five rows) and pick the row in Crow. A fixture keyed
by the statement and its parameters would lift that; a question for the
recording, noted for Vijay.

**The page asks the agent.** `agent/chat.co`: a question in a field, a
button, the handler calling `support.run` itself — `act` is an effect,
so the handler runs off the page's loop while the model answers and the
button shows it is working — the answer and the tools called shown, and
the run's steps under them through the trace page's own view
(`trace.steps_view`, over the fact `trace.run_id` the handler sets).
Considered: `later[f]` for the run (rejected: a page's `later` carries
no arguments, the handler's commit holds the answer either way, and a
chat has no other click to let through while it waits). Tested as the
other apps' pages are: `app[render]`, `change`, `click`, the facts and
the derivations read back, the markup searched, interpreted and against
the compiled app, the model's replies replayed.

**What the build found in the compiler** (three bugs, reported by the
stream that built the example — held to the freeze — and fixed the same
day as the bug fixes they are, each with its test: parity 278,
tests/described.rs, the checker's snapshot round trip; the paragraph
keeps what was found). A value handed to a parameter that becomes a
value that can fail is handed bare, and the compiled program does not
build: `act`'s `system` is reassigned from the skills' prompt, a value
that can fail, so its parameter is typed as one, and a call that hands it
a local or a function's answer meets rustc's `E0308` — only a literal is
wrapped, which is why the default `''` and the suites' `system = 'be
brief'` build. The interpreter widens and never notices. The example
writes the prompt as a literal in the call (`pa_wrap/`). A function's
name as `describe` names it — the name a model is told, calls back and
the steps frame records — is the name as written when its part is the
entry (`order_status`, the evals) and the part-qualified internal name
when an entry imports the part (`agent_support_order_status`, the pages:
`lower::shown` strips a standard module's prefix, not a part's); the page
tests read the name by its end, and the pages' run records fixtures of
its own (`tool_name/`). Both modes agree in each case, so neither is a
parity bug; both are the compiler's. And one of the checker's: the
snapshot store keeps a function's edge-case answers one per line, so a
function answering text with a line break — the judge's prompt, four
lines — is read back as its first line and reported changed by every
check after the first (27 findings); the example builds the prompt inside
`graded`, which the checker probes with no edge cases, since its first
parameter is a model (`snap_lines/`).

**What holds it.** `crow test agent/store.co` (the seed), `crow test
agent/judge.co` (the verdict), `crow test --fixtures agent/support.co`
(the evals), `--fixtures agent/trace.co` and `agent/chat.co` (the pages,
and `--fixtures --compiled` for each), in raven's `./test.sh`.

## What was fixed in the compiler

A function called by name — `apply`'s, or a group's member where the
group was handed — is noted as called with its parameters' kinds in the
scan, so inference types it and the compiled program keeps it
(`note_called_by_name`, phase 4). The note went through `note_call`,
whose scope rule makes a program's functions invisible inside a standard
module's inlined function (a module local named like one is indexed, not
called): `act`'s dispatch over `tools = [weather]` noted nothing, `weather`
stayed `city: ?`, and the compiled program had a call of a function it
did not hold ("cannot find function `weather`"). The note records the
signature itself now (`note_named_call`, past the scope rule, since a
group's names were resolved where the literal was written — 2026-11-07:
it types the parameters and keeps no signature, which a later call could
not replace: a description above a function's call had made a copy at a
parsed value, crow's parity 476), and the
dispatch's call of the function is lowered as the program's call it is
(`applying`: the interpreter had met a by-name call of a function it does
not run). A bug fix under rule 24; parity 274 reproduces the first (the
compiled program had not built), tests/llm_server.rs the second.

Found by the loop's servers: a program's top-level value named with a
standard module's prefix (`mcp_s = server[…]`) was printed as a Rust
constant by the emitter's rule for a user module's hoisted values
(`mymath_pi`), and the compiled program did not build; a standard module
hoists no value (the inliner carries its functions and records alone), so
its prefix names none now (`emit_module_consts`). Found by the first run
against a real model: a function copied per signature was scanned
without entering its own scope (`scan_clone` bound the parameters alone),
so its first assignment to a name was not a local of its own and the
program's top-level names were visible inside a standard module's copy —
the copy of `act` made for a second tool list read the program's `one`,
the record the first call answered, as the loop's local `one` and refused
its own push; the copy's scan enters the scope as the function's own does
(parity 275). And two shapes a module
written in Crow needs: a list parameter with a `[]` default makes a copy of
the function whose items are of no kind, so a helper that takes the list
typed by another copy does not build — `act` reads its servers as their
JSON (`json[encode[servers]]`), the same in every copy; and an import's
alias is not resolved inside a module (`use mcp as peer` left `peer`
undefined), while a bare `use mcp` reserves the exports' names from the
module's parameters, which is why the listing is `catalog` and not
`tools`.

## What holds it

- The package's own tests (`crow test packages/llm/main.co`, interpreted
  and compiled): a call answered by the tool of its name — the value as
  the model reads it, a missing argument, a tool's own failure, an
  unknown name; a failed result told as one in each wire format; the
  loop's limits and a model nobody answers.
- `tests/model.py` (crow's `tests/llm_server.rs` until the move): the
  stand-in server scripts multi-step runs by the question asked — a tool
  called once then answered, a model that keeps calling stopped by
  `rounds` and by `budget`, an unknown name, a wrong argument, a tool
  that fails, two calls in one reply, a reply cut by its length — in both
  wire formats, the model's final text saying back what it was told (a
  failed result marked where the wire carries the flag), and the
  program's lines the same from both modes; the delegating runs (`act:
  delegate`, `act: locked`) and a stand-in MCP endpoint in the loop.
- `tests/mcp.py` (crow's `tests/mcp.rs`): both directions over HTTP, and
  a Crow client against a Crow server over stdio, each side interpreted
  and compiled.
- Parity 274 (the loop without a model: its refusals, the failed
  result's turn, both modes), 275–277 and 279, the book's pages, and the
  agent example's tests (`examples/agent`).
- Until the move, crow's `crow check --self` ran the loop against its own
  model (the llm family's live cases `act` and `anthropic-act`); a run
  against a local model (mlx-lm serving Ministral 3 8B), interpreted and
  compiled, answered Lisbon's weather and population from what the
  subagent `research` found.

## The pieces

- `packages/llm/main.co` — `act` with `_answered`, `_outcome`,
  `_plain_of`, `_trimmed`, `_compact_ask`/`_compacted`, the skills
  helpers (`_skill_read`, `_skill_name_fault`, `_skills_in`,
  `_skills_prompt`, `_skill_schemas`, `_skill_loaded`,
  `_skill_file_read`), the servers' (`_served_tools`, `_served_call`,
  `_is_served`), `_result_anthropic`, `told`'s `failed`; the record
  `Done`; `use frame`, `use mcp` (the package, linked under
  `packages/llm/crow_modules/mcp`).
- `packages/mcp/main.co` — `server`, `discover`, `catalog`, `tool`,
  `answer`, `listen` and their helpers (the stdio transport:
  `_process_of`, `_line_of`, `_next_id`, `_sent_over_stdio`; the shared
  `_answered` and its record `Answered`).
- crow — `sys.start` (a process as a socket: `ProcessConn`, the
  processes kept by command, in `crow_runtime/src/sys.rs`), `describe`
  and `apply` (`src/lower_ir/named.rs`), and the fixes above, none of
  which names llm or mcp.
- Tests: `tests/model.py`, `tests/mcp.py`, the parity programs, the
  refusals, the packages' own tests, the examples.
