# 10. Google Gemini as a second language model provider

- Status: accepted
- Date: 2026-10-21

## Context

The owner has a Google Gemini key, not a Claude key. The provider is already hidden behind
`LLMProvider` (ADR 0003/0005), so a second provider does not touch chat, agents or the
permission engine. New Google AI Studio keys start with `AQ.` ("auth keys"); users
report problems with these keys on Google's OpenAI-compatible endpoint.

## Decision

- `GeminiProvider` uses the native REST API (`streamGenerateContent`, SSE) with the key
  in the `x-goog-api-key` header, never in the URL.
- Internal content blocks stay in the Claude format; the provider translates to Gemini
  `contents` (function calls, function responses, thought signatures) and back.
  Tool schemas are passed unchanged as `parametersJsonSchema`.
- Tool lock for untrusted data maps to `functionCallingConfig.mode = NONE`. Safety blocks
  map to the existing `refusal` handling.
- Product research uses Google Search grounding. Gemini cannot restrict domains, so the
  shop service keeps validating every URL against the shop allowlist afterwards.
- Selection order: Claude key → Gemini key (Windows Credential Manager via
  `sentinel gemini login`, or `GEMINI_API_KEY`) → offline. Default model
  `gemini-flash-latest`, configurable with `SENTINEL_GEMINI_MODEL`.

## Consequences

- On the free tier Google may use prompts and answers to improve its products. Jarvis
  sends alerts, process names and memories to the model, so a billing-enabled key is
  recommended for real use.
- Costs are estimated with paid-tier prices; the daily and monthly limits still apply.
