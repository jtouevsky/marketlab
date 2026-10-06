"""
providers.py — where intraday candles come from. The engine never imports a
provider directly; it receives normalized rows through this interface.

IntradayDataProvider
    provider_name()                     "Yahoo Finance"
    supported_intervals()               ["1m", "5m", ...]   (what this source can deliver)
    history_available(interval)         {"days": 29, "note": "..."}
    is_realtime()                       True only for genuine real-time feeds
    data_delay()                        seconds of exchange delay (None if unknown)
    get_historical_bars(symbol, interval, start=None, end=None)   -> [normalized rows]
    get_latest_bars(symbol, interval, n=500)                      -> [normalized rows]
    subscribe_live(symbol, interval, callback)                    optional; raises NotSupported

Normalized row: {timestamp, open, high, low, close, volume, symbol, interval, provider, contract}
(timestamp = bar OPEN time in UTC seconds).

Providers here:
    YahooProvider       free, CME futures delayed 10 minutes (Yahoo's published delay),
                        1m for ~30 days, 5m/15m/30m ~60 days, 1h ~2 years. Both the continuous
                        front month (NQ=F, no contract label) and each listed quarterly
                        contract (NQZ26.CME) while it trades.
    FirstRateSampleProvider  FirstRate Data's free public sample: ~2 recent weeks, 1m/5m/1h,
                        unadjusted front month (private use, attribution "FirstRate Data").
    AutoProvider (composite.py)  MarketLab's own research series from all of the above.
    LocalFileProvider   CSV files you place in data/intraday/ (e.g. exports from a paid
                        vendor later, including second-level data). Nothing is fetched.

Yahoo candles are also saved to a local archive (cache/intraday/), so history you
have already downloaded is kept even after it falls out of Yahoo's 30-day window.
"""

import csv
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import BASE_DIR, CACHE_DIR

from .bars import INTERVAL_SECONDS, normalize_row

ARCHIVE_DIR = CACHE_DIR / "intraday"
LOCAL_DIR = BASE_DIR / "data" / "intraday"


class NotSupported(Exception):
    pass


class ProviderError(Exception):
    pass


class IntradayDataProvider:
    key = "base"

    def provider_name(self):
        raise NotImplementedError

    def supported_intervals(self):
        raise NotImplementedError

    def history_available(self, interval):
        raise NotImplementedError

    def is_realtime(self):
        return False

    def data_delay(self):
        return None

    def get_historical_bars(self, symbol, interval, start=None, end=None):
        raise NotImplementedError

    def get_latest_bars(self, symbol, interval, n=500):
        rows = self.get_historical_bars(symbol, interval)
        return rows[-n:]

    def subscribe_live(self, symbol, interval, callback):
        raise NotSupported(f"{self.provider_name()} has no live stream.")

    def describe(self):
        return {"key": self.key, "name": self.provider_name(), "realtime": self.is_realtime(),
                "delay_seconds": self.data_delay(), "intervals": self.supported_intervals(),
                "history": {iv: self.history_available(iv) for iv in self.supported_intervals()}}


# --- local archive + catalog ---------------------------------------------------------
# Every download is merged into a JSON archive per (source, symbol or contract, interval), so history
# that has fallen out of a provider's window is kept, and unchanged history is never downloaded again.
# catalog.json describes each archive: provider, instrument, contract, resolution, first/last candle,
# download time and the latest quality summary.
_archive_lock = threading.RLock()
CATALOG = ARCHIVE_DIR / "catalog.json"


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path)


def catalog():
    return _read_json(CATALOG, {})


def record_catalog(name, **fields):
    with _archive_lock:
        cat = catalog()
        cat[name] = {**cat.get(name, {}), **fields}
        _write_json(CATALOG, cat)


