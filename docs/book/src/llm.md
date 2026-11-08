# A language model as a connection: `use llm`

`reach` makes a model a connection — `anthropic://<model>`, or
`openai://<host>/<model>` for any server speaking the OpenAI chat
completions (Ollama, vLLM, OpenRouter, LiteLLM). The key is read from the
environment when a call is made; a local host needs none. What `reach`
answers is a description the program can read:

```crow
use llm

m = llm.reach['openai://localhost:11434/llama3.2']
log[m.provider]
log[m.model]
log[m.base]
log[m.key]
bad = llm.reach['ftp://somewhere']
log[bad.ok]
log[bad.reason]
```

```text
openai
llama3.2
http://localhost:11434
OPENAI_API_KEY
false
cannot reach 'ftp://somewhere': a model's URL is anthropic://<model> or openai://<host>/<model>
```

A conversation is a parsed value, a list of turns, built a turn at a
time: `ask` begins one with the user's turn, `asked` adds the user's next,
`said` the model's reply, `told` a tool's result.
A reply is a parsed value too — its `text`, its `calls`, its `usage` and
its `stop` — so the builders take what `chat` answers:

```crow
use llm

talk = llm.ask['What is the capital of Mongolia?']
log[len[talk]]
log[talk[0].role]
talk = llm.asked[talk, 'And its population?']
log[len[talk]]
r = json['{"text": "Let me check.", "calls": [{"id": "c1", "name": "lookup", "args": {"city": "Ulaanbaatar"}}], "usage": {"input": 12, "output": 7}, "stop": "tool"}']
talk = llm.said[talk, r]
log[talk[2].role]
log[talk[2].calls[0].name]
talk = llm.told[talk, r.calls[0], '1.6 million']
log[talk[3].role]
log[talk[3].id]
```

```text
1
user
2
assistant
lookup
tool
c1
```

`chat` asks the model; a server nobody answers is the call's error value,
of kind `llm`, and so is a stream that cannot be opened (`follow`), whose
pieces (`next`) and whole (`reply`) then fail with it. `embed` answers a
matrix of embeddings from an OpenAI-compatible endpoint, which Anthropic
does not serve:

```crow
use llm

m = llm.reach['openai://127.0.0.1:1/llama3.2']
talk = llm.ask['Hello?']
r = llm.chat[m, talk, system = 'Answer in one word.', tokens = 16]
log[r.ok]
log[r.kind]
s = llm.follow[m, talk]
log[s.ok]
piece = llm.next[s]
log[piece.ok]
whole = llm.reply[s]
log[whole.ok]
e = llm.embed[m, ['one', 'two']]
log[e.ok]
a = llm.reach['anthropic://claude-sonnet-5-5?key=CROW_BOOK_NO_SUCH_KEY']
log[a.reason]
```

```text
false
llm
false
false
false
false
cannot reach 'anthropic://claude-sonnet-5-5?key=CROW_BOOK_NO_SUCH_KEY': the key's variable CROW_BOOK_NO_SUCH_KEY is not set
```

Tools are the functions themselves: `tools = [weather]` hands the function,
and the package writes its schema from the function's description — the
name, the parameters with the kinds inference found (what the program's
calls hand them, or what the body reads them as), and the comment above
the function as the description. The program dispatches the model's calls
and hands each result back with `told`:

```crow
use llm

--- the current weather in a city, in °C
fn weather[city]
    return 'sunny in `upper[city]`'

m = llm.reach['openai://127.0.0.1:1/llama3.2']
talk = llm.ask['How warm is it in Lisbon right now?']
r = llm.chat[m, talk, tools = [weather]]
while r.ok and r.stop eq 'tool'
    talk = llm.said[talk, r]
    for call in r.calls
        if call.name eq 'weather'
            talk = llm.told[talk, call, weather[call.args.city]]
    r = llm.chat[m, talk, tools = [weather]]
log[r.ok]
log[weather['Lisbon']]
```

```text
false
sunny in LISBON
```

`act` is that loop, written once in the package: the model runs its tools
until it answers or a limit stops it. Each reply's calls are answered by
the tools' own functions, every call answered — a name no tool has, an
argument of the wrong kind and a tool's own failure are each told to the
model as a failed result that says why — and the run comes back as a
record: `text`, `stop` (`end`, `length`, `other`, `rounds`, `budget`),
`rounds`, `usage`, `talk` and `steps`, a frame with a row per model call
and per tool call. `allow = [g]` names the program's gates, functions of
the call asked before a tool runs: a call one refuses is told to the
model as a failed result rather than run, and a function that is no gate
— one that takes another count of parameters or answers another kind —
is the call's error. `told[…, failed = true]` is how a failure is told by
hand; the loop is held to its limits before any model is asked:

