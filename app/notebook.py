"""
notebook.py — the research notebook: saved experiments you can reopen,
edit, re-run, duplicate and fork.

Storage: one JSON file, notebook/experiments.json, next to the app. It is
plain text on purpose: you can read it, back it up, or put it in git.

An entry:
{
  "id": "e_20261001_ab12cd",
  "name": "NVDA after 5% drops",
  "hypothesis": "Sharp weekly drops in NVDA tend to recover within two weeks",
  "notes": "",
  "definition": { ...ExperimentDefinition (experiments.py)... },
  "summary":    { n, mean, median, ... }   compact numbers from the last run
  "challenge":  {survived: [...], caution: [...]} from "Challenge this result", if run
  "parent_id":  "e_..." or null            the entry this was forked from
  "version":    3                          bumped whenever the definition or results change
  "versions":   [ {version, definition, summary, challenge, saved_at}, ... ]   older states, newest last
  "created_at", "updated_at", "last_run_at"
}

VERSIONS: "Update existing" never silently overwrites research. The previous
definition and results are pushed onto `versions` first, so every earlier
state can be reopened. "Save as new" is a fork.

A FORK is a new entry that remembers its parent, so you can trace how an
idea evolved ("NVDA -5% → added volume filter → tried AMD"). A DUPLICATE is
a fork with the same definition. Deleting an entry keeps its children; they
just point at a parent that no longer exists (shown as "parent deleted").
"""

import json
import secrets
import threading
from datetime import datetime

import experiments
from config import BASE_DIR
from data import now_iso

NOTEBOOK_DIR = BASE_DIR / "notebook"
NOTEBOOK_FILE = NOTEBOOK_DIR / "experiments.json"
_lock = threading.Lock()
MAX_TEXT = 4000


class NotebookError(ValueError):
    """Bad input or unknown entry."""


def _load():
    if not NOTEBOOK_FILE.exists():
        return []
    try:
        return json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8")).get("experiments", [])
    except (OSError, ValueError):
        # Never silently overwrite a file we couldn't read: keep a copy first.
        backup = NOTEBOOK_FILE.with_suffix(f".unreadable-{datetime.now():%Y%m%d%H%M%S}.json")
        NOTEBOOK_FILE.rename(backup)
        return []


def _save(entries):
    NOTEBOOK_DIR.mkdir(exist_ok=True)
    temp = NOTEBOOK_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "experiments": entries}, indent=2), encoding="utf-8")
    temp.replace(NOTEBOOK_FILE)                     # atomic: never a half-written file


def _text(value, limit=MAX_TEXT):
    return str(value or "").strip()[:limit]


def _find(entries, entry_id):
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    raise NotebookError("That notebook entry no longer exists.")


def _new_id():
    return f"e_{datetime.now():%Y%m%d}_{secrets.token_hex(3)}"


def list_entries():
    with _lock:
        entries = _load()
    ids = {e["id"] for e in entries}
    for e in entries:
        e["children"] = [c["id"] for c in entries if c.get("parent_id") == e["id"]]
        e["parent_missing"] = bool(e.get("parent_id")) and e["parent_id"] not in ids
    return sorted(entries, key=lambda e: e["updated_at"], reverse=True)


def get(entry_id):
    return _find(list_entries(), entry_id)


def create(body, parent_id=None):
    """Save a new entry. body: {name, hypothesis, notes, definition, summary?}"""
    definition = experiments.normalize(body.get("definition") or {})
    now = now_iso()
    entry = {
        "id": _new_id(),
        "name": _text(body.get("name"), 120) or _default_name(definition),
        "hypothesis": _text(body.get("hypothesis")),
        "notes": _text(body.get("notes")),
        "definition": definition,
        "summary": body.get("summary") if isinstance(body.get("summary"), dict) else None,
        "challenge": body.get("challenge") if isinstance(body.get("challenge"), dict) else None,
        "parent_id": parent_id,
        "version": 1,
        "versions": [],
        "created_at": now, "updated_at": now,
        "last_run_at": (body.get("summary") or {}).get("ran_at") if isinstance(body.get("summary"), dict) else None,
    }
    with _lock:
        entries = _load()
        if parent_id:
            _find(entries, parent_id)
        entries.append(entry)
        _save(entries)
    return entry


