# -*- coding: utf-8 -*-
"""A stand-in for a model's server, speaking Ollama's OpenAI-compatible
protocol on 127.0.0.1:11434 (or the port given): embeddings from hashed
words, so alike texts have alike vectors, and chat answers made of the
first sentence of the first two notes in the system prompt, cited as the
app asks. It exists so the app's fixtures can be recorded with no model
(`crow check --record-fixtures`), deterministically; with Ollama or any
other server at the same address, the same command records real answers.

    python3 reference/standin.py &
    crow check --record-fixtures main.co
"""
import hashlib
import json
import math
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

DIMENSIONS = 64


def embedding(text):
    v = [0.0] * DIMENSIONS
    for w in re.findall(r"[a-z0-9_]+", text.lower()):
        h = hashlib.sha256(w.encode("utf-8")).digest()
        v[h[0] % DIMENSIONS] += 1.0 if h[1] % 2 == 0 else -1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


def answer(messages):
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    notes = re.findall(r"^\[(\d+)\] \([^)]*\) (.*)$", system, re.M)
    pieces = []
    for n, body in notes[:2]:
        first = body.split(". ")[0].rstrip(".") + "."
        pieces.append("%s [%s]" % (first, n))
    if not pieces:
        return "The notes do not say."
    return "From the notes: " + " ".join(pieces)


def words(text):
    return len(text.split())


class StandIn(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        model = body.get("model", "stand-in")
        if self.path.endswith("/embeddings"):
            texts = body.get("input", [])
            if isinstance(texts, str):
                texts = [texts]
            data = [{"object": "embedding", "index": i, "embedding": embedding(t)} for i, t in enumerate(texts)]
            used = sum(words(t) for t in texts)
            out = {"object": "list", "data": data, "model": model, "usage": {"prompt_tokens": used, "total_tokens": used}}
        elif self.path.endswith("/chat/completions"):
            messages = body.get("messages", [])
            said = answer(messages)
            out = {
                "id": "stand-in-1",
                "object": "chat.completion",
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": said}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": sum(words(m.get("content", "")) for m in messages), "completion_tokens": words(said)},
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        text = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(text)))
        self.end_headers()
        self.wfile.write(text)

    def log_message(self, fmt, *args):
        sys.stderr.write("stand-in: %s\n" % (fmt % args))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    sys.stderr.write("stand-in model on 127.0.0.1:%d\n" % port)
    HTTPServer(("127.0.0.1", port), StandIn).serve_forever()
