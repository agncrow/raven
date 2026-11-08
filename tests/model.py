#!/usr/bin/env python3
"""The llm package against stand-in models, in both modes. A program
reaching a model that speaks both wire formats — the OpenAI-compatible
chat completions and embeddings, and Anthropic's messages, each plain and
streamed as server-sent events over chunked transfer encoding, the chunks
of uneven widths written apart after short pauses, so an event straddles
the chunks and the reads: a reply, a tool call the compiler wrote the
schema for, the conversation continued with the tool's result, a reply
followed piece by piece, a streamed tool call gathered, embeddings, and
the refusals (a bad key, a body that is no JSON, a server nobody answers).
The agent loop (`llm.act`) against scripted runs: a tool called and
answered, a model that keeps calling stopped by `rounds` and by `budget`,
an unknown name, a wrong argument, a tool's own failure, two calls in one
reply, the program's gate, subagents, a reply cut by its length, a
conversation kept short and one compacted, skills, and the tools of an MCP
server over HTTP and of a Crow program serving over stdio (`mcp.listen`,
started as a command) — the same in Anthropic's format, where a failed
result reaches the wire as `is_error`. And the cases Crow's self-check held
the module to (its llm family): conversations built and read with no model,
`reach` and its refusals, and a model that says back what it was sent in
both wire formats, each case a program of its own and its answer the one
the self-check's reference gave.

Each program runs in a directory of its own with this repository's
packages linked as crow_modules/llm and crow_modules/mcp, as `crow install`
would link them, and prints the same lines from `crow run -i` and from the
compiled program. Programs run several at once (RAVEN_TEST_JOBS, 6: a
compiled run builds a Rust program in its own directory, minutes of a few
cores and most of a gigabyte of disk), each in a process group of its own,
ended with it. Standard library only.

Usage: python3 tests/model.py [path to crow]   (exits 1 on any failure)"""
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
JOBS = int(os.environ.get('RAVEN_TEST_JOBS', '6'))
failures = []
printing = threading.Lock()
TOP = tempfile.mkdtemp(prefix='raven-model-')


def fresh(name):
    d = tempfile.mkdtemp(prefix=f'{name}-', dir=TOP)
    os.makedirs(os.path.join(d, 'crow_modules'))
    for package in ('llm', 'mcp'):
        os.symlink(os.path.join(PACKAGES, package), os.path.join(d, 'crow_modules', package))
    return d


