#!/usr/bin/env python3
"""The mcp package in both modes, over the Model Context Protocol's
revision 2026-07-28. A Crow client driven against a stand-in server of
this script's own: `server/discover`, `tools/list` answered as a stream of
events (a progress notification, then the response), `tools/call` (a
result, one marked an error, an unknown tool), each request's headers held
to its body, a server nobody answers and a URL that is none. A Crow program
serving `/mcp` (`mcp.answer` in a route) driven by a stand-in client:
discovery, the tools listed from the functions themselves, a call
answered, a wrong argument and a tool's failure marked errors, an unknown
tool, an unknown method, a notification, a body that is no JSON, a request
missing its headers or `_meta`, one of another protocol version, a header
that names another tool, an object handed to a tool typed by the fields
its body reads. And the stdio transport: a Crow client against a Crow
program serving over its standard streams (`mcp.listen`), the server
started as a command by the client, interpreted and compiled on each side.

Each program runs in a directory of its own with this repository's
packages linked as crow_modules/llm and crow_modules/mcp, as `crow install`
would link them, and answers the same from `crow run -i` and from the
compiled program. The three groups run at once; a served program runs in a
process group of its own, ended with the program `crow run` built.
Standard library only.

Usage: python3 tests/mcp.py [path to crow]   (exits 1 on any failure)"""
import concurrent.futures
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGES = os.path.join(os.path.dirname(HERE), 'packages')
CROW = sys.argv[1] if len(sys.argv) > 1 else 'crow'
# (absolute: a program runs in its own directory, and a server started as
# a command runs crow from another)
CROW_PATH = os.path.abspath(shutil.which(CROW) or CROW)
failures = []
printing = threading.Lock()
TOP = tempfile.mkdtemp(prefix='raven-mcp-')


def fresh(name):
    d = tempfile.mkdtemp(prefix=f'{name}-', dir=TOP)
    os.makedirs(os.path.join(d, 'crow_modules'))
    for package in ('llm', 'mcp'):
        os.symlink(os.path.join(PACKAGES, package), os.path.join(d, 'crow_modules', package))
    return d


def write(d, name, text):
    with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
        f.write(text)


def mode(interpreted):
    return 'interpreted' if interpreted else 'compiled'


def args_of(interpreted, file):
    return [CROW_PATH, 'run'] + (['-i'] if interpreted else []) + [file]


def end_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(d, interpreted, file, args=()):
    """A program run to its end in a process group of its own (the program
    `crow run` built is in it, and goes with it): its output and the end
    of its errors."""
    e = dict(os.environ, CROW_BUILD_PROFILE='dev')
    p = subprocess.Popen(args_of(interpreted, file) + list(args), cwd=d, env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(timeout=1800)
    except subprocess.TimeoutExpired:
        end_group(p)
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = b'', b''
        err += b'\n(ended: past the time limit)'
    end_group(p)
    return out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


def check(name, ok, detail=''):
    with printing:
        if ok:
            print(f'  ok    {name}', flush=True)
        else:
            failures.append(name)
            print(f'  FAIL  {name}\n        {detail}', flush=True)


def first_difference(got, want, what='line'):
    for i in range(max(len(got), len(want))):
        a = got[i] if i < len(got) else None
        b = want[i] if i < len(want) else None
        if a != b:
            return f'{what} {i + 1}: {"nothing" if a is None else repr(a)}, wanted {"nothing" if b is None else repr(b)}'
    return ''


def printed(name, out, want, err):
    """A check that a run printed what was wanted: the first line that
    differs where it did not, and the end of what it said on stderr."""
    tail = '\n        '.join(err.strip().splitlines()[-6:])
    check(name, out == want, f'{first_difference(out.split(chr(10)), want.split(chr(10)))}\n        {tail}')


def guarded(name, f, *args):
    """A check's own failure (an exception in this script) is a failure."""
    try:
        f(*args)
    except Exception as e:
        check(name, False, f'{type(e).__name__}: {e}')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def dumps(v):
    """A JSON value as serde_json writes one: compact, its keys in order."""
    return json.dumps(v, separators=(',', ':'), sort_keys=True, ensure_ascii=False)


def field(v, key):
    return v.get(key) if isinstance(v, dict) else None


def text_of(v):
    return v if isinstance(v, str) else ''


def rust_lines(s):
    """A text's lines as Rust's `lines()` reads them."""
    parts = s.split('\n')
    if parts and parts[-1] == '':
        parts.pop()
    return [p[:-1] if p.endswith('\r') else p for p in parts]


def rust_debug(s):
    """A text as Rust's `{:?}` writes it."""
    out = '"'
    for c in s:
        if c in '"\\':
            out += '\\' + c
        elif c == '\n':
            out += '\\n'
        elif c == '\r':
            out += '\\r'
        elif c == '\t':
            out += '\\t'
        elif c == '\0':
            out += '\\0'
        elif ord(c) < 0x20 or ord(c) == 0x7f:
            out += '\\u{%x}' % ord(c)
        else:
            out += c
    return out + '"'


# ── the stand-in server ──────────────────────────────────────────────

class Server:
    """Every connection answered by `serve(conn)` on a thread of its own,
    the connection closed after."""

    def __init__(self, serve):
        self.serve = serve
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(128)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stopped = False
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self.one, args=(conn,), daemon=True).start()

    def one(self, conn):
        conn.settimeout(60)
        try:
            self.serve(conn)
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self):
        self.stopped = True
        self.sock.close()