```crow
use llm

--- the current weather in a city
fn weather[city]
    return 'sunny in `upper[city]`'

--- the weather may be asked for Lisbon alone
fn only_lisbon[call]
    if str[call.name] ne 'weather'
        return true
    return str[call.args.city] eq 'Lisbon'

m = llm.reach['openai://127.0.0.1:1/llama3.2']
done = llm.act[m, llm.ask['Is it warmer in Lisbon or Porto?'], tools = [weather], allow = [only_lisbon], rounds = 4]
log[done.ok]
log[done.kind]
none = llm.act[m, llm.ask['hi'], tools = [weather], rounds = 0]
log[none.reason]
ungated = llm.act[m, llm.ask['hi'], tools = [weather], allow = [weather]]
log[ungated.reason]
r = json['{"text": "", "calls": [{"id": "c1", "name": "weather", "args": {"city": "Nowhere"}}], "usage": {"input": 3, "output": 4}, "stop": "tool"}']
talk = llm.told[llm.said[llm.ask['hi'], r], r.calls[0], 'no station in Nowhere', failed = true]
log[talk[2].failed]
```

```text
false
llm
act[…] runs at least one round: rounds = 0
a gate takes the call and answers a bool: weather takes 1 and answers text
true
```

A subagent is a tool that runs the loop: a function the parent model
calls, whose body runs `act` with a model and tools of its own and
answers what the inner run found. A function sees no top-level name, so
it reaches its model itself; `trim[question]` reads the question as text
(`ask` takes any value and types nothing); and a run that failed fails
the tool, so the parent model hears a failed result that says why. The
parent's steps show the delegation as one tool row; the inner run's
steps are the tool's own to read:

```crow
use llm

--- the current weather in a city
fn weather[city]
    return 'sunny in `upper[city]`'

--- what a smaller agent finds out about a question, with the weather as
--- its only tool
fn research[question]
    small = llm.reach['openai://127.0.0.1:1/llama3.2']
    inner = llm.act[small, llm.ask[trim[question]], tools = [weather], rounds = 4]
    return inner.text

m = llm.reach['openai://127.0.0.1:1/llama3.2']
done = llm.act[m, llm.ask['Is it warm in Lisbon today?'], tools = [research]]
log[done.ok]
found = research['Is it warm in Lisbon today?']
log[found.ok]
log[found.kind]
```

```text
false
false
llm
```

The run's context and know-how are options too. `keep = n` cuts the tool
results of rounds older than the last n to a stub naming their size,
`compact = n` asks the model for a summary once a reply has read n tokens
and goes on from the summary alone, and `budget = n` stops the run once
it has spent n tokens, after the reply's calls are answered. `skills =
folder` reads a folder of skills in the Agent Skills format — each a
folder holding a `SKILL.md`: a frontmatter with its `name` (lowercase
letters, digits and hyphens, the folder's own name) and its
`description`, then the instructions — names each in the system prompt,
and lets the model load one, and the files it refers to, through the
tools `skill` and `skill_file`. `servers = [s]` adds the tools MCP
servers offer (`use mcp`), each called on its server. A skill that breaks
the format's rules is the call's error, before any model is asked:

```crow
use llm

mkdir['ex_skills/refunds']
write['ex_skills/refunds/SKILL.md', '---\nname: Refunds\ndescription: How the shop decides a refund, and what to ask first.\n---\nAsk for the order number, then read its status.\n']
m = llm.reach['openai://127.0.0.1:1/llama3.2']
r = llm.act[m, llm.ask['May I have a refund?'], skills = 'ex_skills', keep = 2, compact = 8000, budget = 20000]
log[r.reason]
rm['ex_skills']
```

```text
the skill refunds's name 'Refunds' does not match its folder refunds
```

A whole agent — three tools over a shop's orders in SQLite, the refund
behind a gate, every run's `steps` frame written to the database as a
trace beside a row per run, a page over the traces with their tokens and
latency charted, evals as `test` blocks replayed from recorded fixtures
with a model as the judge, and a page that asks the agent — is
`examples/agent` in this repository: from its folder, `crow run
main.co` serves it, and `crow test --fixtures agent/support.co` runs its
evals with no model (docs/agents-design.md, Traces, evals and a page).

Reference: [packages/llm/REFERENCE.md](../../../packages/llm/REFERENCE.md).
