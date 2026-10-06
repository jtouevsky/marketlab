"""
MarketLab — a stock research workspace that runs in your browser.

Run it with:   python marketlab.py
It starts a small local web server and opens http://127.0.0.1:8050 for you.

How the pieces fit together:
    browser (static/*.js)  --asks for-->  /api/stock/AAPL/...         (this file)
    this file              --calls---->   tab_*.py / describe.py / assistant.py
    those                  --call------>  sources/yahoo.py, sources/sec.py, sources/llm.py
    this file              --returns-->   JSON (or streamed text) to the browser

Each piece of a ticker page has its own URL, so the page can load them in
parallel and show each one as soon as it's ready. If one source fails, the
others still work.
"""

import json
import logging
import threading
import webbrowser

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import assistant
import challenge
import concept_library
import config
import conditions
import describe
import experiments
import invest
import lab
import notebook
import research_projects
import risk_intel
from intraday import dataset as intraday_dataset, ladder as intraday_ladder, strategy as intraday_strategy
from intraday.providers import LIVE_SOURCES_NOTE, NotSupported, ProviderError, PROVIDERS as INTRADAY_PROVIDERS
import tab_chart
import tab_earnings
import tab_news
import tab_risk
from data import DataSourceError, TickerNotFoundError, clean_ticker
from sources import llm, logos, peers, search as search_source, sec
from tab_overview import get_company_info
from tab_profile import get_profile

PORT = 8050  # (5000 is avoided on purpose: macOS uses it for AirPlay)

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # keep Terminal quiet


def api_response(raw_ticker, build_function):
    """
    Shared error handling for every JSON route:
        validate ticker -> call build_function(ticker) -> return JSON.

    Status codes: 200 OK, 400 bad input, 404 no such company,
    503 a data source is unreachable, 500 a bug in our own code.
    """
    try:
        ticker = clean_ticker(raw_ticker)
    except ValueError as error:
        return jsonify(error=str(error)), 400

    try:
        return jsonify(build_function(ticker))
    except TickerNotFoundError:
        return jsonify(error=f"No company found for '{ticker}'."), 404
    except DataSourceError:
        return jsonify(error="Couldn't reach Yahoo Finance. Check your internet connection."), 503
    except sec.SecUnavailable as error:
        return jsonify(error=str(error)), 503
    except (lab.LabInputError, notebook.NotebookError) as error:
        return jsonify(error=str(error)), 400
    except Exception:
        app.logger.exception(f"Unexpected error while building data for {ticker}")
        return jsonify(error="Something went wrong building this view (details in Terminal)."), 500


# --- Pages & status ----------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/status")
def status():
    """Which optional sources are switched on (shown in the top bar)."""
    ai = llm.assistant_status()
    return jsonify(ai=ai["enabled"], ai_provider=ai["provider"], ai_label=ai["label"],
                   ai_summaries=llm.is_configured(), sec=sec.is_configured())


@app.route("/api/ai/status")
def ai_status():
    """Ask MarketLab connection state (used by "Check connection"). Holds no credentials."""
    return jsonify(llm.assistant_status(refresh=request.args.get("refresh") == "1"))


# --- Ticker data (one route per piece, loaded in parallel by the page) -------

@app.route("/api/stock/<raw_ticker>")
def overview(raw_ticker):
    return api_response(raw_ticker, get_company_info)


@app.route("/api/stock/<raw_ticker>/summary")
def summary(raw_ticker):
    return api_response(raw_ticker, describe.company_summary)


@app.route("/api/stock/<raw_ticker>/filings")
def filings(raw_ticker):
    return api_response(raw_ticker, sec.get_company_filings)


@app.route("/api/stock/<raw_ticker>/profile")
def profile(raw_ticker):
    return api_response(raw_ticker, get_profile)


@app.route("/api/stock/<raw_ticker>/chart")
def chart(raw_ticker):
    """Bars + overlays. ?range=1D|5D|1M|3M|6M|YTD|1Y|5Y|MAX"""
    return api_response(raw_ticker, lambda t: tab_chart.get_chart(t, request.args.get("range", "1Y")))