def respond(conn, status, kind, body):
    sent = body.encode()
    conn.sendall(f'HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {len(sent)}\r\nConnection: close\r\n\r\n'.encode() + sent)


def read_request(conn):
    """A request read whole: its path, its headers (lower-cased names)
    and its body."""
    buf = b''
    while True:
        try:
            chunk = conn.recv(65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        end = buf.find(b'\r\n\r\n')
        if end >= 0:
            length = 0
            for line in rust_lines(buf[:end].decode('utf-8', 'replace')):
                if line.lower().startswith('content-length:'):
                    try:
                        length = int(line.split(':', 1)[1].strip())
                    except ValueError:
                        length = 0
                    break
            if len(buf) >= end + 4 + length:
                break
    end = buf.find(b'\r\n\r\n')
    if end < 0:
        end = len(buf)
    head = buf[:end].decode('utf-8', 'replace')
    body = buf[min(end + 4, len(buf)):].decode('utf-8', 'replace')
    lines = rust_lines(head)
    words = lines[0].split() if lines else []
    headers = []
    for line in lines[1:]:
        k, colon, v = line.partition(':')
        if colon:
            headers.append((k.strip().lower(), v.strip()))
    return (words[1] if len(words) > 1 else ''), headers, body


SERVER_INFO = {'io.modelcontextprotocol/serverInfo': {'name': 'stand-in', 'version': '1.0'}}


def stand_in(conn):
    """Every request answered by its method, as the revision says —
    `tools/list` as a stream of events (a progress notification, then the
    response), the rest as JSON; a request whose headers do not mirror its
    body, or whose `_meta` lacks the version or the capabilities,
    refused."""
    path, headers, body = read_request(conn)

    def header(name):
        return next((v for k, v in headers if k == name), '')

    if path != '/mcp':
        return respond(conn, '404 Not Found', 'application/json', '{"jsonrpc": "2.0", "id": null, "error": {"code": -32601, "message": "no such endpoint"}}')
    try:
        sent = json.loads(body)
    except ValueError:
        sent = None
    id_ = field(sent, 'id')
    method = text_of(field(sent, 'method'))
    if header('mcp-method') != method or header('mcp-protocol-version') != '2026-07-28':
        return respond(conn, '400 Bad Request', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32020, 'message': 'Header mismatch'}}))
    meta = field(field(sent, 'params'), '_meta')
    if field(meta, 'io.modelcontextprotocol/protocolVersion') != '2026-07-28' or field(meta, 'io.modelcontextprotocol/clientCapabilities') is None:
        return respond(conn, '400 Bad Request', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32602, 'message': 'Invalid params: _meta'}}))
    if method == 'server/discover':
        result = {'jsonrpc': '2.0', 'id': id_, 'result': {'resultType': 'complete', 'supportedVersions': ['2026-07-28'], 'capabilities': {'tools': {}}, '_meta': SERVER_INFO, 'instructions': 'echo things', 'ttlMs': 3600000, 'cacheScope': 'public'}}
        return respond(conn, '200 OK', 'application/json', dumps(result))
    if method == 'tools/list':
        tools = [
            {'name': 'echo', 'description': 'says its words back', 'inputSchema': {'type': 'object', 'properties': {'words': {'type': 'string'}}, 'required': ['words']}},
            {'name': 'fail', 'description': 'always fails', 'inputSchema': {'type': 'object', 'additionalProperties': False}},
        ]
        progress = {'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'progressToken': 't', 'progress': 1}}
        result = {'jsonrpc': '2.0', 'id': id_, 'result': {'resultType': 'complete', 'tools': tools, '_meta': SERVER_INFO, 'ttlMs': 60000, 'cacheScope': 'public'}}
        events = f'event: message\ndata: {dumps(progress)}\n\n: keep-alive\n\ndata: {dumps(result)}\n\n'
        return respond(conn, '200 OK', 'text/event-stream', events)
    if method == 'tools/call':
        params = field(sent, 'params')
        name = text_of(field(params, 'name'))
        if header('mcp-name') != name:
            return respond(conn, '400 Bad Request', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32020, 'message': 'Header mismatch: Mcp-Name'}}))
        if name == 'echo':
            words = text_of(field(field(params, 'arguments'), 'words'))
            result = {'resultType': 'complete', 'content': [{'type': 'text', 'text': f'echo: {words}'}], 'isError': False, '_meta': SERVER_INFO}
        elif name == 'fail':
            result = {'resultType': 'complete', 'content': [{'type': 'text', 'text': 'it failed, as it always does'}], 'isError': True, '_meta': SERVER_INFO}
        else:
            return respond(conn, '200 OK', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32602, 'message': f'Unknown tool: {name}'}}))
        return respond(conn, '200 OK', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'result': result}))
    return respond(conn, '404 Not Found', 'application/json', dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32601, 'message': 'Method not found'}}))


