# Packages

A package is a directory of Crow files rooted at itself. The crowfile names each dependency as a git repository and a version, or as a local path, and `crow install` checks each one out into the `crow_modules` directory.

A program imports a package's parts with `use`: the line `use vectors.flat` reads the flat index of the vectors package, and `use retrieval.bm25` the BM25 ranking of the retrieval package.

Every package in the Crow repository is held to a reference. The retrieval package matches scikit-learn and rank_bm25 to the last digit, the vectors package matches faiss's positions exactly, and the optimize package follows scipy step for step.