class ArchivedProvider(IntradayDataProvider):
    """Shared archive handling: load, merge new rows (newer copies win), save, describe in the catalog."""

    def _archive_name(self, symbol, interval):
        return f"{self.key}_{symbol}_{interval}"

    def _load_archive(self, symbol, interval):
        return _read_json(ARCHIVE_DIR / f"{self._archive_name(symbol, interval)}.json", {"rows": {}, "fetched_at": 0})

    def _merge(self, symbol, interval, archive, fresh, contract=None):
        step = INTERVAL_SECONDS[interval]
        cutoff = time.time() - step                    # the newest candle may still be forming
        added = 0
        for row in fresh:
            if row["timestamp"] + step <= cutoff:
                key = str(row["timestamp"])
                added += key not in archive["rows"]
                archive["rows"][key] = row
        archive["fetched_at"] = time.time()
        archive["raw_count"] = len(fresh)
        name = self._archive_name(symbol, interval)
        _write_json(ARCHIVE_DIR / f"{name}.json", archive)
        stamps = [int(k) for k in archive["rows"]]
        record_catalog(name, provider=self.provider_name(), provider_key=self.key, instrument=symbol,
                       contract=contract, resolution=interval, first=min(stamps) if stamps else None,
                       last=max(stamps) if stamps else None, bars=len(stamps), downloaded_at=archive["fetched_at"],
                       last_download_rows=len(fresh), last_download_new=added)
        return archive

    def _rows(self, symbol, interval, archive, start, end, contract=None):
        out = []
        for key in sorted(archive["rows"], key=int):   # MarketLab's own storage, kept in time order
            row = normalize_row(archive["rows"][key], symbol, interval, self.provider_name(), contract=contract)
            if row and (start is None or row["timestamp"] >= start) and (end is None or row["timestamp"] < end):
                row["source"] = self.key
                out.append(row)
        return out

    def fetched_at(self, symbol, interval):
        return self._load_archive(symbol, interval).get("fetched_at") or None


# --- Yahoo ---------------------------------------------------------------------------
YAHOO_SYMBOLS = {"NQ": "NQ=F", "MNQ": "MNQ=F", "ES": "ES=F", "MES": "MES=F", "RTY": "RTY=F", "M2K": "M2K=F", "YM": "YM=F", "MYM": "MYM=F"}
YAHOO_HISTORY_DAYS = {"1m": 29, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "1h": 729, "1d": 9000}
CHUNK_DAYS = {"1m": 7}
CONTRACT_RE = re.compile(r"^(NQ|MNQ|ES|MES|RTY|M2K|YM|MYM)[HMUZ]\d{2}$")
CBOT_ROOTS = ("YM", "MYM")


def yahoo_ticker(symbol):
    """NQ → NQ=F (continuous); NQZ26 → NQZ26.CME (one specific contract)."""
    if symbol in YAHOO_SYMBOLS:
        return YAHOO_SYMBOLS[symbol]
    if CONTRACT_RE.match(symbol):
        return f"{symbol}.CBT" if symbol[:-3] in CBOT_ROOTS else f"{symbol}.CME"
    return symbol