@app.route("/api/stock/<raw_ticker>/earnings")
def earnings(raw_ticker):
    return api_response(raw_ticker, tab_earnings.get_earnings)


@app.route("/api/stock/<raw_ticker>/news")
def news(raw_ticker):
    """Multi-source news events. ?refresh=1 skips the 15-minute provider cache."""
    refresh = request.args.get("refresh") == "1"
    return api_response(raw_ticker, lambda t: tab_news.build_news(t, refresh=refresh))


@app.route("/api/stock/<raw_ticker>/risk")
def risk(raw_ticker):
    """?period=full|5y&custom=<trading days>"""
    custom = request.args.get("custom", "")
    custom_days = int(custom) if custom.isdigit() else None
    return api_response(raw_ticker, lambda t: tab_risk.get_risk(t, request.args.get("period", "full"), custom_days))


# --- Search & logos ------------------------------------------------------------

@app.route("/api/search")
def search():
    """Company name or ticker -> matching securities (?q=nvid)."""
    return jsonify(search_source.search(request.args.get("q", "")[:60]))


@app.route("/api/logo/<raw_ticker>")
def logo(raw_ticker):
    """Company logo image, or 404 (the page then draws initials instead)."""
    try:
        ticker = clean_ticker(raw_ticker)
    except ValueError:
        return "", 404
    found = logos.get_logo(ticker)
    if not found:
        return Response(status=404, headers={"Cache-Control": "max-age=3600"})
    body, content_type = found
    return Response(body, mimetype=content_type, headers={"Cache-Control": "max-age=86400"})


# --- Strategy Lab ------------------------------------------------------------

@app.route("/api/lab/conditions")
def lab_conditions():
    """The condition registry: what can be tested, with parameter limits (builds the UI)."""
    return jsonify(conditions.describe_registry())


@app.route("/api/lab/experiment", methods=["POST"])
def lab_experiment():
    """Runs an ExperimentDefinition (JSON body). See experiments.py for the format."""
    definition = request.get_json(silent=True) or {}
    symbol = ((definition.get("instrument") or {}).get("symbol") or "")

    def build(ticker):
        definition["instrument"] = {**(definition.get("instrument") or {}), "symbol": ticker}
        result = experiments.run(definition)
        result["name"] = get_company_info(ticker)["name"]
        return result

    return api_response(symbol, build)


@app.route("/api/lab/run")
def lab_run():
    """
    Runs one Strategy Lab experiment, e.g.
    /api/lab/run?ticker=NVDA&direction=falls&threshold=5&window=5&forward=10
    """
    def build(ticker):
        args = request.args
        try:
            threshold = float(args.get("threshold", ""))
            window = int(args.get("window", ""))
            forward = int(args.get("forward", ""))
        except ValueError:
            raise lab.LabInputError("Threshold, window and forward period must be numbers.")
        result = lab.run_experiment(ticker, args.get("direction", ""), threshold, window, forward)
        result["name"] = get_company_info(ticker)["name"]
        return result

    return api_response(request.args.get("ticker", ""), build)


def _definition_route(build):
    """POST routes that take an ExperimentDefinition body."""
    definition = request.get_json(silent=True) or {}
    symbol = ((definition.get("instrument") or {}).get("symbol") or "")

    def run_it(ticker):
        definition["instrument"] = {**(definition.get("instrument") or {}), "symbol": ticker}
        return build(definition)
    return api_response(symbol, run_it)


@app.route("/api/lab/preview", methods=["POST"])
def lab_preview():
    """Qualifying days and episodes as each condition is added (sample-size preview)."""
    return _definition_route(experiments.preview)


@app.route("/api/lab/challenge", methods=["POST"])
def lab_challenge():
    """'Challenge this result': outliers, time split, recency, nearby parameters, summary."""
    return _definition_route(challenge.challenge)


@app.route("/api/lab/peers/<raw_ticker>")
def lab_peers(raw_ticker):
    """Suggested instruments for 'Test elsewhere', grouped by why they're suggested."""
    return api_response(raw_ticker, peers.suggestions)


# --- Notebook -----------------------------------------------------------------