def write(d, name, text):
    path = os.path.join(d, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def mode(interpreted):
    return 'interpreted' if interpreted else 'compiled'


def end_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(d, interpreted, file, args=(), env=None):
    """A program run to its end in a process group of its own (the program
    `crow run` built is in it, and goes with it): its output and the end
    of its errors. `env` adds variables, and takes away those it gives
    None."""
    e = dict(os.environ, CROW_BUILD_PROFILE='dev')
    for k, v in (env or {}).items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    cmd = [CROW_PATH, 'run'] + (['-i'] if interpreted else []) + [file] + list(args)
    p = subprocess.Popen(cmd, cwd=d, env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(timeout=600 if interpreted else 1800)
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


def first_difference(got, want):
    g, w = got.split('\n'), want.split('\n')
    for i in range(max(len(g), len(w))):
        a = g[i] if i < len(g) else None
        b = w[i] if i < len(w) else None
        if a != b:
            return f'line {i + 1}: printed {"nothing" if a is None else repr(a)}, wanted {"nothing" if b is None else repr(b)}'
    return ''


def printed(name, out, want, err):
    """A check that a run printed what was wanted: the first line that
    differs where it did not, and the end of what it said on stderr."""
    tail = '\n        '.join(err.strip().splitlines()[-6:])
    check(name, out == want, f'{first_difference(out, want)}\n        {tail}')


def guarded(name, f, *args):
    """A check's own failure (an exception in this script) is a failure."""
    try:
        f(*args)
    except Exception as e:
        check(name, False, f'{type(e).__name__}: {e}')


# ── a stand-in on this machine's loopback ────────────────────────────

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


def dumps(v):
    """A JSON value as serde_json writes one: compact, its keys in order."""
    return json.dumps(v, separators=(',', ':'), sort_keys=True, ensure_ascii=False)


def field(v, key):
    return v.get(key) if isinstance(v, dict) else None


def items(v):
    return v if isinstance(v, list) else []


def text_of(v):
    return v if isinstance(v, str) else ''


def rust_lines(s):
    """A text's lines as Rust's `lines()` reads them."""
    parts = s.split('\n')
    if parts and parts[-1] == '':
        parts.pop()
    return [p[:-1] if p.endswith('\r') else p for p in parts]


def header(head, name):
    for line in rust_lines(head):
        k, colon, v = line.partition(':')
        if colon and k.strip().lower() == name:
            return v.strip()
    return None


def path_of(head):
    lines = rust_lines(head)
    words = lines[0].split() if lines else []
    return words[1] if len(words) > 1 else ''


def respond(conn, status, body):
    sent = body.encode()
    conn.sendall(f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(sent)}\r\nConnection: close\r\n\r\n'.encode() + sent)


def straddled(events, width):
    """The whole stream cut into pieces of widths `width` and `width + 3`
    by turns, each cut on a character's boundary: an event straddles the
    chunks (and a character of several bytes may too)."""
    rest = events.encode()
    pieces, w = [], width
    while rest:
        cut = min(w, len(rest))
        while cut < len(rest) and rest[cut] & 0xC0 == 0x80:
            cut -= 1
        pieces.append(rest[:cut])
        rest = rest[cut:]
        w = width + 3 if w == width else width
    return pieces


def respond_stream(conn, events, width, pause):
    """The events as a chunked body, each chunk written on its own after a
    pause: an event straddles the reads too, not only the chunks (a reader
    that let go of the half event it held, to read the rest, had lost the
    event; one write of the whole body had never shown it)."""
    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
    for piece in straddled(events, width):
        if pause:
            time.sleep(pause)
        conn.sendall(b'%x\r\n' % len(piece) + piece + b'\r\n')
    conn.sendall(b'0\r\n\r\n')


# ── the scripted model ───────────────────────────────────────────────
#
# One request answered by what it asked: the path names the format, the
# body says whether a reply is streamed and whether tools were offered (a
# tool call comes once, then its result is read back); a question
# beginning `act: ` names a scripted run of the agent loop.

def read_whole(conn):
    """A request read to its blank line and its Content-Length: its head
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
    return buf[:end].decode('utf-8', 'replace'), buf[min(end + 4, len(buf)):].decode('utf-8', 'replace')


def openai_stream(tool):
    if tool:
        lines = [
            r'{"choices": [{"delta": {"role": "assistant", "content": null, "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "weather", "arguments": ""}}]}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"ci"}}]}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ty\": \"Lisbon\"}"}}]}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}',
            r'{"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}}',
        ]
    else:
        lines = [
            r'{"choices": [{"delta": {"role": "assistant", "content": ""}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {"content": "Ulaan"}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {"content": "baatar"}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {"content": " é!"}, "finish_reason": null}]}',
            r'{"choices": [{"delta": {}, "finish_reason": "stop"}]}',
            r'{"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}',
        ]
    out = ''
    for i, line in enumerate(lines):
        out += f'data: {line}\n\n'
        if i == 1:
            out += ': keep-alive\n\n'
    return out + 'data: [DONE]\n\n'


def anthropic_stream(tool):
    if tool:
        events = [
            ('message_start', r'{"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1}}}'),
            ('content_block_start', r'{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}'),
            ('content_block_delta', r'{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me check."}}'),
            ('content_block_stop', r'{"type": "content_block_stop", "index": 0}'),
            ('content_block_start', r'{"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {}}}'),
            ('content_block_delta', r'{"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"city\": "}}'),
            ('content_block_delta', r'{"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"Lisbon\"}"}}'),
            ('content_block_stop', r'{"type": "content_block_stop", "index": 1}'),
            ('message_delta', r'{"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}}'),
            ('message_stop', r'{"type": "message_stop"}'),
        ]
    else:
        events = [
            ('message_start', r'{"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1}}}'),
            ('content_block_start', r'{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}'),
            ('ping', r'{"type": "ping"}'),
            ('content_block_delta', r'{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ulaan"}}'),
            ('content_block_delta', r'{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "baatar é!"}}'),
            ('content_block_stop', r'{"type": "content_block_stop", "index": 0}'),
            ('message_delta', r'{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}'),
            ('message_stop', r'{"type": "message_stop"}'),
        ]
    return ''.join(f'event: {e}\ndata: {d}\n\n' for e, d in events)


def reply_text(anthropic, text, stop):
    """A reply of text in the format the path names, with the stop reason
    the loop reads as `end` or `length`."""
    if anthropic:
        return dumps({'content': [{'type': 'text', 'text': text}], 'stop_reason': 'max_tokens' if stop == 'length' else 'end_turn', 'usage': {'input_tokens': 21, 'output_tokens': 3}})
    return dumps({'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'length' if stop == 'length' else 'stop'}], 'usage': {'prompt_tokens': 21, 'completion_tokens': 3}})


def reply_calls(anthropic, calls):
    """A reply of calls — one block or tool_call each, ids numbered — in
    the format the path names."""
    if anthropic:
        blocks = [{'type': 'tool_use', 'id': f'toolu_{i + 1}', 'name': n, 'input': a} for i, (n, a) in enumerate(calls)]
        return dumps({'content': blocks, 'stop_reason': 'tool_use', 'usage': {'input_tokens': 12, 'output_tokens': 15}})
    tcs = [{'id': f'call_{i + 1}', 'type': 'function', 'function': {'name': n, 'arguments': dumps(a)}} for i, (n, a) in enumerate(calls)]
    return dumps({'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': tcs}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}})


def told_results(messages, anthropic):
    """The tool results the last turn carries — each result's text and
    whether it was flagged an error (Anthropic's `is_error`; the
    OpenAI-compatible wire has no flag)."""
    if anthropic:
        if not messages or field(messages[-1], 'role') != 'user':
            return []
        return [(text_of(field(b, 'content')), field(b, 'is_error') is True) for b in items(field(messages[-1], 'content')) if field(b, 'type') == 'tool_result']
    out = []
    for m in reversed(messages):
        if field(m, 'role') != 'tool':
            break
        out.append((text_of(field(m, 'content')), False))
    return out[::-1]


def all_told(messages, anthropic):
    """Every tool result the conversation holds, in order, each with
    whether it was flagged an error."""
    out = []
    for m in messages:
        if anthropic:
            if field(m, 'role') == 'user':
                out += [(text_of(field(b, 'content')), field(b, 'is_error') is True) for b in items(field(m, 'content')) if field(b, 'type') == 'tool_result']
        elif field(m, 'role') == 'tool':
            out.append((text_of(field(m, 'content')), False))
    return out


def system_of(v, messages, anthropic):
    """The system prompt sent, in either format."""
    if anthropic:
        return text_of(field(v, 'system'))
    for m in messages:
        if field(m, 'role') == 'system':
            return text_of(field(m, 'content'))
    return ''


def scripted(path, body):
    """A scripted run for the agent loop: a question beginning `act: `
    names the scenario, the model's turns so far say which step this is,
    and the final text says back what the model was told (`heard: …`, each
    failed result marked `(error)` where the wire carries the flag), so the
    test sees what the model heard. None for any other question."""
    anthropic = path == '/v1/messages'
    if not anthropic and path != '/v1/chat/completions':
        return None
    try:
        v = json.loads(body)
    except ValueError:
        return None
    messages = items(field(v, 'messages'))

    def user_text(m):
        if anthropic:
            for x in items(field(m, 'content')):
                if field(x, 'type') == 'text':
                    return text_of(field(x, 'text'))
            return ''
        return text_of(field(m, 'content'))

    users = [m for m in messages if field(m, 'role') == 'user']
    if not users:
        return None
    question = user_text(users[0])
    last_user = user_text(users[-1])
    # (a compacted conversation starts from a summary; a summary is asked
    # with no tools and the question gone)
    if last_user.startswith('Summarize the conversation'):
        return reply_text(anthropic, 'the task: the weather (a summary)', 'end')
    if question.startswith('The conversation so far, summarized:'):
        lines = rust_lines(question)
        return reply_text(anthropic, f'heard: resumed from {lines[1] if len(lines) > 1 else ""}', 'end')
    if not question.startswith('act: '):
        return None
    scenario = question[len('act: '):]

    def shown(results):
        return ' | '.join(f'{t} (error)' if e else t for t, e in results)

    everything = f'seen: {shown(all_told(messages, anthropic))}'
    listed = ' | listed' if 'crow-parity-test: ' in system_of(v, messages, anthropic) else ''
    turn = sum(1 for m in messages if field(m, 'role') == 'assistant')
    heard = f'heard: {shown(told_results(messages, anthropic))}'
    lisbon = {'city': 'Lisbon'}
    a = anthropic
    if scenario == 'weather' and turn == 0:
        return reply_calls(a, [('weather', lisbon)])
    if scenario == 'forever':
        return reply_calls(a, [('weather', lisbon)])
    if scenario == 'nothing' and turn == 0:
        return reply_calls(a, [('nothing', {})])
    if scenario == 'bad' and turn == 0:
        return reply_calls(a, [('forecast', {'city': 'Lisbon', 'days': 'many'})])
    if scenario == 'fail' and turn == 0:
        return reply_calls(a, [('weather', {'city': 'Nowhere'})])
    if scenario == 'two' and turn == 0:
        return reply_calls(a, [('weather', lisbon), ('weather', {'city': 'Porto'})])
    if scenario == 'length':
        return reply_text(a, 'cut off', 'length')
    if scenario == 'keep' and turn < 3:
        return reply_calls(a, [('weather', lisbon)])
    if scenario == 'keep':
        return reply_text(a, everything, 'end')
    if scenario == 'compact' and turn == 0:
        return reply_calls(a, [('weather', lisbon)])
    if scenario == 'skills' and turn == 0:
        return reply_calls(a, [('skill', {'name': 'crow-parity-test'})])
    if scenario == 'skills' and turn == 1:
        return reply_calls(a, [('skill_file', {'name': 'crow-parity-test', 'path': 'references/parity.md'})])
    if scenario == 'skills' and turn == 2:
        return reply_calls(a, [('skill_file', {'name': 'crow-parity-test', 'path': '../../secrets.txt'}), ('skill', {'name': 'nothing'})])
    if scenario == 'skills':
        return reply_text(a, everything + listed, 'end')
    if scenario == 'mcp' and turn == 0:
        return reply_calls(a, [('echo', {'words': 'over mcp'}), ('fail', {})])
    if scenario == 'mcp':
        return reply_text(a, everything, 'end')
    # (a parent delegates: the tool `research` runs a loop of its own
    # against this same server, asking the `weather` scenario; the `locked`
    # researcher's model refuses its key, so its run fails and the parent
    # hears why)
    if scenario == 'delegate' and turn == 0:
        return reply_calls(a, [('research', {'question': 'act: weather'})])
    if scenario == 'locked' and turn == 0:
        return reply_calls(a, [('research_locked', {'question': 'anything'})])
    return reply_text(a, heard, 'end')


def mcp_answer(body):
    """A stand-in MCP server at /mcp: an `echo` tool and one that fails,
    listed and called by the loop's `servers = [s]`."""
    try:
        sent = json.loads(body)
    except ValueError:
        sent = None
    id_ = field(sent, 'id')
    method = text_of(field(sent, 'method'))
    if method == 'tools/list':
        return dumps({'jsonrpc': '2.0', 'id': id_, 'result': {'resultType': 'complete', 'tools': [
            {'name': 'echo', 'description': 'says its words back', 'inputSchema': {'type': 'object', 'properties': {'words': {'type': 'string'}}, 'required': ['words']}},
            {'name': 'fail', 'description': 'always fails', 'inputSchema': {'type': 'object', 'additionalProperties': False}},
        ]}})
    if method == 'tools/call':
        params = field(sent, 'params')
        if text_of(field(params, 'name')) == 'echo':
            words = text_of(field(field(params, 'arguments'), 'words'))
            result = {'resultType': 'complete', 'content': [{'type': 'text', 'text': f'echo: {words}'}], 'isError': False}
        else:
            result = {'resultType': 'complete', 'content': [{'type': 'text', 'text': 'it failed, as it always does'}], 'isError': True}
        return dumps({'jsonrpc': '2.0', 'id': id_, 'result': result})
    return dumps({'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32601, 'message': f'Method not found: {method}'}})


def scripted_model(conn):
    head, body = read_whole(conn)
    path = path_of(head)
    streamed = '"stream": true' in body
    offered = '"tools": [' in body
    told = '"role": "tool"' in body or '"type": "tool_result"' in body
    wants_tool = offered and not told
    if path.startswith('/broken'):
        return respond(conn, '200 OK', 'this is not json')
    reply = scripted(path, body)
    if reply is not None:
        return respond(conn, '200 OK', reply)
    if path == '/mcp':
        return respond(conn, '200 OK', mcp_answer(body))
    if path == '/v1/chat/completions':
        if header(head, 'authorization') == 'Bearer bad':
            return respond(conn, '401 Unauthorized', '{"error": {"message": "Incorrect API key provided: bad", "type": "invalid_request_error"}}')
        if streamed:
            return respond_stream(conn, openai_stream(wants_tool), 23, 0.005)
        if wants_tool:
            return respond(conn, '200 OK', r'{"choices": [{"message": {"role": "assistant", "content": null, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "weather", "arguments": "{\"city\": \"Lisbon\"}"}}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 7, "completion_tokens": 5}}')
        text = 'It is sunny in LISBON.' if told else 'Ulaanbaatar'
        return respond(conn, '200 OK', '{"choices": [{"message": {"role": "assistant", "content": "%s"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 21, "completion_tokens": 3}}' % text)
    if path == '/v1/embeddings':
        # (a row per text in the input list)
        pieces = body.split('"input": [')
        inputs = pieces[1].split(']')[0] if len(pieces) > 1 else ''
        rows = ', '.join('{"embedding": [%d.0, 0.5, -0.25]}' % i for i in range(inputs.count('"') // 2))
        return respond(conn, '200 OK', '{"data": [%s], "usage": {"prompt_tokens": 3}}' % rows)
    if path == '/v1/messages':
        if header(head, 'x-api-key') == 'bad':
            return respond(conn, '401 Unauthorized', '{"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}')
        if streamed:
            return respond_stream(conn, anthropic_stream(wants_tool), 31, 0.005)
        if wants_tool:
            return respond(conn, '200 OK', '{"content": [{"type": "text", "text": "Let me check."}, {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Lisbon"}}], "stop_reason": "tool_use", "usage": {"input_tokens": 12, "output_tokens": 15}}')
        text = 'It is sunny.' if told else 'Ulaanbaatar'
        return respond(conn, '200 OK', '{"content": [{"type": "text", "text": "%s"}], "stop_reason": "end_turn", "usage": {"input_tokens": 21, "output_tokens": 3}}' % text)
    return respond(conn, '404 Not Found', '{"error": {"message": "no such path"}}')


def model_env(port):
    return {
        'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{port}',
        'ANTHROPIC_API_KEY': 'test',
        'OPENAI_API_KEY': 'test',
        'CROW_LLM_TEST_BAD_KEY': 'bad',
    }


# ── a program talks to a model ──────────────────────────────────────

PROGRAM = """use llm

port = args[][0]
m = llm.reach['openai://127.0.0.1:`port`/test-model']
log[m.provider]
log[m.model]
log[m.base]
talk = llm.ask['What is the capital of Mongolia?']
r = llm.chat[m, talk, system = 'one word', tokens = 32]
log[r.text]
log[r.stop]
log[r.usage.input]
log[r.usage.output]
log[len[r.calls]]

--- the current weather in a city
fn weather[city]
    return 'sunny in `upper[city]`'

r2 = llm.chat[m, talk, tools = [weather]]
log[r2.stop]
log[r2.calls[0].name]
log[r2.calls[0].args.city]
talk2 = llm.said[talk, r2]
talk2 = llm.told[talk2, r2.calls[0], weather[r2.calls[0].args.city]]
log[len[talk2]]
r3 = llm.chat[m, talk2, tools = [weather]]
log[r3.text]

s = llm.follow[m, talk]
piece = llm.next[s]
while piece.ok and len[piece] gt 0
    log['piece: `piece`']
    piece = llm.next[s]
whole = llm.reply[s]
log[whole.text]
log[whole.stop]
log[whole.usage.output]

s2 = llm.follow[m, talk, tools = [weather]]
piece = llm.next[s2]
while piece.ok and len[piece] gt 0
    log['piece: `piece`']
    piece = llm.next[s2]
whole2 = llm.reply[s2]
log[whole2.stop]
log[whole2.calls[0].args.city]

e = llm.embed[m, ['one', 'two']]
log[e]

a = llm.reach['anthropic://claude-test']
log[a.base]
ra = llm.chat[a, talk]
log[ra.text]
log[ra.usage.input]
ra2 = llm.chat[a, talk, tools = [weather]]
log[ra2.text]
log[ra2.stop]
log[ra2.calls[0].id]
log[ra2.calls[0].args.city]
talka = llm.told[llm.said[talk, ra2], ra2.calls[0], 'sunny']
ra3 = llm.chat[a, talka, tools = [weather]]
log[ra3.text]
sa = llm.follow[a, talk]
piece = llm.next[sa]
while piece.ok and len[piece] gt 0
    log['piece: `piece`']
    piece = llm.next[sa]
log[llm.reply[sa].text]
log[llm.reply[sa].stop]
sa2 = llm.follow[a, talk, tools = [weather]]
piece = llm.next[sa2]
while piece.ok and len[piece] gt 0
    log['piece: `piece`']
    piece = llm.next[sa2]
wa = llm.reply[sa2]
log[wa.stop]
log[wa.calls[0].name]
log[wa.calls[0].args.city]
log[wa.usage.output]

bad = llm.reach['openai://127.0.0.1:`port`/test-model?key=CROW_LLM_TEST_BAD_KEY']
rb = llm.chat[bad, talk]
log[rb.ok]
log[rb.reason]
broken = llm.reach['openai://127.0.0.1:`port`/test-model?path=/broken']
rk = llm.chat[broken, talk]
log[rk.reason]
nobody = llm.reach['openai://127.0.0.1:1/test-model']
rn = llm.chat[nobody, talk]
log[rn.ok]
log[rn.kind]
sn = llm.follow[nobody, talk]
log[sn.ok]
log[sn.kind]
"""

PROGRAM_SAYS = """openai
test-model
http://127.0.0.1:{port}
Ulaanbaatar
end
21
3
0
tool
weather
Lisbon
3
It is sunny in LISBON.
piece: Ulaan
piece: baatar
piece:  é!
Ulaanbaatar é!
end
4
tool
Lisbon
[[0, 0.5, -0.25], [1, 0.5, -0.25]]
http://127.0.0.1:{port}
Ulaanbaatar
21
Let me check.
tool
toolu_1
Lisbon
It is sunny.
piece: Ulaan
piece: baatar é!
Ulaanbaatar é!
end
piece: Let me check.
tool
weather
Lisbon
15
false
the model's server answered 401: Incorrect API key provided: bad
the model's server answered no JSON: this is not json
false
llm
false
llm
"""


def talks_to_a_model():
    """A reply, a tool call the compiler wrote the schema for, the
    conversation continued with the tool's result, a reply followed piece
    by piece, a streamed tool call gathered, embeddings, the same in
    Anthropic's format, and the refusals: the same lines from both
    modes."""
    model = Server(scripted_model)
    d = fresh('talk')
    try:
        write(d, 'main.co', PROGRAM)
        want = PROGRAM_SAYS.replace('{port}', str(model.port))
        for interpreted in (True, False):
            out, err = run(d, interpreted, 'main.co', [str(model.port)], model_env(model.port))
            printed(f'a program talks to a model ({mode(interpreted)})', out, want, err)
    finally:
        model.stop()
        shutil.rmtree(d, ignore_errors=True)


# ── the agent loop ──────────────────────────────────────────────────

AGENT = """use llm, mcp

port = args[][0]
m = llm.reach['openai://127.0.0.1:`port`/test-model']

--- the current weather in a city
fn weather[city]
    if city eq 'Nowhere'
        return error['no station in `city`', 'weather']
    return 'sunny in `upper[city]`'

--- the forecast for a city, for a number of days
fn forecast[city, days = 2]
    return '`days` days of sun in `upper[city]`'

--- says its words back, from the program (a name an MCP server also offers)
fn echo[words]
    return 'echo `trim[words]`'

--- the program's gate: the weather may be asked for Lisbon alone
fn only_lisbon[call]
    if str[call.name] ne 'weather'
        return true
    return str[call.args.city] eq 'Lisbon'

--- what a smaller agent finds out about a question, with the weather as
--- its only tool: its answer, or why its run failed (a subagent is a
--- tool that runs the loop; a function sees no top-level name, so it
--- reaches the model itself)
fn research[question]
    port = args[][0]
    inner = llm.act[llm.reach['openai://127.0.0.1:`port`/test-model'], llm.ask[trim[question]], tools = [weather], rounds = 4]
    return inner.text

--- a researcher whose model refuses its key: its run fails, and it answers
--- a failure in words of its own, which the parent hears as one
fn research_locked[question]
    port = args[][0]
    inner = llm.act[llm.reach['openai://127.0.0.1:`port`/test-model?key=CROW_LLM_TEST_BAD_KEY'], llm.ask[trim[question]], tools = [weather], rounds = 4]
    if not inner.ok
        return error['the researcher could not run: `inner.reason`', 'research']
    return inner.text

done = llm.act[m, llm.ask['act: weather'], tools = [weather]]
log[done.text]
log[done.stop]
log[done.rounds]
log[done.usage.input]
log[done.usage.output]
log[len[done.talk]]
log[done.steps.what]
log[done.steps.name]
log[done.steps.how]
log[done.steps.input]
log[len[done.steps.ms]]

more = llm.act[m, llm.ask['act: forever'], tools = [weather], rounds = 3]
log[more.stop]
log[more.rounds]
log[len[more.talk]]
log[more.steps.how]

none = llm.act[m, llm.ask['act: nothing'], tools = [weather]]
log[none.text]
log[none.steps.how]

bad = llm.act[m, llm.ask['act: bad'], tools = [weather, forecast]]
log[bad.text]
log[bad.steps.how]

broken = llm.act[m, llm.ask['act: fail'], tools = [weather]]
log[broken.text]
log[broken.steps.how]

two = llm.act[m, llm.ask['act: two'], tools = [weather]]
log[two.text]
log[len[two.steps.what]]

gated = llm.act[m, llm.ask['act: two'], tools = [weather], allow = [only_lisbon]]
log[gated.text]
log[gated.steps.how]
ungated = llm.act[m, llm.ask['act: weather'], tools = [weather], allow = [weather]]
log[ungated.reason]

delegated = llm.act[m, llm.ask['act: delegate'], tools = [research, weather]]
log[delegated.text]
log[delegated.steps.name]
log[delegated.steps.how]
log[len[delegated.talk]]
locked = llm.act[m, llm.ask['act: locked'], tools = [research_locked, weather]]
log[locked.text]
log[locked.steps.how]
log[locked.talk[2].failed]

tight = llm.act[m, llm.ask['act: forever'], tools = [weather], budget = 10]
log[tight.stop]
log[tight.rounds]

cut = llm.act[m, llm.ask['act: length'], tools = [weather]]
log[cut.stop]
log[cut.text]

kept = llm.act[m, llm.ask['act: keep'], tools = [weather], keep = 1]
log[kept.text]
log[kept.rounds]
log[kept.talk[2].text]
log[kept.talk[2].cut]

packed = llm.act[m, llm.ask['act: compact'], tools = [weather], compact = 1]
log[packed.text]
log[packed.steps.what]
log[len[packed.talk]]
log[packed.rounds]

skilled = llm.act[m, llm.ask['act: skills'], tools = [weather], skills = 'skills', system = 'be brief']
log[skilled.text]
log[skilled.steps.how]
log[len[skilled.steps.what]]
unskilled = llm.act[m, llm.ask['act: skills'], tools = [weather], skills = 'bad_skills']
log[unskilled.ok]
log[unskilled.reason]

mcp_s = mcp.server['http://127.0.0.1:`port`/mcp']
over = llm.act[m, llm.ask['act: mcp'], tools = [weather], servers = [mcp_s]]
log[over.text]
log[over.steps.name]
log[over.steps.how]
taken = llm.act[m, llm.ask['act: mcp'], tools = [weather, echo], servers = [mcp_s]]
log[taken.reason]
std_s = mcp.server[command = args[][1]]
over_stdio = llm.act[m, llm.ask['act: mcp'], tools = [weather], servers = [std_s]]
log[over_stdio.text]
log[over_stdio.steps.name]
log[over_stdio.steps.how]
both = llm.act[m, llm.ask['act: mcp'], tools = [weather, echo], servers = [std_s]]
log[both.reason]

a = llm.reach['anthropic://claude-test']
da = llm.act[a, llm.ask['act: weather'], tools = [weather]]
log[da.text]
log[da.stop]
log[da.usage.input]
log[da.steps.how]
na = llm.act[a, llm.ask['act: nothing'], tools = [weather]]
log[na.text]
fa = llm.act[a, llm.ask['act: fail'], tools = [weather]]
log[fa.text]
ga = llm.act[a, llm.ask['act: two'], tools = [weather], allow = [only_lisbon]]
log[ga.text]
dla = llm.act[a, llm.ask['act: delegate'], tools = [research, weather]]
log[dla.text]
log[dla.steps.how]
la = llm.act[a, llm.ask['act: locked'], tools = [research_locked, weather]]
log[la.text]
"""

# The stand-in's two tools as a Crow program serving over its standard
# streams, started by the loop's client as a command.
STDIO_SERVER = """use mcp

--- says its words back
fn echo[words]
    return 'echo: `trim[words]`'

--- always fails
fn fail[]
    return error['it failed, as it always does', 'tool']

mcp.listen[tools = [echo, fail], name = 'stand-in']
"""

AGENT_SAYS = """heard: sunny in LISBON
end
2
28
8
4
['chat', 'tool', 'chat']
['test-model', 'weather', 'test-model']
['tool', 'ok', 'end']
[7, 0, 21]
3
rounds
3
7
['tool', 'ok', 'tool', 'ok', 'tool', 'ok']
heard: no tool is named nothing
['tool', 'unknown', 'end']
heard: a parsed value holds text 'many' where the program reads an int (forecast[]'s 'days')
['tool', 'arguments', 'end']
heard: no station in Nowhere
['tool', 'failed', 'end']
heard: sunny in LISBON | sunny in PORTO
4
heard: sunny in LISBON | weather is not allowed by the program
['tool', 'ok', 'refused', 'end']
a gate takes the call and answers a bool: weather takes 1 and answers text
heard: heard: sunny in LISBON
['test-model', 'research', 'test-model']
['tool', 'ok', 'end']
4
heard: the researcher could not run: the model's server answered 401: Incorrect API key provided: bad
['tool', 'failed', 'end']
true
budget
1
length
cut off
seen: [a result of 15 characters, cut] | [a result of 15 characters, cut] | sunny in LISBON
4
[a result of 15 characters, cut]
true
heard: resumed from the task: the weather (a summary)
['chat', 'tool', 'compact', 'chat']
2
2
seen: # A parity program

1. Take the next number. | Both modes must print the same lines.
 | a skill's file is named by its path inside the skill: ../../secrets.txt | no skill is named nothing | listed
['tool', 'ok', 'tool', 'ok', 'tool', 'failed', 'unknown', 'end']
8
false
the skill Bad-Name's name 'Bad-Name' holds 'B', and a name holds lowercase letters, digits and hyphens
seen: echo: over mcp | it failed, as it always does
['test-model', 'echo', 'fail', 'test-model']
['tool', 'ok', 'failed', 'end']
the MCP server http://127.0.0.1:@PORT@/mcp offers a tool named echo, which another tool has: the names must differ
seen: echo: over mcp | it failed, as it always does
['test-model', 'echo', 'fail', 'test-model']
['tool', 'ok', 'failed', 'end']
the MCP server @STDIO@ offers a tool named echo, which another tool has: the names must differ
heard: sunny in LISBON
end
33
['tool', 'ok', 'end']
heard: no tool is named nothing (error)
heard: no station in Nowhere (error)
heard: sunny in LISBON | weather is not allowed by the program (error)
heard: heard: sunny in LISBON
['tool', 'ok', 'end']
heard: the researcher could not run: the model's server answered 401: Incorrect API key provided: bad (error)
"""


def runs_an_agent():
    """The agent loop against the scripted model (a tool called once and
    answered, a model that keeps calling stopped by `rounds` and by
    `budget`, an unknown name, a wrong argument, a tool's own failure and
    two calls in one reply each told as the loop tells them — the wrong
    argument against a second tool list: the loop is copied per list — a
    gate, subagents, a reply cut by its length, a conversation kept short
    and one compacted, skills, an MCP server's tools over HTTP and over
    stdio), and the same in Anthropic's format, where a failed result
    reaches the wire as `is_error`."""
    model = Server(scripted_model)
    d = fresh('agent')
    # (the stdio server in a directory of its own, since a `.crow_build`
    # is one program's)
    stdio = fresh('stdio')
    try:
        write(d, 'main.co', AGENT)
        write(d, 'skills/crow-parity-test/SKILL.md', '---\nname: crow-parity-test\ndescription: Writes a parity program. Use when a feature needs a test under tests/parity/.\n---\n# A parity program\n\n1. Take the next number.\n')
        write(d, 'skills/crow-parity-test/references/parity.md', 'Both modes must print the same lines.\n')
        write(d, 'bad_skills/Bad-Name/SKILL.md', '---\nname: Bad-Name\ndescription: a skill whose name is no name\n---\nbody\n')
        write(stdio, 'server.co', STDIO_SERVER)
        command = f"cd '{stdio}' && '{CROW_PATH}' run -i server.co"
        want = AGENT_SAYS.replace('@PORT@', str(model.port)).replace('@STDIO@', command)
        for interpreted in (True, False):
            out, err = run(d, interpreted, 'main.co', [str(model.port), command], model_env(model.port))
            printed(f'an agent runs its tools in a loop ({mode(interpreted)})', out, want, err)
    finally:
        model.stop()
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(stdio, ignore_errors=True)


# ── the self-check's llm family ─────────────────────────────────────
#
# A model of its own that speaks both wire formats and says back what it
# was sent — the user's last words, the system prompt, the tokens at most
# and the temperature, a tool's result — so the answer of each case is
# known, and a request the package writes wrong answers otherwise.

def read_request(conn):
    """A request's path and body: the head to its blank line, the body by
    its length or, sent in chunks, to its last chunk. None where the
    connection ends first."""
    buf = b''

    def more():
        nonlocal buf
        try:
            chunk = conn.recv(65536)
        except OSError:
            return False
        buf += chunk
        return bool(chunk)

    while b'\r\n\r\n' not in buf:
        if not more():
            return None
    end = buf.find(b'\r\n\r\n')
    head = buf[:end].decode('utf-8', 'replace')
    path = path_of(head)
    if not path:
        return None
    start = end + 4
    if (header(head, 'transfer-encoding') or '').lower() == 'chunked':
        while True:
            body = unchunked(buf[start:])
            if body is not None:
                return path, body
            if not more():
                return None
    try:
        length = int(header(head, 'content-length') or '0')
    except ValueError:
        length = 0
    while len(buf) < start + length:
        if not more():
            return None
    return path, buf[start:start + length]


def unchunked(raw):
    """A chunked body decoded, or None while its last chunk has not come."""
    out, at = b'', 0
    while True:
        end = raw.find(b'\r\n', at)
        if end < 0:
            return None
        try:
            size = int(raw[at:end].decode().split(';')[0].strip(), 16)
        except (ValueError, UnicodeDecodeError):
            return None
        at = end + 2
        if size == 0:
            return out
        if at + size > len(raw):
            return None
        out += raw[at:at + size]
        at += size + 2


def split_inclusive(text):
    """A text's words, each with the space after it, as Rust's
    `split_inclusive(' ')` cuts them."""
    pieces, start = [], 0
    for i, c in enumerate(text):
        if c == ' ':
            pieces.append(text[start:i + 1])
            start = i + 1
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def heard_of(user, system, most, temperature, told, tool, turns):
    """What the model makes of a request: its answer's text (the words
    heard said back, or a tool's result read back), or a call of the first
    tool offered — its arguments the user's last words — and the tokens in
    and out (the turns it was sent, the words it answers)."""
    if told is not None:
        text = f'told {told}'
        return {'text': text, 'call': None, 'input': turns, 'output': len(text.split())}
    if tool is not None:
        return {'text': '', 'call': (tool, dumps({'x': user})), 'input': turns, 'output': 1}
    text = f'heard {user} | {system or "-"} | {dumps(most)} | {dumps(temperature)}'
    return {'text': text, 'call': None, 'input': turns, 'output': len(text.split())}


def openai_heard(sent):
    """An OpenAI-compatible request read: the system prompt is its first
    message, a tool's result a message of its own (the last, when the
    model is to read it back)."""
    messages = items(field(sent, 'messages'))
    system = next((text_of(field(m, 'content')) for m in messages if field(m, 'role') == 'system'), '')
    user = next((text_of(field(m, 'content')) for m in reversed(messages) if field(m, 'role') == 'user'), '')
    told = None
    if messages and field(messages[-1], 'role') == 'tool' and isinstance(field(messages[-1], 'content'), str):
        told = field(messages[-1], 'content')
    tools = items(field(sent, 'tools'))
    tool = field(field(tools[0], 'function'), 'name') if tools else None
    return heard_of(user, system, field(sent, 'max_tokens'), field(sent, 'temperature'), told, tool if isinstance(tool, str) else None, len(messages))


def openai_reply(r):
    usage = {'prompt_tokens': r['input'], 'completion_tokens': r['output']}
    if r['call']:
        name, args = r['call']
        return dumps({'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_self', 'type': 'function', 'function': {'name': name, 'arguments': args}}]}, 'finish_reason': 'tool_calls'}], 'usage': usage})
    return dumps({'choices': [{'message': {'role': 'assistant', 'content': r['text']}, 'finish_reason': 'stop'}], 'usage': usage})


def openai_events(r):
    """The reply as OpenAI streams it: the role, the words one by one (a
    keep-alive comment among them), the reason it stopped, the usage, done;
    a call's arguments in two pieces."""
    lines = []
    if r['call']:
        name, args = r['call']
        half = len(args) // 2
        lines.append({'choices': [{'delta': {'role': 'assistant', 'content': None, 'tool_calls': [{'index': 0, 'id': 'call_self', 'type': 'function', 'function': {'name': name, 'arguments': ''}}]}, 'finish_reason': None}]})
        for piece in (args[:half], args[half:]):
            lines.append({'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': piece}}]}, 'finish_reason': None}]})
        stop = 'tool_calls'
    else:
        lines.append({'choices': [{'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})
        for piece in split_inclusive(r['text']):
            lines.append({'choices': [{'delta': {'content': piece}, 'finish_reason': None}]})
        stop = 'stop'
    lines.append({'choices': [{'delta': {}, 'finish_reason': stop}]})
    lines.append({'choices': [], 'usage': {'prompt_tokens': r['input'], 'completion_tokens': r['output']}})
    out = ''
    for i, line in enumerate(lines):
        out += f'data: {dumps(line)}\n\n'
        if i == 1:
            out += ': keep-alive\n\n'
    return out + 'data: [DONE]\n\n'


def anthropic_words(content):
    """A user turn's words, as Anthropic's messages carry them: the
    content itself, or its text blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    texts = [field(b, 'text') for b in content if field(b, 'type') == 'text' and isinstance(field(b, 'text'), str)]
    return ''.join(texts) if texts else None


def anthropic_heard(sent):
    """An Anthropic request read: the system prompt a field of its own, a
    tool's result a block of the last user turn."""
    messages = items(field(sent, 'messages'))
    user = next((w for w in (anthropic_words(field(m, 'content')) for m in reversed(messages) if field(m, 'role') == 'user') if w is not None), '')
    told = None
    if messages and field(messages[-1], 'role') == 'user' and isinstance(field(messages[-1], 'content'), list):
        results = [b for b in field(messages[-1], 'content') if field(b, 'type') == 'tool_result']
        if results:
            told = anthropic_words(field(results[-1], 'content'))
    tools = items(field(sent, 'tools'))
    tool = field(tools[0], 'name') if tools else None
    return heard_of(user, text_of(field(sent, 'system')), field(sent, 'max_tokens'), field(sent, 'temperature'), told, tool if isinstance(tool, str) else None, len(messages))


def anthropic_reply(r):
    usage = {'input_tokens': r['input'], 'output_tokens': r['output']}
    if r['call']:
        name, args = r['call']
        return dumps({'content': [{'type': 'tool_use', 'id': 'toolu_self', 'name': name, 'input': json.loads(args)}], 'stop_reason': 'tool_use', 'usage': usage})
    return dumps({'content': [{'type': 'text', 'text': r['text']}], 'stop_reason': 'end_turn', 'usage': usage})


def anthropic_events(r):
    """The reply as Anthropic streams it: the message's start with its
    input tokens, a block's start, its pieces (a ping among them), its
    stop, the reason and the output tokens, the message's stop."""
    events = [('message_start', {'type': 'message_start', 'message': {'usage': {'input_tokens': r['input'], 'output_tokens': 1}}})]
    if r['call']:
        name, args = r['call']
        half = len(args) // 2
        events.append(('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'toolu_self', 'name': name, 'input': {}}}))
        for piece in (args[:half], args[half:]):
            events.append(('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': piece}}))
        stop = 'tool_use'
    else:
        events.append(('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}))
        events.append(('ping', {'type': 'ping'}))
        for piece in split_inclusive(r['text']):
            events.append(('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': piece}}))
        stop = 'end_turn'
    events.append(('content_block_stop', {'type': 'content_block_stop', 'index': 0}))
    events.append(('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': {'output_tokens': r['output']}}))
    events.append(('message_stop', {'type': 'message_stop'}))
    return ''.join(f'event: {e}\ndata: {dumps(d)}\n\n' for e, d in events)


def say_back_model(conn):
    """One connection: a request read whole (by its length, or its
    chunks), answered by its path; the events of a streamed reply in
    chunks of 23 and 26 bytes by turns, as a server's writes fall where
    they fall."""
    got = read_request(conn)
    if got is None:
        return
    path, body = got
    try:
        sent = json.loads(body)
    except ValueError:
        sent = None
    if path.startswith('/refuse'):
        return respond(conn, '401 Unauthorized', '{"error": {"message": "no key here"}}')
    if path.startswith('/broken'):
        return respond(conn, '200 OK', 'this is not json')
    if path.endswith('/embeddings'):
        # (a row per text: its length in characters, then two numbers)
        rows = [{'embedding': [float(len(x)) if isinstance(x, str) else 0.0, 0.5, -0.25]} for x in items(field(sent, 'input'))]
        return respond(conn, '200 OK', dumps({'data': rows, 'usage': {'prompt_tokens': 1}}))
    streamed = field(sent, 'stream') is True
    if path.endswith('/chat/completions'):
        r = openai_heard(sent)
        if streamed:
            return respond_stream(conn, openai_events(r), 23, 0)
        return respond(conn, '200 OK', openai_reply(r))
    if path == '/v1/messages':
        r = anthropic_heard(sent)
        if streamed:
            return respond_stream(conn, anthropic_events(r), 23, 0)
        return respond(conn, '200 OK', anthropic_reply(r))
    return respond(conn, '404 Not Found', '{"error": {"message": "no such path"}}')


# The cases: a conversation begun, added to by the user, by the model's
# reply and by a tool's result, and each refused where its value is not
# what it says; a model reached by each URL's form and refused by each
# mistake; then, against the say-back model, a reply, its options sent, a
# tool called and its result read back, a reply followed piece by piece (a
# call's too), embeddings, the agent loop, each again in Anthropic's
# format, and a server that refuses, one that answers no JSON and one
# nobody answers. A case that names `self_tool_@N` has the tool the model
# may call: the compiler writes its schema from its comment and its
# parameter, which its body reads as text.

REPLY = """json['{"text": "hello", "calls": [], "usage": {"input": 1, "output": 1}, "stop": "end"}']"""
CALLED = """json['{"text": "", "calls": [{"id": "c1", "name": "f", "args": {"x": 1}}], "usage": {"input": 1, "output": 1}, "stop": "tool"}']"""
OPEN = "self_p = env['CROW_SELF_LLM', '9']\nm = llm.reach['openai://127.0.0.1:`self_p`/self-model']"
ANTHROPIC = "a = llm.reach['anthropic://self-model?key=CROW_SELF_LLM']"
TOOL = "--- says its argument back\nfn self_tool_@N[x]\n    return 'echo `trim[x]`'"


def followed(s):
    return f"piece = llm.next[{s}]\nwhile piece.ok and len[piece] gt 0\n    log['piece: [`piece`]']\n    piece = llm.next[{s}]\nwhole = llm.reply[{s}]"


def tool_flow(m):
    return (f"t = llm.ask['hi']\nr = llm.chat[{m}, t, tools = [self_tool_@N]]\nlog[r.stop]\nlog[starts[r.calls[0].name, 'self_tool_']]\nlog[r.calls[0].args.x]\n"
            f"t2 = llm.told[llm.said[t, r], r.calls[0], self_tool_@N[str[r.calls[0].args.x]]]\nlog[len[t2]]\nr2 = llm.chat[{m}, t2, tools = [self_tool_@N]]\nlog[r2.text]\nlog[r2.stop]\nlog[r2.usage.input]")


PURE = [
    ('ask', "log[llm.ask['hi there']]"),
    ('asked', "t = llm.ask['hi']\nlog[llm.asked[t, 'and you']]"),
    ('said', f"t = llm.ask['hi']\nlog[llm.said[t, {REPLY}]]"),
    ('told', f"t = llm.ask['hi']\nr = {CALLED}\nt2 = llm.said[t, r]\nlog[llm.told[t2, r.calls[0], 'done']]\nlog[len[t2]]"),
    ('said-no-reply', "r = llm.said[llm.ask['hi'], json['{\"a\": 1}']]\nlog[r.ok]\nlog[r.reason]"),
    ('told-no-call', "r = llm.told[llm.ask['hi'], json['{\"a\": 1}'], 'x']\nlog[r.ok]\nlog[r.reason]"),
    ('no-talk', "r = llm.asked[json['{\"a\": 1}'], 'x']\nlog[r.ok]\nlog[r.reason]"),
    ('reach', "m = llm.reach['openai://localhost:11434/llama3.2']\nlog[m.provider]\nlog[m.model]\nlog[m.base]\nlog[m.path]\nlog[m.key]"),
    ('reach-options', "m = llm.reach['openai://127.0.0.1:8000/org/model?path=/api/v1&key=SELF_KEY']\nlog[m.model]\nlog[m.base]\nlog[m.path]\nlog[m.key]"),
    ('reach-no-key', "log[llm.reach['openai://api.example.com/gpt'].reason]"),
    ('reach-anthropic-no-key', "log[llm.reach['anthropic://claude-x'].reason]"),
    ('reach-no-model', "log[llm.reach['anthropic://'].reason]"),
    ('reach-host-no-model', "log[llm.reach['openai://localhost:11434'].reason]"),
    ('reach-scheme', "log[llm.reach['http://x'].reason]"),
]

LIVE = [
    ('chat', f"{OPEN}\nr = llm.chat[m, llm.ask['hi there']]\nlog[r.text]\nlog[r.stop]\nlog[r.usage.input]\nlog[r.usage.output]\nlog[len[r.calls]]"),
    ('chat-options', f"{OPEN}\nr = llm.chat[m, llm.ask['hi'], system = 'be brief', tokens = 16, temperature = 0.5]\nlog[r.text]\nlog[r.usage.input]"),
    ('tool', f"{OPEN}\n{tool_flow('m')}"),
    ('act', f"{OPEN}\nd = llm.act[m, llm.ask['hi'], tools = [self_tool_@N]]\nlog[d.text]\nlog[d.stop]\nlog[d.rounds]\nlog[len[d.talk]]\nlog[d.steps.what]\nlog[d.steps.how]\nlog[d.usage.input]\nlog[d.usage.output]"),
    ('follow', f"{OPEN}\ns = llm.follow[m, llm.ask['one two']]\n{followed('s')}\nlog[whole.text]\nlog[whole.stop]\nlog[whole.usage.input]\nlog[whole.usage.output]"),
    ('follow-tool', f"{OPEN}\ns = llm.follow[m, llm.ask['hi'], tools = [self_tool_@N]]\n{followed('s')}\nlog[whole.stop]\nlog[starts[whole.calls[0].name, 'self_tool_']]\nlog[whole.calls[0].args.x]"),
    ('embed', f"{OPEN}\nlog[llm.embed[m, ['one', 'three']]]"),
    ('anthropic', f"{OPEN}\n{ANTHROPIC}\nr = llm.chat[a, llm.ask['hi there'], system = 'be brief']\nlog[r.text]\nlog[r.stop]\nlog[r.usage.input]\nlog[r.usage.output]"),
    ('anthropic-tool', f"{OPEN}\n{ANTHROPIC}\n{tool_flow('a')}"),
    ('anthropic-act', f"{OPEN}\n{ANTHROPIC}\nd = llm.act[a, llm.ask['hi'], tools = [self_tool_@N]]\nlog[d.text]\nlog[d.stop]\nlog[d.rounds]\nlog[d.steps.how]"),
    ('anthropic-follow', f"{OPEN}\n{ANTHROPIC}\ns = llm.follow[a, llm.ask['one two']]\n{followed('s')}\nlog[whole.text]\nlog[whole.stop]\nlog[whole.usage.output]"),
    ('anthropic-follow-tool', f"{OPEN}\n{ANTHROPIC}\ns = llm.follow[a, llm.ask['hi'], tools = [self_tool_@N]]\n{followed('s')}\nlog[whole.stop]\nlog[whole.calls[0].id]\nlog[whole.calls[0].args.x]"),
    ('anthropic-embed', f"{OPEN}\n{ANTHROPIC}\nlog[llm.embed[a, ['x']].reason]"),
    ('refused', "self_p = env['CROW_SELF_LLM', '9']\nm = llm.reach['openai://127.0.0.1:`self_p`/self-model?path=/refuse']\nr = llm.chat[m, llm.ask['hi']]\nlog[r.ok]\nlog[r.reason]"),
    ('no-json', "self_p = env['CROW_SELF_LLM', '9']\nm = llm.reach['openai://127.0.0.1:`self_p`/self-model?path=/broken']\nr = llm.chat[m, llm.ask['hi']]\nlog[r.ok]\nlog[r.reason]"),
    ('nobody', "m = llm.reach['openai://127.0.0.1:9/self-model']\nr = llm.chat[m, llm.ask['hi']]\nlog[r.ok]\nlog[r.kind]"),
]


# The answers the self-check's reference gave (tests/self/reference.py):
# a conversation is a list of turns, each a map shown with its keys in
# order; the model says back `heard <the user's last words> | <the system
# prompt, or -> | <max> | <temperature>`, a tool's result as `told
# <result>`, or a call of the first tool offered with the user's words as
# `x`, its usage the turns it was sent and the words it answers, and it
# streams its words one by one.

def number(x):
    """A number as Crow shows it: a whole float without its point."""
    s = repr(x)
    return s[:-2] if s.endswith('.0') else s


def heard_back(user, system='', most=1024, temperature=1.0):
    return f'heard {user} | {system or "-"} | {number(most)} | {number(temperature)}'


def pieces_of(text):
    words = text.split(' ')
    return [p for p in [w + ' ' for w in words[:-1]] + [words[-1]] if p]


def pieces_said(text, lines):
    return '\n'.join([f'piece: [{p}]' for p in pieces_of(text)] + lines)


def answers():
    hi_there = heard_back('hi there')
    one_two = heard_back('one two')
    brief = heard_back('hi there', 'be brief')
    tool = '\n'.join(['tool', 'true', 'hi', '3', 'told echo hi', 'end', '3'])
    return {
        'ask': "[[role = 'user', text = 'hi there']]",
        # (the reference kept this answer under `ask-more`, the function's
        # name before it was `asked`, and so had none for the case)
        'asked': "[[role = 'user', text = 'hi'], [role = 'user', text = 'and you']]",
        'said': "[[role = 'user', text = 'hi'], [calls = [], role = 'assistant', text = 'hello']]",
        'told': "[[role = 'user', text = 'hi'], [calls = [[args = [x = 1], id = 'c1', name = 'f']], role = 'assistant', text = ''], [id = 'c1', role = 'tool', text = 'done']]\n2",
        'said-no-reply': 'false\na reply is what chat[…] and reply[…] answer',
        'told-no-call': "false\na call is one of a reply's calls",
        'no-talk': 'false\na conversation is what ask[…] answers, with its turns added by asked, said and told',
        'reach': 'openai\nllama3.2\nhttp://localhost:11434\n/v1\nOPENAI_API_KEY',
        'reach-options': 'org/model\nhttp://127.0.0.1:8000\n/api/v1\nSELF_KEY',
        'reach-no-key': "cannot reach 'openai://api.example.com/gpt': the key's variable OPENAI_API_KEY is not set",
        'reach-anthropic-no-key': "cannot reach 'anthropic://claude-x': the key's variable ANTHROPIC_API_KEY is not set",
        'reach-no-model': "cannot reach 'anthropic://': no model is named — anthropic://claude-sonnet-5-5",
        'reach-host-no-model': "cannot reach 'openai://localhost:11434': a model is named after its host — openai://localhost:11434/llama3.2",
        'reach-scheme': "cannot reach 'http://x': a model's URL is anthropic://<model> or openai://<host>/<model>",
        'chat': '\n'.join([hi_there, 'end', '1', str(len(hi_there.split())), '0']),
        'chat-options': '\n'.join([heard_back('hi', 'be brief', 16, 0.5), '2']),
        'tool': tool,
        # the loop: a chat that calls the tool (1 turn in, 1 token out), the
        # tool's answer, a chat that says it back (3 turns in, 3 words out)
        'act': '\n'.join(['told echo hi', 'end', '2', '4', "['chat', 'tool', 'chat']", "['tool', 'ok', 'end']", '4', '4']),
        'follow': pieces_said(one_two, [one_two, 'end', '1', str(len(one_two.split()))]),
        'follow-tool': 'tool\ntrue\nhi',
        'embed': '[[3, 0.5, -0.25], [5, 0.5, -0.25]]',
        'anthropic': '\n'.join([brief, 'end', '1', str(len(brief.split()))]),
        'anthropic-tool': tool,
        'anthropic-act': '\n'.join(['told echo hi', 'end', '2', "['tool', 'ok', 'end']"]),
        'anthropic-follow': pieces_said(one_two, [one_two, 'end', str(len(one_two.split()))]),
        'anthropic-follow-tool': 'tool\ntoolu_self\nhi',
        'anthropic-embed': 'anthropic serves no embeddings: reach an openai://<host>/<embedding model>',
        'refused': "false\nthe model's server answered 401: no key here",
        'no-json': "false\nthe model's server answered no JSON: this is not json",
        'nobody': 'false\nllm',
    }


def cases():
    """Each case as a program of its own: its id as the self-check named
    it, its source, and the lines it prints."""
    said_back = answers()
    out = []
    for family, group in (('llm', PURE), ('llm-live', LIVE)):
        for name, body in group:
            lines = ['use llm', '']
            if 'self_tool_@N' in body:
                lines += TOOL.split('\n') + ['']
            source = '\n'.join(lines + body.split('\n')).replace('@N', '0') + '\n'
            out.append((f'{family}/{name}', source, said_back[name] + '\n'))
    return out


def self_case(port, case, source, want):
    """A case run in both modes against the say-back model, with no key
    of either provider set (the self-check ran its cases in an environment
    of its own, holding neither)."""
    d = fresh('case')
    try:
        write(d, 'main.co', source)
        env = {
            'CROW_SELF_LLM': str(port),
            'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{port}',
            'ANTHROPIC_API_KEY': None,
            'OPENAI_API_KEY': None,
            'CROW_REPLAY': None,
        }
        for interpreted in (True, False):
            out, err = run(d, interpreted, 'main.co', [], env)
            printed(f'the case {case} ({mode(interpreted)})', out, want, err)
    finally:
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
    print(f'model: the llm package against stand-in models, both modes ({CROW})\n', flush=True)
    start_at = time.time()
    say_back = Server(say_back_model)
    try:
        with concurrent.futures.ThreadPoolExecutor(JOBS) as pool:
            jobs = [
                pool.submit(guarded, 'a program talks to a model', talks_to_a_model),
                pool.submit(guarded, 'an agent runs its tools in a loop', runs_an_agent),
            ]
            for case, source, want in cases():
                jobs.append(pool.submit(guarded, f'the case {case}', self_case, say_back.port, case, source, want))
            for job in jobs:
                job.result()
    finally:
        say_back.stop()
        sweep()
    took = time.time() - start_at
    if failures:
        print(f'\n✗ {len(failures)} failed ({took:.1f}s)')
        sys.exit(1)
    print(f'\n✓ the llm package answers its models alike in both modes ({took:.1f}s)')


if __name__ == '__main__':
    main()
