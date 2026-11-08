# Contributing

Raven is in alpha, developed by Vijay Sriram at aggnr beside Crow. During
the alpha it takes no pull requests: its packages still change with
Crow's releases, and every line is kept under one hand so that the
licence can still change. A pull request is closed with this note by a
workflow, so nobody waits on one.

What is welcome, in Issues:

- **A bug**, with the program that shows it and what each mode said —
  `crow run -i file.co` and `crow run file.co`. Raven's answers are held
  to be the same in both, so a difference between them is a bug by
  itself. Where a model or a server is involved, say which, and what it
  was sent if you can (`crow check --record-fixtures` keeps both).
- **A question** the READMEs, the book's chapters (`docs/book/src`) or the
  design notes (`docs/`) do not answer.
- **What you built with it.**

Working from a checkout: `./test.sh [crow]` runs every suite — the
packages' tests and examples, the parity programs, the refusals, the
stand-in servers' drives and the examples' tests — against a crow binary;
it is what a change passes before it is done.
