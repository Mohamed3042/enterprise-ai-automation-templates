# Recorded provider responses

**These are authored, not captured from a live account.** No `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY` exists on the machine this repository was built on, so the Anthropic and
OpenAI-compatible adapters are held to the vendors' *documented* wire shapes rather than to
observed traffic. Each file names the documentation page it was written from.

The Gemini cassettes are the exception in one direction only: their shapes were confirmed
against live `gemini-3.6-flash` responses from this machine — which is where the
`thoughtsTokenCount` in `gemini/complete_json.json` comes from. A real answer of 61 tokens
carried 1,844 reasoning tokens, and counting only `candidatesTokenCount` under-reports the
cost of a thinking model by thirty times. The content is still synthetic.

What the tests do with them:

- `body` is fed to the shipped adapter's `parse_response`, so a parser change that would
  break on a real answer breaks here.
- `status` and `body` go through the shipped error mapping, so 401/429/5xx keep meaning
  auth / retryable / retryable.
- `_request` records the shape the adapter is asserted to *send*.

No file contains a key: every credential reads `<redacted>`.
