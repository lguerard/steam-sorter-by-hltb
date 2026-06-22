#!/usr/bin/env python3
"""Sort your Steam library by HowLongToBeat and tag games in Steam."""

import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import vdf
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

# Time buckets used as Steam category names
BUCKETS = [
    (0,   5,   "HLTB: ≤5h"),
    (5,   15,  "HLTB: 5-15h"),
    (15,  30,  "HLTB: 15-30h"),
    (30,  60,  "HLTB: 30-60h"),
    (60,  float("inf"), "HLTB: 60h+"),
]
NO_DATA_TAG = "HLTB: No data"
TAG_PREFIX  = "HLTB:"

CACHE_FILE = Path("hltb_cache.json")

# SteamID64 base constant
_STEAM64_BASE = 76561197960265728


# ── helpers ──────────────────────────────────────────────────────────────────

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


def _pos(val) -> float | None:
    """Return float if val is a positive number, else None."""
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def bucket_label(hours: float | None) -> str:
    if hours is None:
        return NO_DATA_TAG
    for lo, hi, label in BUCKETS:
        if lo <= hours < hi:
            return label
    return "HLTB: 60h+"


# ── cache ─────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Steam library ─────────────────────────────────────────────────────────────

def get_steam_library(username: str) -> tuple[str, list[dict]]:
    """Return (steamID64, [{appID, name}, ...])."""
    url = f"https://steamcommunity.com/id/{username}/games/?tab=all&xml=1"
    try:
        data = urllib.request.urlopen(url, timeout=15)
    except Exception as e:
        console.print(f"[red]Steam fetch failed: {e}[/red]")
        console.print("[yellow]Check username and that profile is set to public.[/yellow]")
        sys.exit(1)

    soup = BeautifulSoup(data, features="xml")
    steam_id_tag = soup.find("steamID64")
    if steam_id_tag is None:
        console.print("[yellow]Profile may be private — no steamID64 found.[/yellow]")
        sys.exit(1)

    games = soup.find_all("game")
    if not games:
        console.print("[yellow]No games found.[/yellow]")
        sys.exit(1)

    library = [
        {"appID": g.find("appID").text, "name": g.find("name").text}
        for g in games
    ]
    return steam_id_tag.text, library


# ── HLTB lookup ───────────────────────────────────────────────────────────────

def fetch_hltb_data(library: list[dict]) -> list[dict]:
    cache = load_cache()
    not_found: list[str] = []

    to_fetch = [g for g in library if g["name"] not in cache]
    if to_fetch:
        for game in track(to_fetch, description="Looking up HLTB..."):
            name = game["name"]
            try:
                search = HowLongToBeat().search(name)
            except Exception:
                not_found.append(name)
                cache[name] = {"not_found": True, "cached_on": str(date.today())}
                continue
            if not search:
                not_found.append(name)
                cache[name] = {"not_found": True, "cached_on": str(date.today())}
                continue
            best = max(search, key=lambda e: e.similarity)
            cache[name] = {
                "hltb_name":     best.game_name,
                "main_story":    best.main_story,
                "main_extra":    best.main_extra,
                "completionist": best.completionist,
                "all_styles":    best.all_styles,
                "coop_time":     best.coop_time,
                "mp_time":       best.mp_time,
                "score":         best.review_score,
                "year":          best.release_world,
                "cached_on":     str(date.today()),
            }
        save_cache(cache)
        if not_found:
            console.print(f"[dim]{len(not_found)} games not found on HLTB (cached as missing).[/dim]")
    else:
        console.print("[dim]All games loaded from cache.[/dim]")

    results = []
    for game in library:
        name = game["name"]
        c = cache.get(name)
        if c is None or c.get("not_found"):
            continue
        results.append({
            "appID":         game["appID"],
            "name":          c["hltb_name"],
            "steam_name":    name,
            "main_story":    _pos(c["main_story"]),
            "main_extra":    _pos(c["main_extra"]),
            "completionist": _pos(c["completionist"]),
            "all_styles":    _pos(c["all_styles"]),
            "coop_time":     _pos(c["coop_time"]),
            "mp_time":       _pos(c["mp_time"]),
            "score":         c["score"],
            "year":          c["year"],
        })
    return results


