# Language models

The module `llm` makes a model a connection. The function `reach` takes a URL: `anthropic://` and a model's name, or `openai://` with a host and a model's name for any server speaking the OpenAI chat completions, such as Ollama or vLLM. The key is read from the environment when a call is made.

A conversation is a list of turns, built a turn at a time: `ask` adds the user's, `said` the model's reply, `told` a tool's result. The function `chat` asks the model and answers a reply with its text, its calls, its usage and why it stopped.

Tools are functions. Writing `tools = [weather]` hands the function, and the compiler writes its schema from the parameters and the comment above it. The program dispatches the model's calls and hands each result back.

The function `embed` answers a matrix of embeddings, a row per text, from an OpenAI-compatible endpoint. A reply can also be followed piece by piece with `follow` and `next`.
