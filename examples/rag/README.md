# Ask the notes — a RAG app

Raven's RAG example, Crow's third reference app until it moved here (its
design is crow's docs/rag-design.md): the markdown notes under
`docs/` are cut into chunks, embedded once through a model and indexed; a
question finds its chunks by two rankings — the vectors' nearest and
BM25's best, fused by reciprocal rank — and the model answers from them,
citing each note it used as `[n]`. The conversation is the session's; the
index is the app's, shared by every reader and kept an hour.

From this folder, `crow run main.co` serves it on port 3400. The model and
the embedder are reached by the environment: `RAG_MODEL`
(`openai://127.0.0.1:11434/llama3.2` unless set) and `RAG_EMBEDDER`
(`openai://127.0.0.1:11434/nomic-embed-text`) — Ollama's addresses. Any
server speaking the OpenAI chat completions and embeddings serves, and
`anthropic://<model>` answers the questions (the embedder stays
OpenAI-compatible, since Anthropic serves no embeddings).

## The parts

| File | What it holds |
|---|---|
| `main.co` | `serve`, the page |
| `chat/corpus.co` | The notes cut into chunks at blank lines — each its file and its number, a heading on its own left out — and the embedder reached; `crow test chat/corpus.co` |
| `chat/retrieve.co` | `fused[near, lexical_top, k]`: two rankings fused by reciprocal rank — each position scoring 1 / (60 + its rank) in each ranking it is in — and the `k` best kept; `hybrid[…]`: a question's chunks, by its embedding's nearest (`vectors.flat`) and BM25's best (`retrieval.bm25`); `crow test chat/retrieve.co` |
| `chat/page.co` | The derivations — the chunks, their index, their BM25 ranking — the handler that asks (the chunks numbered in the system prompt, the model held to them), and the page: each turn's question, answer and the notes it read; `crow test --fixtures chat/page.co` |
| `docs/*.md` | The notes: five short pages about Crow |
| `crow_modules/` | The packages the app imports, linked to raven's `packages/llm` and `packages/mcp` and to Pie's `packages/vectors` and `packages/retrieval` in a Pie checkout beside raven, `../pie` (`crowfile` names them; the links are committed, relative to the checkout, where `crow install` would write them again as absolute paths) |
| `reference/standin.py` | A stand-in for the model's server, for recording the fixtures with no model |

## The tests, without a model

The page's tests ask the model, so they replay what a recording kept under
`.crow/fixtures` — one file per request, named by its method, URL and
body (spec, Requests):

```
crow test chat/corpus.co
crow test chat/retrieve.co
crow test --fixtures chat/page.co
crow test --fixtures --compiled chat/page.co
```

(`crow test` runs one file's blocks: a part's tests run from the part.)

The fixtures in this folder were recorded against `reference/standin.py`,
a stand-in speaking Ollama's OpenAI-compatible protocol on port 11434 —
embeddings from hashed words, so alike texts have alike vectors, and
answers made of the first sentence of the first two notes given, cited as
the app asks — so they are deterministic and need no model. To record
against a real model, start Ollama (or point the variables at a server),
delete `.crow/fixtures/*.json`, and run the recording:

```
python3 reference/standin.py &      # or ollama serve, with the models pulled
crow check --record-fixtures main.co
```

A question the fixtures do not hold is named by the failing test (``no
fixture holds `POST http://127.0.0.1:11434/v1/chat/completions` — `crow
check --record-fixtures` records it where the server is``).