def notebook_response(build):
    try:
        return jsonify(build())
    except (notebook.NotebookError, lab.LabInputError) as error:
        return jsonify(error=str(error)), 400
    except TickerNotFoundError as error:
        return jsonify(error=f"No company found for '{error}'."), 404
    except DataSourceError:
        return jsonify(error="Couldn't reach Yahoo Finance. Check your internet connection."), 503
    except Exception:
        app.logger.exception("Notebook error")
        return jsonify(error="Something went wrong in the notebook (details in Terminal)."), 500


@app.route("/api/notebook", methods=["GET", "POST"])
def notebook_list():
    if request.method == "POST":
        return notebook_response(lambda: notebook.create(request.get_json(silent=True) or {}))
    return notebook_response(lambda: {"experiments": notebook.list_entries()})


@app.route("/api/notebook/<entry_id>", methods=["GET", "PATCH", "DELETE"])
def notebook_entry(entry_id):
    if request.method == "PATCH":
        return notebook_response(lambda: notebook.update(entry_id, request.get_json(silent=True) or {}))
    if request.method == "DELETE":
        return notebook_response(lambda: notebook.delete(entry_id))
    return notebook_response(lambda: notebook.get(entry_id))


@app.route("/api/notebook/<entry_id>/fork", methods=["POST"])
def notebook_fork(entry_id):
    return notebook_response(lambda: notebook.fork(entry_id, request.get_json(silent=True) or {}))


@app.route("/api/notebook/<entry_id>/rerun", methods=["POST"])
def notebook_rerun(entry_id):
    return notebook_response(lambda: notebook.rerun(entry_id))


@app.route("/api/stock/<raw_ticker>/risk/intel")
def risk_intelligence(raw_ticker):
    """Dependencies & Exposures, Government & Policy, Controversies, Guidance (sourced; slower, loaded lazily)."""
    refresh = request.args.get("refresh") == "1"
    return api_response(raw_ticker, lambda ticker: risk_intel.build(ticker, refresh=refresh))


# --- Research Projects (intraday research notebooks) --------------------------

def research_response(build):
    try:
        return jsonify(build())
    except intraday_strategy.UntestableError as error:
        return jsonify(error=str(error), problems=error.problems), 400
    except (research_projects.ResearchError, intraday_strategy.StrategyError, NotSupported) as error:
        return jsonify(error=str(error)), 400
    except ProviderError as error:
        return jsonify(error=str(error)), 503
    except KeyError:
        return jsonify(error="Unknown concept."), 400
    except Exception:
        app.logger.exception("Research project error")
        return jsonify(error="Something went wrong in Research Projects (details in Terminal)."), 500


def _body():
    return request.get_json(silent=True) or {}


@app.route("/api/research/library")
def research_library():
    return research_response(lambda: {**concept_library.library(), **research_projects.meta(),
                                      "data_status": research_projects.DATA_STATUS})


@app.route("/api/research/analyze", methods=["POST"])
def research_analyze():
    return research_response(lambda: concept_library.analyze_observation(str(_body().get("text") or "")[:8000]))


@app.route("/api/research/explain", methods=["POST"])
def research_explain():
    body = _body()
    def build():
        concept = body.get("concept")
        if concept not in concept_library.CONCEPTS_BY_ID:
            raise research_projects.ResearchError("Unknown concept.")
        params = research_projects._params(concept, body.get("params") or {})
        return {"concept": concept, "params": params, "explanation": concept_library.explain(concept, params)}
    return research_response(build)


@app.route("/api/research/projects", methods=["GET", "POST"])
def research_list():
    if request.method == "POST":
        return research_response(lambda: research_projects.create(_body()))
    return research_response(lambda: {"projects": research_projects.list_projects()})


@app.route("/api/research/projects/<project_id>", methods=["GET", "PATCH", "DELETE"])
def research_project(project_id):
    if request.method == "PATCH":
        return research_response(lambda: research_projects.update(project_id, _body()))
    if request.method == "DELETE":
        return research_response(lambda: research_projects.delete(project_id))
    return research_response(lambda: research_projects.get(project_id))


