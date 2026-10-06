"""
sources/claude_code.py — Ask MarketLab through YOUR OWN Claude Code install.

Only sources/llm.py imports this file.

How it works, and why it's set up this way
------------------------------------------
Anthropic doesn't let third-party apps offer "Sign in with Claude" or handle
Claude account tokens. What its docs do allow is a person signing in to the
unmodified Claude Code program with their own subscription, and running it
from scripts in headless mode (`claude -p`).

So MarketLab never sees a Claude credential:
  * You sign in once, in Terminal, with Claude Code's own login
    (`claude auth login`). The login happens on Anthropic's page, and Claude
    Code stores the result itself (macOS Keychain).
  * For each question MarketLab starts `claude -p`, writes the question to it,
    and reads the answer back. No tokens are read, copied or stored here.
  * Built-in tools, MCP servers, skills and session history are all switched
    off, so Claude Code only answers text questions and can't touch files.
  * ANTHROPIC_API_KEY is removed from the child process's environment, so
    Claude Code always uses your subscription login and never quietly bills
    an API key instead.

This is meant for personal use on your own computer. Answers count toward your
plan's normal usage limits.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config


class ClaudeCodeError(Exception):
    """Claude Code couldn't answer (not installed, signed out, limit reached...)."""


# Places the official installers put the `claude` command, in case the shell
# that started MarketLab doesn't have them on PATH.
_FALLBACK_PATHS = [
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
]

_STATUS_TTL = 60          # seconds to trust a sign-in check
_ANSWER_TIMEOUT = 240     # seconds before a question is abandoned
_status_cache = {"at": 0.0, "value": None}
_status_lock = threading.Lock()

# An empty folder to run in, so Claude Code doesn't pick up a CLAUDE.md or
# project settings from whatever folder MarketLab was started in.
_WORKDIR = Path(tempfile.gettempdir()) / "marketlab-claude-code"


def find_binary():
    """Path to the `claude` command, or None if Claude Code isn't installed."""
    if config.CLAUDE_CODE_PATH:
        path = Path(config.CLAUDE_CODE_PATH).expanduser()
        return str(path) if path.is_file() else None
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _FALLBACK_PATHS:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _child_env():
    """MarketLab's environment minus any API credentials, so the subscription login is used."""
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    return env


def status(refresh=False):
    """
    Is Claude Code installed and signed in? Returns
      {"installed": bool, "signed_in": bool, "method": str|None, "error": str|None}
    This runs `claude auth status`, which reports sign-in state without
    printing any credential. Results are cached for a minute.
    """
    with _status_lock:
        cached = _status_cache["value"]
        if cached and not refresh and time.time() - _status_cache["at"] < _STATUS_TTL:
            return cached

        binary = find_binary()
        if not binary:
            value = {"installed": False, "signed_in": False, "method": None, "error": None}
        else:
            try:
                done = subprocess.run([binary, "auth", "status"], capture_output=True, text=True,
                                      timeout=20, env=_child_env(), stdin=subprocess.DEVNULL)
                try:
                    info = json.loads(done.stdout or "{}")
                except json.JSONDecodeError:
                    info = {}
                signed_in = done.returncode == 0 and info.get("loggedIn", True) is not False
                value = {"installed": True, "signed_in": signed_in,
                         "method": info.get("authMethod") if signed_in else None, "error": None}
            except (OSError, subprocess.TimeoutExpired) as error:
                value = {"installed": True, "signed_in": False, "method": None,
                         "error": f"Couldn't run Claude Code ({type(error).__name__})."}

        _status_cache.update(at=time.time(), value=value)
        return value


def _model_alias(model):
    """Claude Code accepts aliases (sonnet, opus, haiku); map MarketLab's model names onto them."""
    if config.CLAUDE_CODE_MODEL:
        return config.CLAUDE_CODE_MODEL
    name = (model or "").lower()
    for alias in ("haiku", "opus", "sonnet"):
        if alias in name:
            return alias
    return "sonnet"


def _prompt_text(messages):
    """Claude Code takes one prompt, so earlier turns are written out as a transcript."""
    *history, last = messages
    if not history:
        return last["content"]
    lines = ["Earlier in this conversation:"]
    for message in history:
        who = "User" if message["role"] == "user" else "You (the assistant)"
        lines.append(f"\n{who}:\n{message['content']}")
    lines.append(f"\n\nThe user's new message:\n{last['content']}")
    return "\n".join(lines)


def _friendly(text):
    """Turn Claude Code's error text into something a person can act on."""
    low = (text or "").lower()
    if "login" in low or "log in" in low or "authenticat" in low or "oauth" in low or "api key" in low:
        _status_cache["value"] = None
        return ClaudeCodeError("Claude Code isn't signed in. Run `claude auth login` in Terminal, "
                               "then click Check connection.")
    if "limit" in low or "rate" in low or "overloaded" in low or "usage" in low:
        return ClaudeCodeError("Your Claude plan's usage limit has been reached for now. Try again later.")
    short = " ".join((text or "").split())[:200]
    return ClaudeCodeError(f"Claude Code couldn't answer: {short}" if short else "Claude Code couldn't answer.")


def stream(system, messages, model):
    """Yield the answer piece by piece from `claude -p` (stream-json output)."""
    binary = find_binary()
    if not binary:
        raise ClaudeCodeError("Claude Code isn't installed on this computer.")

    _WORKDIR.mkdir(parents=True, exist_ok=True)
    # The system prompt (company data) can be large, so it goes in a private
    # temporary file rather than on the command line. It holds no credentials.
    fd, prompt_file = tempfile.mkstemp(prefix="marketlab-system-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(system)

    command = [
        binary, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--model", _model_alias(model),
        "--system-prompt-file", prompt_file,
        "--tools", "",                       # no file, shell or web tools
        "--disallowedTools", "mcp__*",       # no MCP tools either
        "--strict-mcp-config",               # don't load your MCP servers
        "--disable-slash-commands",          # no skills or commands
        "--no-session-persistence",          # don't save these chats to Claude Code history
        "--max-turns", "1",
    ]
    process = None
    timer = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   cwd=_WORKDIR, env=_child_env(), bufsize=1)
        timer = threading.Timer(_ANSWER_TIMEOUT, process.kill)
        timer.start()
        process.stdin.write(_prompt_text(messages))
        process.stdin.close()

        streamed = False
        final_text, error_text = "", ""
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "stream_event":
                inner = event.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    piece = delta.get("text") or ""
                    if piece:
                        streamed = True
                        yield piece
            elif kind == "result":
                if event.get("is_error") or (event.get("subtype") or "success") != "success":
                    error_text = str(event.get("result") or event.get("subtype") or "")
                else:
                    final_text = str(event.get("result") or "")

        process.wait(timeout=10)
        if error_text:
            raise _friendly(error_text)
        if process.returncode not in (0, None) and not streamed and not final_text:
            raise _friendly(process.stderr.read())
        if not streamed and final_text:
            yield final_text          # older versions without partial messages
    except OSError as error:
        raise ClaudeCodeError(f"Couldn't start Claude Code ({type(error).__name__}).") from error
    finally:
        if timer:
            timer.cancel()
        if process and process.poll() is None:   # the browser left mid-answer
            process.kill()
        try:
            os.remove(prompt_file)
        except OSError:
            pass
