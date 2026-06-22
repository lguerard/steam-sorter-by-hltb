#!/usr/bin/env python3
"""Sort your Steam library by HowLongToBeat playtime."""

import sys
import urllib.request
from bs4 import BeautifulSoup
from howlongtobeatpy import HowLongToBeat
from rich.console import Console
from rich.table import Table
from rich.progress import track

console = Console()

PLAY_MODES = [
    ("Solo — Main Story",    "main_story"),
    ("Solo — Main + Extras", "main_extra"),
    ("Solo — Completionist", "completionist"),
    ("Solo — All Styles",    "all_styles"),
    ("Co-op",                "coop_time"),
    ("Multiplayer",          "mp_time"),
]


def ask(prompt: str, lo: int, hi: int) -> int:
    """Prompt until user enters an int in [lo, hi]."""
    while True:
        try:
            v = int(input(prompt))
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print(f"  Enter {lo}–{hi}")


def get_steam_games(username: str) -> list[str]:
    url = f"https://steamcommunity.com/id/{username}/games/?tab=all&xml=1"
    try:
        data = urllib.request.urlopen(url, timeout=15)
    except Exception as e:
        console.print(f"[red]Steam fetch failed: {e}[/red]")
        console.print("[yellow]Check username and that the profile is set to public.[/yellow]")
        sys.exit(1)
    soup = BeautifulSoup(data, features="xml")
    games = soup.find_all("game")
    if not games:
        console.print("[yellow]No games found. Profile may be private.[/yellow]")
        sys.exit(1)
    return [g.find("name").text for g in games]


def _pos(val) -> float | None:
    """Return val as float if positive, else None."""
    return float(val) if val and float(val) > 0 else None


def fetch_hltb_data(game_names: list[str]) -> list[dict]:
    results = []
    not_found: list[str] = []
    for name in track(game_names, description="Looking up HLTB..."):
        try:
            search = HowLongToBeat().search(name)
        except Exception:
            not_found.append(name)
            continue
        if not search:
            not_found.append(name)
            continue
        best = max(search, key=lambda e: e.similarity)
        results.append({
            "name":          best.game_name,
            "main_story":    _pos(best.main_story),
            "main_extra":    _pos(best.main_extra),
            "completionist": _pos(best.completionist),
            "all_styles":    _pos(best.all_styles),
            "coop_time":     _pos(best.coop_time),
            "mp_time":       _pos(best.mp_time),
            "score":         best.review_score,
            "year":          best.release_world,
        })
    if not_found:
        console.print(f"[dim]{len(not_found)} games not found on HLTB.[/dim]")
    return results


def pick_play_mode(games: list[dict]) -> tuple[str, str]:
    available = [
        (label, field) for label, field in PLAY_MODES
        if any(g[field] is not None for g in games)
    ]
    console.print("\n[bold]Play mode?[/bold]")
    for i, (label, _) in enumerate(available, 1):
        console.print(f"  {i}. {label}")
    idx = ask(f"Choose (1–{len(available)}): ", 1, len(available)) - 1
    return available[idx]


def display(games: list[dict], sort_label: str, sort_field: str, reverse: bool) -> None:
    has_data = sorted(
        [g for g in games if g[sort_field] is not None],
        key=lambda x: x[sort_field],
        reverse=reverse,
    )
    no_data = [g for g in games if g[sort_field] is None]

    def fmt(v) -> str:
        return f"{v:.1f}h" if v is not None else "[dim]-[/dim]"

    order_label = "longest" if reverse else "shortest"
    table = Table(
        title=f"Steam Library — {sort_label} ({order_label} first)",
        show_lines=False,
    )
    table.add_column("#",          style="dim",    width=4, justify="right")
    table.add_column("Game",       style="cyan",   min_width=30)
    table.add_column(sort_label,   justify="right", style="green")
    table.add_column("Score",      justify="right", style="yellow")
    table.add_column("Year",       justify="right", style="dim")

    for rank, g in enumerate(has_data, 1):
        score = str(g["score"]) if g["score"] else "-"
        year  = str(g["year"])  if g["year"]  else "-"
        table.add_row(str(rank), g["name"], fmt(g[sort_field]), score, year)

    if no_data:
        table.add_section()
        for g in no_data:
            score = str(g["score"]) if g["score"] else "-"
            year  = str(g["year"])  if g["year"]  else "-"
            table.add_row("-", g["name"], "-", score, year)

    console.print(table)
    console.print(
        f"\n[dim]{len(has_data)} games with {sort_label} data"
        + (f", {len(no_data)} without" if no_data else "")
        + ".[/dim]"
    )


def main() -> None:
    username = input("Steam username: ").strip()
    if not username:
        sys.exit(1)

    console.print(f"\n[dim]Fetching library for [bold]{username}[/bold]...[/dim]")
    game_names = get_steam_games(username)
    console.print(f"Found [bold]{len(game_names)}[/bold] games.\n")

    games = fetch_hltb_data(game_names)
    console.print(f"Matched [bold]{len(games)}[/bold] games on HowLongToBeat.")

    if not games:
        console.print("[red]No results.[/red]")
        sys.exit(1)

    sort_label, sort_field = pick_play_mode(games)

    console.print("\n[bold]Order?[/bold]")
    console.print("  1. Shortest first")
    console.print("  2. Longest first")
    order = ask("Choose (1–2): ", 1, 2)

    display(games, sort_label, sort_field, reverse=(order == 2))


if __name__ == "__main__":
    main()