MAX_VERSIONS = 30


def _snapshot(entry):
    return {"version": entry.get("version", 1), "definition": entry["definition"], "summary": entry.get("summary"),
            "challenge": entry.get("challenge"), "saved_at": entry["updated_at"]}


def update(entry_id, body):
    """
    Edit name / hypothesis / notes, or record a new state (definition and/or results).
    A new state pushes the old one onto `versions` first; nothing is lost.
    """
    with _lock:
        entries = _load()
        entry = _find(entries, entry_id)
        entry.setdefault("versions", [])
        entry.setdefault("version", 1)
        for field, limit in (("name", 120), ("hypothesis", MAX_TEXT), ("notes", MAX_TEXT)):
            if field in body:
                entry[field] = _text(body[field], limit)
        new_definition = experiments.normalize(body["definition"]) if "definition" in body else entry["definition"]
        definition_changed = new_definition != experiments.normalize(entry["definition"])
        new_summary = body.get("summary") if isinstance(body.get("summary"), dict) else None
        if definition_changed or new_summary is not None:
            if entry.get("summary") is not None or definition_changed:
                entry["versions"] = (entry["versions"] + [_snapshot(entry)])[-MAX_VERSIONS:]
                entry["version"] += 1
            entry["definition"] = new_definition
            entry["summary"] = new_summary if new_summary is not None else None
            entry["challenge"] = body.get("challenge") if isinstance(body.get("challenge"), dict) else None
            if new_summary is not None:
                entry["last_run_at"] = new_summary.get("ran_at")
        elif isinstance(body.get("challenge"), dict):
            entry["challenge"] = body["challenge"]
        entry["updated_at"] = now_iso()
        _save(entries)
    return entry


def fork(entry_id, body=None):
    """New entry derived from an existing one (body may override name / definition)."""
    body = body or {}
    with _lock:
        parent = dict(_find(_load(), entry_id))
    return create({
        "name": body.get("name") or f"{parent['name']} (fork)",
        "hypothesis": body.get("hypothesis", parent["hypothesis"]),
        "notes": body.get("notes", ""),
        "definition": body.get("definition") or parent["definition"],
        "summary": body.get("summary") if body.get("definition") else parent.get("summary"),
        "challenge": body.get("challenge") if body.get("definition") else parent.get("challenge"),
    }, parent_id=entry_id)


def rerun(entry_id):
    """Run the saved definition on today's data and store the fresh summary."""
    with _lock:
        definition = _find(_load(), entry_id)["definition"]
    result = experiments.run(definition)
    entry = update(entry_id, {"summary": experiments.summary_for_notebook(result)})   # previous results kept as a version
    return {"entry": entry, "result": result}


def delete(entry_id):
    with _lock:
        entries = _load()
        _find(entries, entry_id)
        _save([e for e in entries if e["id"] != entry_id])
    return {"deleted": entry_id}


def _default_name(definition):
    symbol = definition["instrument"]["symbol"]
    labels = {"price_move": lambda p: f"{'−' if p['direction'] == 'falls' else '+'}{p['threshold']:g}% in {p['window']}d",
              "relative_volume": lambda p: f"vol {'>' if p['op'] == 'above' else '<'}{p['multiple']:g}×",
              "rsi": lambda p: f"RSI {'<' if p['op'] == 'below' else '>'}{p['level']:g}",
              "ma_position": lambda p: f"{'>' if p['op'] == 'above' else '<'}{p['period']}d MA",
              "ma_cross": lambda p: f"cross {p['op']} {p['period']}d MA",
              "earnings": lambda p: "after earnings"}
    parts = [labels.get(c["type"], lambda p: c["type"])(c["params"]) for c in definition["conditions"]]
    return f"{symbol} {' + '.join(parts)} → {definition['outcome']['horizon']}d"
