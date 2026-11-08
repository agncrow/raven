# Fixtures

A test that reaches a database server or a model needs it running, unless it was recorded. The command `crow check --record-fixtures` runs the tests against the real servers and keeps what each statement and each request answered under the `.crow/fixtures` directory.

The command `crow test --fixtures` runs the tests from what was kept, in both modes, and names a statement or a request nothing recorded.

A request's fixture is named by its method, its URL and what it sends, so a changed prompt to a model is a changed request. Its headers, and the key in them, are no part of it.