class YahooProvider(ArchivedProvider):
    """
    Two kinds of Yahoo series, both free and anonymous:
        NQ=F        Yahoo's continuous front month (unadjusted; Yahoo picks the roll; no contract label)
        NQZ26.CME   one specific quarterly contract (only while it is listed: expired contracts disappear,
                    which is why MarketLab archives them while they can still be downloaded)
    """
    key = "yahoo"

    def provider_name(self):
        return "Yahoo Finance"

    def supported_intervals(self):
        return list(YAHOO_HISTORY_DAYS)

    def history_available(self, interval):
        days = YAHOO_HISTORY_DAYS.get(interval)
        return {"days": days, "note": f"Yahoo keeps about {days} days of {interval} candles" if days and days < 9000
                else "Decades of daily candles"}

    def data_delay(self):
        return 600      # Yahoo lists CME futures as delayed 10 minutes

    def _archive_name(self, symbol, interval):
        return f"yahoo_{symbol}_{interval}"

    def _download(self, symbol, interval, since=None):
        import yfinance as yf
        ticker = yf.Ticker(yahoo_ticker(symbol))
        days = YAHOO_HISTORY_DAYS.get(interval, 59)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        if since:                     # incremental: only what is new (with a 6-hour overlap to pick up corrections)
            start = max(start, datetime.fromtimestamp(since, timezone.utc) - timedelta(hours=6))
        frames = []
        if interval == "1d" and not since:
            frames.append(ticker.history(period="max", interval=interval, prepost=True, auto_adjust=False))
        else:
            chunk = timedelta(days=CHUNK_DAYS.get(interval, days + 1))
            while start < now:
                end = min(start + chunk, now)
                frames.append(ticker.history(start=start, end=end, interval=interval, prepost=True, auto_adjust=False))
                start = end
        rows = []
        for frame in frames:
            if frame is None or frame.empty:
                continue
            for stamp, row in frame.iterrows():
                rows.append({"timestamp": int(stamp.timestamp()), "open": row.get("Open"), "high": row.get("High"),
                             "low": row.get("Low"), "close": row.get("Close"), "volume": row.get("Volume")})
        return rows

    def get_historical_bars(self, symbol, interval, start=None, end=None, refresh=False, max_age=300):
        if interval not in YAHOO_HISTORY_DAYS:
            raise NotSupported(f"Yahoo Finance doesn't provide {interval} candles.")
        contract = symbol if CONTRACT_RE.match(symbol) else None
        with _archive_lock:
            archive = self._load_archive(symbol, interval)
            if contract and not archive["rows"] and time.time() - archive.get("missing_at", 0) < 86400:
                raise ProviderError(f"Yahoo Finance has no {symbol} {interval} candles (checked within the last day).")
            if refresh or time.time() - archive.get("fetched_at", 0) > max_age:
                last = max((int(k) for k in archive["rows"]), default=None)
                try:
                    fresh = self._download(symbol, interval, since=last)
                except Exception as error:          # network problems: keep what we have
                    if not archive["rows"]:
                        raise ProviderError(f"Couldn't download {symbol} {interval} candles from Yahoo Finance.") from error
                    fresh = []
                if fresh:
                    archive = self._merge(symbol, interval, archive, fresh, contract)
                elif not archive["rows"]:
                    if contract:              # remember the miss for a day: expired contracts don't come back
                        _write_json(ARCHIVE_DIR / f"{self._archive_name(symbol, interval)}.json", {**archive, "missing_at": time.time()})
                    raise ProviderError(f"Yahoo Finance has no {symbol} {interval} candles"
                                        + (" (expired contracts are removed by Yahoo)." if contract else "."))
        return self._rows(symbol, interval, archive, start, end, contract)


# --- FirstRate Data public sample --------------------------------------------------------
FRD_URL = "https://frd001.s3.us-east-2.amazonaws.com/frd_sample_futures_{symbol}.zip"
FRD_FILES = {"1m": "{symbol}_1min_sample.csv", "5m": "{symbol}_5min_sample.csv", "1h": "{symbol}_1hour_sample.csv"}
FRD_SYMBOLS = ("NQ", "MNQ", "ES", "YM")