# ── a program reaches an MCP server ─────────────────────────────────

CLIENT = """use mcp

port = args[][0]
s = mcp.server['http://127.0.0.1:`port`/mcp']
log[s.url]
d = mcp.discover[s]
log[d.supportedVersions[0]]
log[d.capabilities.tools.ok]
log[d.instructions]
log[d['_meta']['io.modelcontextprotocol/serverInfo'].name]
ts = mcp.catalog[s]
log[len[ts]]
log[ts[0].name]
log[ts[0].description]
log[ts[0].inputSchema.required[0]]
log[mcp.tool[s, 'echo', map[words = 'hi there']]]
log[mcp.tool[s, 'echo', json['{"words": "from a parsed value"}']]]
failed = mcp.tool[s, 'fail', map[]]
log[failed.ok]
log[failed.kind]
log[failed.reason]
unknown = mcp.tool[s, 'nothing', map[]]
log[unknown.ok]
log[unknown.reason]
nowhere = mcp.server['http://127.0.0.1:1/mcp']
gone = mcp.catalog[nowhere]
log[gone.ok]
log[gone.kind]
log[mcp.server['localhost:8000/mcp'].reason]
"""

CLIENT_SAYS = """http://127.0.0.1:{port}/mcp
2026-07-28
true
echo things
stand-in
2
echo
says its words back
words
echo: hi there
echo: from a parsed value
false
mcp
it failed, as it always does
false
the MCP server answered an error -32602: Unknown tool: nothing
false
mcp
an MCP server is reached at its endpoint's URL: localhost:8000/mcp is none — http://localhost:8000/mcp — or started as a command: server[command = 'npx -y @modelcontextprotocol/server-everything']
"""


def reaches_a_server():
    server = Server(stand_in)
    d = fresh('client')
    try:
        write(d, 'main.co', CLIENT)
        want = CLIENT_SAYS.replace('{port}', str(server.port))
        for interpreted in (True, False):
            out, err = run(d, interpreted, 'main.co', [str(server.port)])
            printed(f'a program reaches an MCP server ({mode(interpreted)})', out, want, err)
    finally:
        server.stop()
        shutil.rmtree(d, ignore_errors=True)


# ── a program serves MCP ────────────────────────────────────────────

# Three tools — one of which fails for one argument, and one taking an
# object, typed by the fields its body reads — answered over `/mcp`.
SERVER = """use web, mcp

--- the current weather in a city
fn weather[city]
    if city eq 'Nowhere'
        return error['no station in `city`', 'weather']
    return 'sunny in `upper[city]`'

--- the forecast for a city, for a number of days
fn forecast[city, days = 2]
    return '`days` days of sun in `upper[city]`'

--- ships an order: its id, and its lines, each a sku and a count
fn ship[order]
    lines = 0
    for line in order.lines
        lines = lines + 1
    return 'shipped order `order.id` with `lines` lines'

fn served[req]
    return mcp.answer[req, tools = [weather, forecast, ship], name = 'weather-server', instructions = 'ask about the weather']

route['/mcp', 'POST', served]
serve[PORT]
"""


