# 6. Local voice pipeline, hybrid memory search, data-only plugins

- Status: accepted
- Date: 2026-10-20

## Voice

- All speech processing runs locally on the CPU: openWakeWord ("hey jarvis"),
  a numpy double-clap detector, push-to-talk, faster-whisper (int8) and Piper.
- Voice packages are an optional extra. Without them the app works fully and spoken
  output falls back to the WebView's speech synthesis.
- "SENTINEL, Stopp" is detected locally on the transcript *before* the chat, so the
  emergency stop never depends on the model or the network.

## Memory

- SQLite FTS5 (BM25) combined with local vectors via rank fusion. The default
  embedder hashes character trigrams (no download, robust to typos and inflection);
  fastembed with a multilingual model is optional.
- Memories are created only from the user's own messages, with their source. A
  secret filter rejects passwords, keys and tokens. Memories are notes for the model,
  never instructions or permissions.

## Plugins

- Plugins are JSON manifests with routines and news feeds only, no code. They are
  disabled until the user enables them; their routines still go through the engine.
