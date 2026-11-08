# Derivations

A derivation is a value the app computes from what it reads: facts, tables, files, or another derivation. It is declared with `derive`, and the app recomputes it when something it reads moves. Nothing calls it; a view or a handler names it, and the value is there.

Who holds a derivation is inferred from what it reads. A derivation that reads only external sources is the app's, shared by every reader. One that reads a session's facts is the session's, computed for that reader alone.

A derivation over an external source stays fresh by its window: `fresh = 300` recomputes it at most every five minutes, and the default of zero means as fresh as the loop, recomputed every render.

A write reaches every reader. When a handler writes a table, every session whose page read a derivation over that table renders again, so a reader sees another reader's change as it lands.