def post(port, body, headers):
    """One POST, with the headers the revision requires unless told to
    leave them out: the status line and the body."""
    s = socket.create_connection(('127.0.0.1', port), timeout=120)
    extra = ''.join(f'{k}: {v}\r\n' for k, v in headers)
    sent = body.encode()
    s.sendall(f'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\n{extra}Content-Length: {len(sent)}\r\nConnection: close\r\n\r\n'.encode() + sent)
    raw = b''
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        raw += chunk
    s.close()
    text = raw.decode('utf-8', 'replace')
    head, gap, rest = text.partition('\r\n\r\n')
    if not gap:
        head, rest = text, ''
    lines = rust_lines(head)
    return (lines[0] if lines else ''), rest


def modern(method, params, id_):
    params = dict(params, _meta={'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientInfo': {'name': 'stand-in', 'version': '1.0'}, 'io.modelcontextprotocol/clientCapabilities': {}})
    return dumps({'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params})


VERSION = ('MCP-Protocol-Version', '2026-07-28')
STEPS = [
    (modern('server/discover', {}, 1), [VERSION, ('Mcp-Method', 'server/discover')]),
    (modern('tools/list', {}, 2), [VERSION, ('Mcp-Method', 'tools/list')]),
    (modern('tools/call', {'name': 'weather', 'arguments': {'city': 'Lisbon'}}, 3), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'weather')]),
    (modern('tools/call', {'name': 'forecast', 'arguments': {'city': 'Porto', 'days': 'many'}}, 4), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'forecast')]),
    (modern('tools/call', {'name': 'weather', 'arguments': {'city': 'Nowhere'}}, 5), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'weather')]),
    (modern('tools/call', {'name': 'nothing', 'arguments': {}}, 'six'), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'nothing')]),
    (modern('resources/list', {}, 7), [VERSION, ('Mcp-Method', 'resources/list')]),
    (dumps({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 3}}), [VERSION, ('Mcp-Method', 'notifications/cancelled')]),
    ('this is not json', [VERSION, ('Mcp-Method', 'tools/list')]),
    (modern('tools/list', {}, 9), []),
    (dumps({'jsonrpc': '2.0', 'id': 10, 'method': 'tools/list', 'params': {}}), [VERSION, ('Mcp-Method', 'tools/list')]),
    (modern('tools/list', {}, 11).replace('2026-07-28', '2025-11-25'), [('MCP-Protocol-Version', '2025-11-25'), ('Mcp-Method', 'tools/list')]),
    (modern('tools/call', {'name': 'weather', 'arguments': {'city': 'Lisbon'}}, 12), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'forecast')]),
    (modern('tools/call', {'name': 'ship', 'arguments': {'order': {'id': 7, 'lines': [{'sku': 'A1', 'count': 2}, {'sku': 'B2', 'count': 1}]}}}, 13), [VERSION, ('Mcp-Method', 'tools/call'), ('Mcp-Name', 'ship')]),
]

