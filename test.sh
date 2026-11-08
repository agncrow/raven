#!/bin/bash
# Raven's whole suite against a crow binary, from this repository's root:
# each package's test blocks and its example in both modes, the book's
# programs in both modes, the parity programs (interpreted against
# compiled), the refusals in both modes and the language server, the
# packages driven against stand-in servers (tests/model.py: a model
# speaking both wire formats; tests/mcp.py: MCP clients and servers over
# HTTP and stdio), and the two examples' tests from their recorded
# fixtures in both modes — the RAG example's where a Pie checkout lies
# beside this one (../pie), whose vectors and retrieval packages it uses.
# Usage: ./test.sh [path to crow]   (the crow on the PATH by default)
set -u
CROW=${1:-crow}
CROW=$(command -v "$CROW" || echo "$CROW")
cd "$(dirname "$0")" || exit 1
failed=()
step() {
    local name=$1
    shift
    echo "== $name"
    "$@" || failed+=("$name")
    echo
}
step packages "$CROW" test --packages
step book "$CROW" test --book
step parity "$CROW" test --parity
step errors "$CROW" test --errors
step model python3 tests/model.py "$CROW"
step mcp python3 tests/mcp.py "$CROW"
agent() {
    (cd examples/agent && "$CROW" test agent/store.co && "$CROW" test agent/judge.co && "$CROW" test --fixtures agent/support.co && "$CROW" test --fixtures agent/trace.co && "$CROW" test --fixtures agent/chat.co && "$CROW" test --fixtures --compiled agent/trace.co && "$CROW" test --fixtures --compiled agent/chat.co)
}
step agent agent
rag() {
    if [ ! -d ../pie/packages/vectors ] || [ ! -d ../pie/packages/retrieval ]; then
        echo "needs a Pie checkout beside raven (../pie): git clone https://github.com/agncrow/pie ../pie"
        return 1
    fi
    (cd examples/rag && "$CROW" test chat/corpus.co && "$CROW" test chat/retrieve.co && "$CROW" test --fixtures chat/page.co && "$CROW" test --fixtures --compiled chat/page.co)
}
step rag rag
if [ ${#failed[@]} -eq 0 ]; then
    echo "✓ raven: every step passes ($CROW)"
else
    echo "✗ raven: ${failed[*]} failed ($CROW)"
    exit 1
fi