@app.route("/api/research/projects/<project_id>/<action>", methods=["POST"])
def research_action(project_id, action):
    actions = {
        "fork": lambda: research_projects.fork(project_id, _body()),
        "revisions": lambda: research_projects.add_revision(project_id, _body().get("text")),
        "rescan": lambda: research_projects.rescan(project_id),
        "versions": lambda: research_projects.save_version(project_id, _body()),
        "describe": lambda: research_projects.describe(project_id, _body()),
        "answer": lambda: research_projects.answer(project_id, _body()),
        "reparse": lambda: research_projects.reparse(project_id),
        "mode": lambda: research_projects.set_mode(project_id, _body().get("mode")),
        "actual-trade": lambda: research_projects.set_actual_trade(project_id, _body()),
        "variation": lambda: research_projects.make_variation(project_id, _body()),
        "duplicate": lambda: research_projects.fork(project_id, {"name": _body().get("name"), "duplicate": True}),
    }
    if action not in actions:
        return jsonify(error="Unknown action."), 404
    return research_response(actions[action])


def _project_dataset(project, refresh=False):
    """The project's dataset. base "auto" = the coarsest resolution that still builds every timeframe it uses."""
    data = (project.get("settings") or {}).get("data") or {"provider": "auto", "base": "auto"}
    base, reason = data.get("base", "auto"), None
    if base == "auto":
        base, reason = intraday_dataset.auto_base(project)
    ds = intraday_dataset.build(project["instrument"]["symbol"], base, data.get("provider", "auto"), refresh=refresh)
    ds.base_reason = reason or f"Fixed in the project's data settings ({base})."
    return ds


@app.route("/api/intraday/providers")
def intraday_providers():
    return research_response(lambda: {"providers": [p.describe() for p in INTRADAY_PROVIDERS.values()],
                                      "live": LIVE_SOURCES_NOTE})


@app.route("/api/intraday/dataset")
def intraday_dataset_route():
    symbol = (request.args.get("symbol") or "NQ").upper()[:8]
    base = request.args.get("base") or "1m"
    provider = request.args.get("provider") or "yahoo"
    return research_response(lambda: intraday_dataset.inspector(
        intraday_dataset.build(symbol, base, provider, refresh=request.args.get("refresh") == "1")))


@app.route("/api/research/projects/<project_id>/ladder", methods=["POST"])
def research_ladder(project_id):
    """Exact / Very similar / Broader setup counts, with every relaxation spelled out, and why exact may be zero."""
    body = _body()

    def build():
        project = research_projects.raw(project_id)
        ds = _project_dataset(project, refresh=bool(body.get("refresh")))
        out = intraday_ladder.build(project, ds)
        out["search_log"] = research_projects.record_ladder(project_id, out["variants_counted"])
        out["dataset"] = intraday_dataset.inspector(ds)
        return out
    return research_response(build)


@app.route("/api/research/projects/<project_id>/test", methods=["POST"])
def research_test(project_id):
    """Paragraph → results in one call: ladder + the most specific tier with a usable sample, run and recorded."""
    body = _body()

    def build():
        project = research_projects.raw(project_id)
        ds = _project_dataset(project, refresh=bool(body.get("refresh")))
        ladder, result = intraday_ladder.auto_test(project, ds)
        ladder["search_log"] = research_projects.record_ladder(project_id, ladder["variants_counted"])
        ladder["dataset"] = intraday_dataset.inspector(ds)
        out = {"ladder": ladder, "result": None}
        if result:
            experiment, log = research_projects.record_experiment(project_id, result, None)
            out["result"] = {**result, "experiment": experiment, "search_log": log, "dataset": ladder["dataset"]}
        return out
    return research_response(build)


@app.route("/api/research/projects/<project_id>/run", methods=["POST"])
def research_run(project_id):
    body = _body()

    def build():
        project = research_projects.raw(project_id)
        ds = _project_dataset(project, refresh=bool(body.get("refresh")))
        relax = [str(x) for x in (body.get("relax_ids") or [])][:20]
        tier = body.get("tier") if body.get("tier") in ("exact", "similar", "broader") else "exact"
        if relax:
            result = intraday_ladder.run_tier(project, ds, relax, body.get("combo"))
        else:
            result = dict(intraday_strategy.run(project, ds, body.get("combo")))
            result["tier"] = {"relax_ids": [], "relaxations": []}
        result["tier"]["name"] = tier if relax else "exact"
        experiment, log = research_projects.record_experiment(project_id, result, body.get("combo"))
        return {**result, "experiment": experiment, "search_log": log,
                "dataset": intraday_dataset.inspector(ds)}
    return research_response(build)