# Each answer: the status line, and the body read back as JSON (or the
# text, as Rust's `{:?}` writes it, where there is none).
SERVED = [
    r'HTTP/1.1 200 OK | {"id":1,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"cacheScope":"public","capabilities":{"tools":{}},"instructions":"ask about the weather","resultType":"complete","supportedVersions":["2026-07-28"],"ttlMs":3600000}}',
    r'HTTP/1.1 200 OK | {"id":2,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"cacheScope":"public","resultType":"complete","tools":[{"description":"the current weather in a city","inputSchema":{"properties":{"city":{"type":"string"}},"required":["city"],"type":"object"},"name":"weather"},{"description":"the forecast for a city, for a number of days","inputSchema":{"properties":{"city":{"type":"string"},"days":{"type":"integer"}},"required":["city","days"],"type":"object"},"name":"forecast"},{"description":"ships an order: its id, and its lines, each a sku and a count","inputSchema":{"properties":{"order":{"type":"object"}},"required":["order"],"type":"object"},"name":"ship"}],"ttlMs":60000}}',
    r'HTTP/1.1 200 OK | {"id":3,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"content":[{"text":"sunny in LISBON","type":"text"}],"isError":false,"resultType":"complete"}}',
    r'''HTTP/1.1 200 OK | {"id":4,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"content":[{"text":"a parsed value holds text 'many' where the program reads an int (forecast[]'s 'days')","type":"text"}],"isError":true,"resultType":"complete"}}''',
    r'HTTP/1.1 200 OK | {"id":5,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"content":[{"text":"no station in Nowhere","type":"text"}],"isError":true,"resultType":"complete"}}',
    r'HTTP/1.1 200 OK | {"error":{"code":-32602,"message":"Unknown tool: nothing"},"id":"six","jsonrpc":"2.0"}',
    r'HTTP/1.1 404 Not Found | {"error":{"code":-32601,"message":"Method not found: resources/list"},"id":7,"jsonrpc":"2.0"}',
    r'HTTP/1.1 202 Accepted | ""',
    r'HTTP/1.1 400 Bad Request | {"error":{"code":-32700,"message":"the body is no JSON"},"id":null,"jsonrpc":"2.0"}',
    r'HTTP/1.1 400 Bad Request | {"error":{"code":-32020,"message":"Header mismatch: MCP-Protocol-Version and Mcp-Method must match the body"},"id":9,"jsonrpc":"2.0"}',
    r'''HTTP/1.1 400 Bad Request | {"error":{"code":-32602,"message":"a request carries its protocol version and the client's capabilities in _meta"},"id":10,"jsonrpc":"2.0"}''',
    r'HTTP/1.1 400 Bad Request | {"error":{"code":-32022,"data":{"requested":"2025-11-25","supported":["2026-07-28"]},"message":"Unsupported protocol version"},"id":11,"jsonrpc":"2.0"}',
    r'HTTP/1.1 400 Bad Request | {"error":{"code":-32020,"message":"Header mismatch: Mcp-Name must match the tool called"},"id":12,"jsonrpc":"2.0"}',
    r'HTTP/1.1 200 OK | {"id":13,"jsonrpc":"2.0","result":{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"weather-server","version":"0.7.0"}},"content":[{"text":"shipped order 7 with 2 lines","type":"text"}],"isError":false,"resultType":"complete"}}',
]


def listening(port):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=1).close()
        return True
    except OSError:
        return False


def serves(interpreted):
    """The served program in one mode, asked each step by the stand-in
    client."""
    name = f'a program serves MCP ({mode(interpreted)})'
    port = free_port()
    d = fresh('served')
    log_path = os.path.join(d, 'stderr.log')
    write(d, 'main.co', SERVER.replace('PORT', str(port)))
    # (its own process group: the compiled program is a process crow
    # starts, and ending crow alone had left it listening)
    with open(log_path, 'w') as errs:
        p = subprocess.Popen(args_of(interpreted, 'main.co'), cwd=d, env=dict(os.environ, CROW_BUILD_PROFILE='dev'), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errs, start_new_session=True)
    try:
        # (the compiled program builds first: wait for the port)
        end = time.time() + 1800
        while not listening(port):
            if p.poll() is not None or time.time() > end:
                with open(log_path, encoding='utf-8', errors='replace') as f:
                    said = ' | '.join(f.read().strip().splitlines()[-6:])
                return check(name, False, f'it did not serve on {port}: {said}')
            time.sleep(0.2)
        answers = []
        for body, headers in STEPS:
            status, got = post(port, body, headers)
            try:
                shown = dumps(json.loads(got))
            except ValueError:
                shown = rust_debug(got)
            answers.append(f'{status} | {shown}')
        check(name, answers == SERVED, first_difference(answers, SERVED, 'step'))
    finally:
        # the whole group — crow and the program it started — then the port
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        end_group(p)
        p.wait()
        gone = time.time() + 10
        while listening(port) and time.time() < gone:
            time.sleep(0.1)
        shutil.rmtree(d, ignore_errors=True)


# ── the stdio transport ─────────────────────────────────────────────

# The same three tools served over the program's standard streams: a
# client starts it as a command and talks to it line by line.
SERVER_STDIO = """use mcp

--- the current weather in a city
fn weather[city]
    if city eq 'Nowhere'
        return error['no station in `city`', 'weather']
    return 'sunny in `upper[city]`'

--- the forecast for a city, for a number of days
fn forecast[city, days = 2]
    return '`days` days of sun in `upper[city]`'

--- ships an order: its id, and its lines, each a sku and a count
fn ship[order]
    lines = 0
    for line in order.lines
        lines = lines + 1
    return 'shipped order `order.id` with `lines` lines'

mcp.listen[tools = [weather, forecast, ship], name = 'weather-server', instructions = 'ask about the weather']
"""

