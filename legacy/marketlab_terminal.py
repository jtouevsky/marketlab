"""
MarketLab v0.1 — the original terminal version (kept for reference).

Run it with:   python3 legacy/marketlab_terminal.py   (needs: pip install rich)

This file handles everything the USER sees (prompts, colors, layout).
All data fetching lives in data.py.
"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "app"))   # the app's modules live in app/

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from data import DataSourceError, TickerNotFoundError, clean_ticker
from tab_overview import get_company_info

# One shared Console object prints everything (with colors) to the terminal.
console = Console()

ACCENT = "bright_cyan"
NA = "[dim]N/A[/dim]"  # shown whenever a value is missing


# --- Formatting helpers ------------------------------------------------------
# Small functions that turn raw numbers into human-friendly text.

def format_big_number(value):
    """3_450_000_000_000 -> '$3.45T'."""
    if value is None:
        return NA
    for limit, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M")]:
        if abs(value) >= limit:
            return f"${value / limit:,.2f}{suffix}"
    return f"${value:,.0f}"


def format_change(change, change_pct):
    """Green ▲ for up days, red ▼ for down days."""
    if change is None:
        return Text("N/A", style="dim")
    color, arrow = ("green", "▲") if change >= 0 else ("red", "▼")
    return Text(f"{arrow} {change:+,.2f}  ({change_pct:+.2f}%)  today", style=f"bold {color}")


def value_or_na(value):
    return str(value) if value else NA


# --- Screens -----------------------------------------------------------------

def show_header():
    logo = Text("◆ MARKET", style=f"bold {ACCENT}") + Text("LAB", style="bold white")
    tagline = Text("terminal equity research  ·  v0.1", style="dim")
    console.print(
        Panel(
            Align.center(Group(Align.center(logo), Align.center(tagline))),
            box=box.ROUNDED,
            border_style=ACCENT,
            padding=(1, 2),
        )
    )
    console.print(Align.center(Text("Type a ticker like AAPL, NVDA, AMZN · 'q' to quit", style="dim")))
    console.print()


def show_basic_info(info):
    # Line 1: company name + ticker/exchange badge
    title = Text(info["name"], style="bold white")
    title.append(f"   {info['ticker']}", style=f"bold {ACCENT}")
    if info["exchange"]:
        title.append(f" · {info['exchange']}", style="dim")

    # Line 2: big price + daily change
    currency = info["currency"] or ""
    price_line = Text(f"{info['price']:,.2f} ", style="bold white")
    price_line.append(f"{currency}   ", style="dim")
    price_line.append_text(format_change(info["change"], info["change_pct"]))

    # Two-column grid of label / value pairs
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="dim", justify="right")
    grid.add_column(style="bold")
    grid.add_row("MARKET CAP", format_big_number(info["market_cap"]))
    grid.add_row("SECTOR", value_or_na(info["sector"]))
    grid.add_row("INDUSTRY", value_or_na(info["industry"]))

    console.print(
        Panel(
            Group(title, price_line, Text(""), grid),
            title=f"[{ACCENT}]01 · OVERVIEW[/]",
            title_align="left",
            box=box.ROUNDED,
            border_style="grey39",
            padding=(1, 3),
        )
    )
    console.print()


def show_error(message):
    console.print(Panel(Text(message, style="white"), title="[red]✖ ERROR[/]",
                        title_align="left", border_style="red", padding=(0, 2)))
    console.print()


# --- Main loop ---------------------------------------------------------------

def main():
    console.clear()
    show_header()

    while True:  # keep asking until the user quits
        raw = console.input(f"[bold {ACCENT}]›[/] Enter stock ticker: ")

        if raw.strip().lower() in ("q", "quit", "exit"):
            break

        # 1) Validate the text itself (no internet needed)
        try:
            ticker = clean_ticker(raw)
        except ValueError as error:
            show_error(str(error))
            continue  # jump back to the top of the loop

        # 2) Fetch data (shows a spinner while waiting on the network)
        try:
            with console.status(f"[{ACCENT}]Fetching {ticker}…", spinner="dots"):
                info = get_company_info(ticker)
        except TickerNotFoundError:
            show_error(f"No company found for '{ticker}'. Check the spelling and try again.")
            continue
        except DataSourceError:
            show_error("Couldn't reach Yahoo Finance. Check your internet connection and try again.")
            continue

        # 3) Display it
        console.print()
        show_basic_info(info)

    console.print(f"\n[{ACCENT}]◆[/] Thanks for using MarketLab.\n")


# This line means: only run main() when this file is executed directly
# (python3 legacy/marketlab_terminal.py   (needs: pip install rich)), not when another file imports it.
if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):  # user pressed Ctrl+C / Ctrl+D
        console.print(f"\n[{ACCENT}]◆[/] Goodbye.\n")
