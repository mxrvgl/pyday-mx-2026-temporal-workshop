"""`make check-key`: one tiny call to Claude with the key and model the support agent will use."""

import os
import sys

import anthropic

key, model = os.getenv("ANTHROPIC_API_KEY", ""), os.getenv("AGENT_MODEL", "claude-haiku-5-5")
if not key:
    sys.exit("✗ ANTHROPIC_API_KEY is not set: add it to .env (see .env.example)")
try:
    anthropic.Anthropic(api_key=key).messages.create(
        model=model, max_tokens=16, messages=[{"role": "user", "content": "Say ok."}]
    )
except anthropic.APIError as err:
    sys.exit(f"✗ Claude didn't answer: {type(err).__name__}: {err}")
print(f"✓ Claude answered ({model}). You're ready for Part 2")