@app.route("/api/research/projects/<project_id>/dataset")
def research_dataset(project_id):
    def build():
        project = research_projects.raw(project_id)
        return intraday_dataset.inspector(_project_dataset(project, refresh=request.args.get("refresh") == "1"))
    return research_response(build)


@app.route("/api/research/projects/<project_id>/why-not", methods=["POST"])
def research_why_not(project_id):
    """Was there a setup at this New York date/time? Condition by condition, with what was known then."""
    body = _body()

    def build():
        from datetime import datetime as _dt
        from intraday import sessions as _sessions
        try:
            day = _dt.strptime(str(body.get("date") or ""), "%Y-%m-%d").date()
            clock = _sessions.parse_clock(str(body.get("time") or ""))
        except ValueError as error:
            raise research_projects.ResearchError("Use a date like 2026-09-29 and a time like 09:47 (New York).") from error
        project = research_projects.raw(project_id)
        ds = _project_dataset(project)
        t = _sessions.from_et(day, clock)
        step = ds.series.step
        return intraday_strategy.why_not(project, ds, t - t % step)
    return research_response(build)


@app.route("/api/research/projects/<project_id>/compare", methods=["POST"])
def research_compare(project_id):
    """Run several versions on their own data and show their numbers side by side. No winner is declared."""
    body = _body()

    def build():
        import copy as _copy
        project = research_projects.raw(project_id)
        wanted = [v for v in (body.get("version_ids") or []) if isinstance(v, str)][:6]
        rows = []
        for vid in wanted:
            if vid == "current":
                snap, label = project, "Working copy"
            else:
                version = next((v for v in project["versions"] if v["id"] == vid), None)
                if not version:
                    continue
                snap, label = {**_copy.deepcopy(project), **_copy.deepcopy(version["snapshot"])}, version["label"]
            row = {"id": vid, "label": label}
            try:
                ds = _project_dataset(snap)
                result = intraday_strategy.run(snap, ds)
                m = result["primary"]["metrics"]
                row.update(ok=True, combo=result["primary"]["cell"]["label"], combos=result["combos"], setups=result["setups"],
                           n=m.get("n", 0), unit=m.get("unit"), win_rate=m.get("win_rate"), median=m.get("median"),
                           expectancy=m.get("expectancy"), max_drawdown=m.get("max_drawdown"), ci95=m.get("ci95"),
                           data=f"{ds.base} · {result['data']['trading_days']} days", rules_hash=result["rules_hash"])
            except (intraday_strategy.StrategyError, NotSupported, ProviderError) as error:
                row.update(ok=False, error=str(error))
            rows.append(row)
        log = project.get("search_log") or {}
        tested = len({x["rules_hash"] for x in project.get("experiments") or []})
        return {"rows": rows, "rule_sets_tested": tested, "variations": log.get("variations", 0),
                "note": (f"{tested} different rule sets have been tested in this project. The more variants are tried, "
                         "the more likely one looks good by chance; differences between small samples are usually noise.")}
    return research_response(build)


# --- Investment Lab -------------------------------------------------------------

def invest_response(build):
    try:
        return jsonify(build())
    except invest.InvestError as error:
        return jsonify(error=str(error)), 400
    except Exception:
        app.logger.exception("Investment Lab error")
        return jsonify(error="Something went wrong in the Investment Lab (details in Terminal)."), 500


@app.route("/api/invest/meta")
def invest_meta():
    return invest_response(invest.meta)


@app.route("/api/invest/parse", methods=["POST"])
def invest_parse():
    return invest_response(lambda: invest.parse_thesis(str(_body().get("text") or "")[:6000]))


@app.route("/api/invest/analyze", methods=["POST"])
def invest_analyze():
    return invest_response(lambda: invest.analyze(_body()))


