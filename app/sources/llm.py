"""
sources/llm.py — the ONLY entry point to the AI. Nothing else in MarketLab
knows which provider answers.

Providers:
  anthropic_api  Claude through the Anthropic API (ANTHROPIC_API_KEY, billed per request).
  claude_code    Claude through your own signed-in Claude Code program, using your
                 Claude subscription (sources/claude_code.py; personal use).

complete()          small jobs (company-summary clean-up): API key only.
assistant_stream()  Ask MarketLab: whichever provider assistant_status() picks.
To add another provider (a local model, say), add a backend and a branch in
assistant_status() / assistant_stream().
"""

import anthropic

import config
from sources import claude_code


class LLMError(Exception):
    """The AI couldn't answer (no key, bad key, rate limit, network...)."""


_client = None


def is_configured():
    return bool(config.ANTHROPIC_API_KEY)


def _get_client():
    global _client
    if not is_configured():
        raise LLMError("AI is off. Add ANTHROPIC_API_KEY to your .env file to turn it on.")
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _friendly(error):
    """Turn an Anthropic SDK error into a message a person can act on."""
    if isinstance(error, anthropic.AuthenticationError):
        return LLMError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.")
    if isinstance(error, anthropic.RateLimitError):
        return LLMError("The AI is rate-limited right now. Wait a moment and try again.")
    if isinstance(error, anthropic.APIConnectionError):
        return LLMError("Couldn't reach the AI service. Check your internet connection.")
    if isinstance(error, anthropic.APIStatusError):
        return LLMError(f"The AI service returned an error ({error.status_code}).")
    return LLMError("The AI request failed.")


def complete(system, messages, model, max_tokens=600):
    """Ask once, get the whole answer back as text."""
    try:
        response = _get_client().messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        )
    except anthropic.AnthropicError as error:
        raise _friendly(error) from error
    return "".join(block.text for block in response.content if block.type == "text").strip()


def stream(system, messages, model, max_tokens=1200):
    """Ask once, get the answer piece by piece (so the UI can show it as it's written)."""
    try:
        with _get_client().messages.stream(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        ) as response:
            for text in response.text_stream:
                yield text
    except anthropic.AnthropicError as error:
        raise _friendly(error) from error


# --- Ask MarketLab: provider choice --------------------------------------------

PROVIDER_LABELS = {
    "claude_code": "Claude subscription (via Claude Code)",
    "anthropic_api": "Anthropic API key",
}


def assistant_status(refresh=False):
    """
    Which provider Ask MarketLab will use. Never contains a credential.
      {"enabled", "provider", "label", "setup", "preference", "claude_code": {...}}
    setup: None when ready, else "install" / "login" / "api_key" (what's missing).
    """
    preference = config.AI_PROVIDER if config.AI_PROVIDER in ("auto", "claude_code", "anthropic_api") else "auto"
    cc = claude_code.status(refresh) if preference != "anthropic_api" else None
    result = {"enabled": False, "provider": None, "label": None, "setup": None,
              "preference": preference, "claude_code": cc}

    if cc and cc["signed_in"]:
        result.update(enabled=True, provider="claude_code")
    elif preference != "claude_code" and is_configured():
        result.update(enabled=True, provider="anthropic_api")
    elif preference == "anthropic_api":
        result["setup"] = "api_key"
    else:
        result["setup"] = "login" if cc and cc["installed"] else "install"

    if result["provider"]:
        result["label"] = PROVIDER_LABELS[result["provider"]]
    return result


def assistant_stream(system, messages, model, max_tokens=1200):
    """Stream an Ask MarketLab answer from the active provider."""
    provider = assistant_status()["provider"]
    if provider == "claude_code":
        try:
            yield from claude_code.stream(system, messages, model)
        except claude_code.ClaudeCodeError as error:
            raise LLMError(str(error)) from error
    elif provider == "anthropic_api":
        yield from stream(system, messages, model, max_tokens)
    else:
        raise LLMError("Ask MarketLab isn't connected. Connect Claude first.")