# ── UI ────────────────────────────────────────────────────────────────────────

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
    table.add_column("#",        style="dim",   width=4,  justify="right")
    table.add_column("Game",     style="cyan",  min_width=30)
    table.add_column(sort_label, justify="right", style="green")
    table.add_column("Score",    justify="right", style="yellow")
    table.add_column("Year",     justify="right", style="dim")

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


# ── Steam category writing ────────────────────────────────────────────────────

def _find_sharedconfig(steam_id64: str) -> Path | None:
    account_id = int(steam_id64) - _STEAM64_BASE
    roots = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / "Library/Application Support/Steam",
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
    ]
    for root in roots:
        p = root / "userdata" / str(account_id) / "7/remote/sharedconfig.vdf"
        if p.exists():
            return p
    return None


def write_steam_categories(games: list[dict], sort_field: str, steam_id64: str) -> None:
    """Write HLTB time-bucket tags to sharedconfig.vdf so they appear in Steam."""
    console.print("\n[bold]Write categories to Steam?[/bold]")
    console.print("  Games get tagged by time bucket, e.g. [cyan]HLTB: 5-15h[/cyan].")
    console.print("  [yellow]⚠ Close Steam first — Steam overwrites this file on exit.[/yellow]")
    console.print("  1. Yes — write Steam categories")
    console.print("  2. No  — skip")
    if ask("Choose (1–2): ", 1, 2) == 2:
        return

    config_path = _find_sharedconfig(steam_id64)
    if config_path is None:
        console.print(
            "[red]sharedconfig.vdf not found.[/red]\n"
            "[dim]Run Steam at least once and check the profile is yours.[/dim]"
        )
        return

    console.print(f"[dim]Config: {config_path}[/dim]")

    # Backup before any write
    backup = config_path.with_suffix(".vdf.bak")
    backup.write_bytes(config_path.read_bytes())
    console.print(f"[dim]Backup → {backup}[/dim]")

    with config_path.open(encoding="utf-8") as f:
        data = vdf.load(f)

    # Navigate to Apps section
    try:
        apps = (
            data["UserRoamingConfigStore"]["Software"]["Valve"]["Steam"]["Apps"]
        )
    except KeyError:
        console.print("[red]Unexpected VDF structure — aborting.[/red]")
        return

    tag = {g["appID"]: bucket_label(g[sort_field]) for g in games}

    for app_id, new_label in tag.items():
        if app_id not in apps:
            apps[app_id] = {}
        existing_tags: dict = apps[app_id].get("tags", {})

        # Keep tags that aren't ours, rebuild with new bucket
        kept = {k: v for k, v in existing_tags.items() if not str(v).startswith(TAG_PREFIX)}
        next_idx = str(max((int(k) for k in kept), default=-1) + 1)
        kept[next_idx] = new_label
        apps[app_id]["tags"] = kept

    with config_path.open("w", encoding="utf-8") as f:
        vdf.dump(data, f, pretty=True)

    console.print(
        f"[green]Done.[/green] Tagged {len(tag)} games. "
        "[dim]Restart Steam to see categories in the left panel.[/dim]"
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    username = input("Steam username: ").strip()
    if not username:
        sys.exit(1)

    console.print(f"\n[dim]Fetching library for [bold]{username}[/bold]...[/dim]")
    steam_id64, library = get_steam_library(username)
    console.print(f"Found [bold]{len(library)}[/bold] games.")

    games = fetch_hltb_data(library)
    console.print(f"Matched [bold]{len(games)}[/bold] games on HowLongToBeat.")

    if not games:
        console.print("[red]No HLTB matches.[/red]")
        sys.exit(1)

    sort_label, sort_field = pick_play_mode(games)

    console.print("\n[bold]Order?[/bold]")
    console.print("  1. Shortest first")
    console.print("  2. Longest first")
    order = ask("Choose (1–2): ", 1, 2)

    display(games, sort_label, sort_field, reverse=(order == 2))
    write_steam_categories(games, sort_field, steam_id64)


if __name__ == "__main__":
    main()