@app.route("/api/invest/scenario", methods=["POST"])
def invest_scenario():
    body = _body()
    return invest_response(lambda: invest.custom_scenario(str(body.get("analysis_id") or ""), str(body.get("text") or "")[:400], body.get("inputs")))


@app.route("/api/invest/quick", methods=["POST"])
def invest_quick():
    return invest_response(lambda: invest.quick(_body()))


@app.route("/api/invest/correlation", methods=["POST"])
def invest_correlation():
    body = _body()
    return invest_response(lambda: invest.correlation_for(str(body.get("analysis_id") or ""), str(body.get("window") or "1y")))


@app.route("/api/invest/theses", methods=["GET", "POST"])
def invest_theses():
    if request.method == "POST":
        return invest_response(lambda: invest.save_thesis(_body()))
    return invest_response(lambda: {"theses": invest.list_theses()})


@app.route("/api/invest/theses/<tid>", methods=["DELETE"])
def invest_thesis_delete(tid):
    return invest_response(lambda: invest.delete_thesis(tid))


@app.route("/api/research/log-trade", methods=["POST"])
def research_log_trade():
    return research_response(lambda: research_projects.log_trade(_body()))


@app.route("/api/research/parse-trade", methods=["POST"])
def research_parse_trade():
    return research_response(lambda: research_projects.parse_trade_text(str(_body().get("text") or "")[:12000]))


@app.route("/api/research/personal-definitions", methods=["GET", "POST"])
def research_personal():
    if request.method == "POST":
        return research_response(lambda: research_projects.save_personal_definition(_body()))
    return research_response(lambda: {"personal": research_projects.personal_definitions()})


@app.route("/api/research/personal-definitions/<pid>", methods=["DELETE"])
def research_personal_delete(pid):
    return research_response(lambda: research_projects.delete_personal_definition(pid))


@app.route("/api/research/parse", methods=["POST"])
def research_parse_preview():
    """Preview Describe My Trade without saving anything."""
    import formalize
    body = _body()
    return research_response(lambda: formalize.parse(str(body.get("text") or "")[:12000], body.get("answers") or {},
                                                     research_projects.personal_definitions(), body.get("instrument")))


AI_RULES = ("You help a trader turn trading ideas into testable rules inside MarketLab. MarketLab's deterministic engine does "
            "ALL detection and backtesting. Never invent prices, dates, trade counts, win rates or any other result. Only "
            "discuss numbers that appear in the data given to you, and say so when something isn't known. No buy/sell advice.")


@app.route("/api/research/projects/<project_id>/ai", methods=["POST"])
def research_ai(project_id):
    """Optional AI help (Claude subscription or API key). Explains and rewords; never calculates."""
    body = _body()

    def build():
        status = llm.assistant_status()
        if not status["enabled"]:
            raise research_projects.ResearchError("AI help isn't connected (it's optional; everything else works without it).")
        project = research_projects.get(project_id)
        action = body.get("action")
        f = project.get("formalization") or {}
        if action == "wording":
            text = (project.get("trade_description") or {}).get("original") or project["observation"]["original"]
            prompt = ("Rewrite this trade description so every step is measurable with these MarketLab concepts: "
                      + ", ".join(c["label"] for c in concept_library.CONCEPTS) + ". Keep the trader's intent; don't add rules "
                      "they didn't state; where something is vague, write it as a question in brackets. Reply with the rewritten "
                      "description only.\n\nDescription:\n" + text)
        elif action == "ambiguity":
            q = next((a for a in f.get("ambiguities", []) if a["id"] == body.get("id")), None)
            if not q:
                raise research_projects.ResearchError("That question no longer exists.")
            prompt = (f"Explain in 3–5 short sentences how each option changes what gets detected, for this question about a trade "
                      f"description. Don't recommend one.\nQuestion: {q['question']}\nOptions: "
                      + "; ".join(o["label"] for o in q["options"]))
        elif action == "result":
            last = (project.get("experiments") or [None])[-1]
            if not last:
                raise research_projects.ResearchError("Run a test first.")
            prompt = ("Explain this backtest summary to the trader in plain words: what the numbers mean, what they can't show, "
                      "and how sample size limits conclusions. Use only these numbers.\nRecipe: "
                      + json.dumps(f.get("recipe") or [], default=str) + "\nLatest test: " + json.dumps(last, default=str))
        else:
            raise research_projects.ResearchError("Unknown AI action.")
        text = "".join(llm.assistant_stream(AI_RULES, [{"role": "user", "content": prompt}], config.AI_MODEL_FAST, max_tokens=700))
        return {"text": text.strip(), "provider": status["label"],
                "note": "Written by AI from the text above. It doesn't calculate anything; all numbers come from MarketLab's engine."}
    return research_response(build)