class FirstRateSampleProvider(ArchivedProvider):
    """
    FirstRate Data publishes a free sample of its futures data (no account, no key): roughly the latest
    two weeks of unadjusted front-month candles, stamped in US Eastern time at the candle START,
    zero-volume minutes omitted. Licence: private use, attribution "FirstRate Data".
    MarketLab uses it as an independent second source (cross-check + filling minutes Yahoo lacks).
    """
    key = "firstrate"

    def provider_name(self):
        return "FirstRate Data (free sample)"

    def supported_intervals(self):
        return list(FRD_FILES)

    def history_available(self, interval):
        return {"days": 14, "note": "The public sample covers about the latest two weeks; MarketLab keeps every sample it downloads."}

    def get_historical_bars(self, symbol, interval, start=None, end=None, refresh=False, max_age=12 * 3600):
        if symbol not in FRD_SYMBOLS or interval not in FRD_FILES:
            raise NotSupported(f"The FirstRate sample has no {symbol} {interval} candles.")
        with _archive_lock:
            archive = self._load_archive(symbol, interval)
            if refresh or time.time() - archive.get("fetched_at", 0) > max_age:
                try:
                    fresh = self._download(symbol, interval)
                except Exception as error:
                    if not archive["rows"]:
                        raise ProviderError("Couldn't download the FirstRate Data sample.") from error
                    fresh = []
                if fresh:
                    archive = self._merge(symbol, interval, archive, fresh)
        return self._rows(symbol, interval, archive, start, end)

    def _download(self, symbol, interval):
        import io
        import urllib.request
        import zipfile
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        with urllib.request.urlopen(FRD_URL.format(symbol=symbol), timeout=30) as response:
            blob = response.read()
        rows = []
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            with zf.open(FRD_FILES[interval].format(symbol=symbol)) as handle:
                for raw in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")):
                    try:
                        stamp = datetime.fromisoformat(raw["timestamp"]).replace(tzinfo=et)
                    except (KeyError, ValueError):
                        continue
                    rows.append({"timestamp": int(stamp.timestamp()), "open": raw.get("open"), "high": raw.get("high"),
                                 "low": raw.get("low"), "close": raw.get("close"), "volume": raw.get("volume")})
        return rows


# --- Local CSV files ------------------------------------------------------------------
class LocalFileProvider(IntradayDataProvider):
    """
    data/intraday/<SYMBOL>_<interval>.csv with a header row:
        timestamp,open,high,low,close,volume[,contract]
    timestamp: Unix seconds (UTC) or ISO 8601 with a timezone (e.g. 2026-09-29T13:30:00Z).
    """
    key = "local"

    def provider_name(self):
        return "Local files"

    def _files(self):
        return sorted(LOCAL_DIR.glob("*_*.csv")) if LOCAL_DIR.exists() else []

    def supported_intervals(self):
        return sorted({p.stem.rsplit("_", 1)[1] for p in self._files() if p.stem.rsplit("_", 1)[1] in INTERVAL_SECONDS},
                      key=lambda iv: INTERVAL_SECONDS[iv])

    def history_available(self, interval):
        return {"days": None, "note": "Whatever the files contain"}

    def symbols(self):
        return sorted({p.stem.rsplit("_", 1)[0].upper() for p in self._files()})

    def get_historical_bars(self, symbol, interval, start=None, end=None, **_):
        path = LOCAL_DIR / f"{symbol}_{interval}.csv"
        if not path.exists():
            raise NotSupported(f"No local file {path.name}.")
        out = []
        with path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                stamp = (raw.get("timestamp") or "").strip()
                try:
                    ts = int(float(stamp))
                except ValueError:
                    try:
                        ts = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
                    except ValueError:
                        continue
                row = normalize_row({**raw, "timestamp": ts}, symbol, interval, self.provider_name())
                if row:
                    row["source"] = self.key
                if row and (start is None or ts >= start) and (end is None or ts < end):
                    out.append(row)
        return out


PROVIDERS = {"yahoo": YahooProvider(), "firstrate": FirstRateSampleProvider(), "local": LocalFileProvider()}


def get_provider(key):
    if key not in PROVIDERS:
        raise ProviderError(f"Unknown data provider {key!r}.")
    return PROVIDERS[key]


LIVE_SOURCES_NOTE = (
    "No legitimate $0 real-time or tick NQ/MNQ source without an account was found (checked October 2026). "
    "Yahoo Finance is delayed 10 minutes for CME futures; the FirstRate Data sample is end-of-day. "
    "Real-time CME data needs a licensed feed (e.g. Databento live, a broker market-data subscription). "
    "subscribe_live() is part of the interface, so a real-time provider can be added without changing the engine."
)
