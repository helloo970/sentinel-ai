# 11. OpenRouter as language model provider

- Status: accepted
- Date: 2026-10-21

## Context

The free Gemini quota of the owner's key ran out: a tiny request took over two minutes and
then returned "quota exceeded". The owner has an OpenRouter account and wants Jarvis to be
fast. OpenRouter offers Claude, Gemini and other models through one OpenAI-compatible API.

## Decision

- `OpenRouterProvider` speaks the OpenAI chat completions API with streaming (SSE),
  function calling and `response_format` JSON schemas. Internal content blocks stay in
  the Claude format and are translated both ways; streamed tool-call fragments are
  assembled by index.
- `reasoning_details` from the model are passed back unchanged with tool results, as
  OpenRouter requires. Reasoning itself is off by default (`SENTINEL_OPENROUTER_REASONING`)
  because it makes every answer much slower.
- Default model `anthropic/claude-opus-5.5` (configurable with `SENTINEL_OPENROUTER_MODEL`).
- Key in Windows Credential Manager via `sentinel openrouter login`, or
  `OPENROUTER_API_KEY`; only sent in the Authorization header.
- Provider order: Claude direct → OpenRouter → Gemini → offline.
- Product research uses OpenRouter's web plugin; the shop service still checks every URL
  against the shop allowlist.

## Consequences

- Prompts go to OpenRouter and the model provider behind it.
- Costs are estimated with the provider prices; daily and monthly limits still apply.