@app.route("/api/research/projects/<project_id>/replay", methods=["POST"])
def research_replay(project_id):
    body = _body()

    def build():
        project = research_projects.raw(project_id)
        ds = _project_dataset(project)
        relax = [str(x) for x in (body.get("relax_ids") or [])][:20]
        if relax:
            project = intraday_ladder.apply(project, relax)
        return intraday_strategy.replay(project, ds, int(body.get("setup_time") or 0), body.get("combo"))
    return research_response(build)


@app.route("/api/research/projects/<project_id>/versions/<version_id>/restore", methods=["POST"])
def research_restore(project_id, version_id):
    return research_response(lambda: research_projects.restore_version(project_id, version_id))


@app.route("/api/research/projects/<project_id>/items/<collection>", methods=["POST"])
def research_item_add(project_id, collection):
    return research_response(lambda: research_projects.add_item(project_id, collection, _body()))


@app.route("/api/research/projects/<project_id>/items/<collection>/<item_id>", methods=["PATCH", "DELETE"])
def research_item(project_id, collection, item_id):
    if request.method == "DELETE":
        return research_response(lambda: research_projects.delete_item(project_id, collection, item_id))
    return research_response(lambda: research_projects.update_item(project_id, collection, item_id, _body()))


# --- Ask MarketLab -----------------------------------------------------------

@app.route("/api/stock/<raw_ticker>/assistant")
def assistant_info(raw_ticker):
    """What the assistant can see for this ticker + suggested questions."""
    def build(ticker):
        context = assistant.get_context(ticker)
        ai = llm.assistant_status()
        return {
            "ticker": ticker,
            "name": context["name"],
            "ai_enabled": ai["enabled"],
            "ai": ai,
            "sources": context["sources"],
            "available": context["available"],
            "not_available": context["not_available"],
            "suggestions": assistant.suggested_questions(context),
            "built_at": context["built_at"],
        }
    return api_response(raw_ticker, build)


@app.route("/api/stock/<raw_ticker>/ask", methods=["POST"])
def ask(raw_ticker):
    """
    Streams the answer as plain text, piece by piece, so the browser can
    show it while it's being written. The browser sends the whole
    conversation each time; the server keeps no chat state.
    """
    try:
        ticker = clean_ticker(raw_ticker)
    except ValueError as error:
        return jsonify(error=str(error)), 400

    body = request.get_json(silent=True) or {}
    messages = assistant.clean_history(body.get("messages"))
    if not messages:
        return jsonify(error="Please type a question."), 400
    if not llm.assistant_status()["enabled"]:
        return jsonify(error="Ask MarketLab isn't connected. Open Ask MarketLab and follow Connect Claude."), 503

    try:
        context = assistant.get_context(ticker)
    except TickerNotFoundError:
        return jsonify(error=f"No company found for '{ticker}'."), 404
    except DataSourceError:
        return jsonify(error="Couldn't reach Yahoo Finance."), 503

    def generate():
        try:
            for piece in assistant.answer_stream(context, messages, body.get("focus")):
                yield piece
        except llm.LLMError as error:
            yield f"\n\n[[error]] {error}"   # the browser shows this as an error note

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Start the app -----------------------------------------------------------

if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n  ◆ MarketLab is running at {url}")
    ai = llm.assistant_status()
    print(f"    Ask MarketLab: {'on, ' + ai['label'] if ai['enabled'] else 'not connected (open Ask MarketLab → Connect Claude)'}")
    print(f"    SEC EDGAR:   {'on' if sec.is_configured() else 'off: add SEC_USER_AGENT to .env'}")
    print("  Leave this window open. Press Ctrl+C here to stop.\n")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