# The server's command is the client's first argument, and the calls are
# the HTTP client's: discovery, the list, a call with a map and with a
# parsed value, a wrong argument, a tool's failure, an unknown tool, an
# object handed to a tool.
CLIENT_STDIO = """use mcp

s = mcp.server[command = args[][0]]
log[s.url]
log[len[s.command] gt 0]
d = mcp.discover[s]
log[d.supportedVersions[0]]
log[d.instructions]
log[d['_meta']['io.modelcontextprotocol/serverInfo'].name]
ts = mcp.catalog[s]
log[len[ts]]
log[ts[0].name]
log[ts[0].description]
log[ts[2].inputSchema.properties.order.type]
log[mcp.tool[s, 'weather', map[city = 'Lisbon']]]
log[mcp.tool[s, 'forecast', json['{"city": "Porto", "days": 3}']]]
bad = mcp.tool[s, 'forecast', json['{"city": "Porto", "days": "many"}']]
log[bad.ok]
log[bad.kind]
log[bad.reason]
failed = mcp.tool[s, 'weather', map[city = 'Nowhere']]
log[failed.ok]
log[failed.reason]
unknown = mcp.tool[s, 'nothing', map[]]
log[unknown.reason]
log[mcp.tool[s, 'ship', json['{"order": {"id": 7, "lines": [{"sku": "A1", "count": 2}, {"sku": "B2", "count": 1}]}}']]]
"""

CLIENT_STDIO_SAYS = """
true
2026-07-28
ask about the weather
weather-server
3
weather
the current weather in a city
object
sunny in LISBON
3 days of sun in PORTO
false
mcp
a parsed value holds text 'many' where the program reads an int (forecast[]'s 'days')
false
no station in Nowhere
the MCP server answered an error -32602: Unknown tool: nothing
shipped order 7 with 2 lines
"""


def over_stdio():
    """Each client mode against each server mode, the server started as a
    command by the client: into its directory, then crow interpreting or
    compiling it (the build's own output goes to standard error, as the
    protocol wants). The compiled server builds while the interpreted pair
    talks, the compiled client while the interpreted client reaches the
    compiled server."""
    # (the server in a directory of its own, and a client's per mode: a
    # `.crow_build` is one program's)
    server = fresh('stdio-server')
    clients = {True: fresh('stdio-client'), False: fresh('stdio-client')}
    try:
        write(server, 'server.co', SERVER_STDIO)
        for d in clients.values():
            write(d, 'main.co', CLIENT_STDIO)

        def one(server_interpreted, client_interpreted):
            command = f"cd '{server}' && '{CROW_PATH}' {'run -i' if server_interpreted else 'run'} server.co"
            out, err = run(clients[client_interpreted], client_interpreted, 'main.co', [command])
            printed(f'the {mode(client_interpreted)} client reaches the {mode(server_interpreted)} server over stdio', out, CLIENT_STDIO_SAYS, err)

        # (the compiled server built before any client starts it, its input
        # already at its end, so it stops at once: a client waits ten minutes
        # at most for a server's first answer, which a cold build under a
        # suite's load can outlast — the Rust test's build directory
        # persisted between runs, and its builds were seldom cold)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            built = pool.submit(run, server, False, 'server.co')
            one(True, True)
            built.result()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            for job in [pool.submit(one, True, False), pool.submit(one, False, True)]:
                job.result()
        one(False, False)
    finally:
        shutil.rmtree(server, ignore_errors=True)
        for d in clients.values():
            shutil.rmtree(d, ignore_errors=True)


def sweep():
    """Whatever a program left running that names this run's directory (a
    server started as a command) ended, and the directory removed."""
    try:
        found = subprocess.run(['pgrep', '-f', TOP], capture_output=True, text=True).stdout.split()
    except OSError:
        found = []
    for pid in found:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    shutil.rmtree(TOP, ignore_errors=True)


def main():
    print(f'mcp: the mcp package from both sides, both modes ({CROW})\n', flush=True)
    start_at = time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            jobs = [
                pool.submit(guarded, 'a program reaches an MCP server', reaches_a_server),
                pool.submit(guarded, 'a program serves MCP (interpreted)', serves, True),
                pool.submit(guarded, 'a program serves MCP (compiled)', serves, False),
                pool.submit(guarded, 'a program reaches an MCP server over stdio', over_stdio),
            ]
            for job in jobs:
                job.result()
    finally:
        sweep()
    took = time.time() - start_at
    if failures:
        print(f'\n✗ {len(failures)} failed ({took:.1f}s)')
        sys.exit(1)
    print(f'\n✓ the mcp package answers alike in both modes ({took:.1f}s)')


if __name__ == '__main__':
    main()
