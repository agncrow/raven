# Tools from other programs: `use mcp`

The Model Context Protocol is how a harness gets tools from another
program: a server offers tools, each a name, a description and an input
schema, and a client lists and calls them over one HTTP endpoint, or over
the standard streams of a process it starts. `server` names an endpoint,
or by name the command that starts one (below); what it answers is a
description the program can read:

```crow
use mcp

s = mcp.server['http://127.0.0.1:1/mcp']
log[s.url]
log[s.token]
t = mcp.server['https://tools.example.com/mcp', token = 'secret']
log[t.token]
bad = mcp.server['localhost:8000/mcp']
log[bad.ok]
log[bad.reason]
```

```text
http://127.0.0.1:1/mcp

secret
false
an MCP server is reached at its endpoint's URL: localhost:8000/mcp is none — http://localhost:8000/mcp — or started as a command: server[command = 'npx -y @modelcontextprotocol/server-everything']
```

`discover` asks a server what it is and supports, `catalog` lists its tools
and `tool` calls one by name with its arguments, a map or a parsed value,
answering the text its result carries. A server nobody answers is the
call's error value, of kind `mcp`, and so is a result the server marks an
error:

```crow
use mcp

s = mcp.server['http://127.0.0.1:1/mcp']
d = mcp.discover[s]
log[d.ok]
log[d.kind]
ts = mcp.catalog[s]
log[ts.ok]
r = mcp.tool[s, 'weather', map[city = 'Lisbon']]
log[r.ok]
log[starts[r.reason, 'cannot reach the MCP server']]
```

```text
false
mcp
false
false
true
```

A server named by a command runs over its standard streams — the stdio
transport: the process starts at the first message and stays while the
thread that started it lives, each request one line on its input, the response the
line on its output carrying the request's id. The same `discover`,
`catalog` and `tool` serve it. `/bin/cat` echoes the request, which is a
message with no result; a command the shell does not find ends before
answering (and is started again at the next message); a shell loop that
answers by method is a server:

```crow
use mcp

cat = mcp.server[command = '/bin/cat']
log[cat.command]
log[cat.url]
got = mcp.catalog[cat]
log[got.ok]
log[got.reason]
gone = mcp.server[command = 'no-such-mcp-server']
log[mcp.catalog[gone].reason]
log[mcp.server[command = '/bin/cat', token = 't'].reason]
loop = mcp.server[command = 'while read -r line; do id=${line#*\"id\":}; id=${id# }; id=${id%%,*}; case $line in *tools/list*) echo "{\"jsonrpc\": \"2.0\", \"id\": $id, \"result\": {\"tools\": [{\"name\": \"echo\", \"description\": \"says its words back\", \"inputSchema\": {\"type\": \"object\", \"properties\": {\"words\": {\"type\": \"string\"}}, \"required\": [\"words\"]}}]}}";; *tools/call*) w=${line#*\"words\":}; w=${w# }; w=${w#\"}; w=${w%%\"*}; echo "{\"jsonrpc\": \"2.0\", \"id\": $id, \"result\": {\"content\": [{\"type\": \"text\", \"text\": \"echo: $w\"}], \"isError\": false}}";; *) echo "{\"jsonrpc\": \"2.0\", \"id\": $id, \"error\": {\"code\": -32601, \"message\": \"Method not found\"}}";; esac; done']
log[mcp.catalog[loop][0].name]
log[mcp.tool[loop, 'echo', map[words = 'hi there']]]
log[mcp.discover[loop].reason]
```

```text
/bin/cat

false
the MCP server answered no result
cannot reach the MCP server no-such-mcp-server: the process ended
a bearer token goes to an endpoint's URL: a server started as a command takes none
echo
echo: hi there
the MCP server answered an error -32601: Method not found
```

A Crow program becomes an MCP server with one route: `answer` reads the
request a POST route was handed and answers `server/discover`,
`tools/list` and `tools/call` over the functions themselves — each tool's
schema from the comment above it and the kinds its parameters take, each
call applied by name, a failure marked an error the model can read:

```crow,norun
use web, mcp

--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`trim[city]`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

fn served[req]
    return mcp.answer[req, tools = [weather], name = 'weather-server']

route['/mcp', 'POST', served]
serve[8000]
```

The same program serves over its standard streams with `listen` in place
of the route — a client starts it as a command, `server[command = 'crow
run weather.co']`, and reads its answers line by line; it ends when its
input does:

```crow,norun
use mcp

--- the current weather in a city, in °C
fn weather[city]
    r = request['https://wttr.in/`trim[city]`?format=j1']
    return str[json[r.body].current_condition[0].temp_C]

mcp.listen[tools = [weather], name = 'weather-server']
```

An agent takes a server's tools beside the program's own: `act[m, talk,
tools = [f], servers = [s]]` (`use llm`) adds every tool `s` offers, each
called on the server, and a name a server's tool shares with another tool
is refused before any model is asked.

`tests/mcp.py` drives a Crow client against a stand-in server, a stand-in
client against a Crow server like the first one, and a Crow client against
a Crow program serving over stdio like the second, in both modes.

Reference: [packages/mcp/REFERENCE.md](../../../packages/mcp/REFERENCE.md).
