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

# ── Play mode groups ──────────────────────────────────────────────────────────
# Maps a user-facing label to the Steam category names that qualify a game
# and the HLTB field(s) to sort by. HLTB fields are tried in order; first
# non-None value wins for display; all are offered when there are multiple.

PLAY_MODE_GROUPS: dict[str, dict] = {
    "Single-player": {
        "steam_cats": {"Single-player"},
        "hltb_fields": [
            ("Main Story",    "main_story"),
            ("Main + Extras", "main_extra"),
            ("Completionist", "completionist"),
            ("All Styles",    "all_styles"),
        ],
        "tag_label": "Solo",
    },
    "Co-op": {
        "steam_cats": {
            "Co-op", "Online Co-op", "Local Co-Op",
            "Shared/Split Screen Co-op", "Shared/Split Screen",
            "Remote Play Together",
        },
        "hltb_fields": [("Co-op time", "coop_time")],
        "tag_label": "Co-op",
    },
    "Multiplayer": {
        "steam_cats": {
            "Multi-player", "Online Multi-Player", "Local Multi-Player",
            "Cross-Platform Multiplayer",
        },
        "hltb_fields": [("Multiplayer time", "mp_time")],
        "tag_label": "Multiplayer",
    },
}

# ── Bucket definitions ────────────────────────────────────────────────────────

HLTB_BUCKETS = [
    (0,          5,          "≤5h"),
    (5,          15,         "5-15h"),
    (15,         30,         "15-30h"),
    (30,         60,         "30-60h"),
    (60, float("inf"),       "60h+"),
]

MC_BUCKETS = [
    (90, "MC: 90+"),
    (75, "MC: 75-89"),
    (50, "MC: 50-74"),
    (0,  "MC: <50"),
]
MC_NO_SCORE = "MC: N/A"

TAG_PREFIX = "HLTB:"
CACHE_FILE = Path("hltb_cache.json")
_STEAM64_BASE = 76561197960265728


# ── helpers ───────────────────────────────────────────────────────────────────

def ask(prompt: str, lo: int, hi: int) -> int:
    while True:
        try:
            v = int(input(prompt))
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print(f"  Enter {lo}–{hi}")


def _pos(val) -> float | None:
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def hltb_bucket(hours: float | None) -> str:
    if hours is None:
        return "No data"
    for lo, hi, label in HLTB_BUCKETS:
        if lo <= hours < hi:
            return label
    return "60h+"


def mc_bucket(score: int | None) -> str:
    if score is None:
        return MC_NO_SCORE
    for threshold, label in MC_BUCKETS:
        if score >= threshold:
            return label
    return "MC: <50"


def steam_tag(mode_label: str, hours: float | None, mc_score: int | None, use_mc: bool) -> str:
    parts = [f"HLTB: {mode_label}", hltb_bucket(hours)]
    if use_mc:
        parts.append(mc_bucket(mc_score))
    return " | ".join(parts)


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
                "cached_on":     str(date.today()),
            }
        save_cache(cache)
        if not_found:
            console.print(f"[dim]{len(not_found)} games not found on HLTB (cached).[/dim]")
    else:
        console.print("[dim]HLTB data loaded from cache.[/dim]")

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
            "mc_score":      None,
            "steam_cats":    set(),
        })
    return results


# ── Steam Store data (categories + Metacritic) ────────────────────────────────

def fetch_steam_store_data(games: list[dict]) -> None:
    """Populate game['mc_score'] and game['steam_cats'] in-place; cached."""
    cache = load_cache()

    def key(app_id: str) -> str:
        return f"store:{app_id}"

    to_fetch = [g for g in games if key(g["appID"]) not in cache]
    if to_fetch:
        for game in track(to_fetch, description="Fetching Steam store data..."):
            app_id = game["appID"]
            url = (
                f"https://store.steampowered.com/api/appdetails"
                f"?appids={app_id}&filters=categories,metacritic"
            )
            mc_score = None
            categories: list[str] = []
            try:
                raw  = json.loads(urllib.request.urlopen(url, timeout=10).read())
                data = raw.get(app_id, {})
                if data.get("success"):
                    d = data.get("data", {})
                    mc_score   = (d.get("metacritic") or {}).get("score")
                    categories = [c["description"] for c in d.get("categories", [])]
            except Exception:
                pass
            cache[key(app_id)] = {
                "mc_score":   mc_score,
                "categories": categories,
                "cached_on":  str(date.today()),
            }
        save_cache(cache)
    else:
        console.print("[dim]Steam store data loaded from cache.[/dim]")

    for game in games:
        entry = cache.get(key(game["appID"]), {})
        game["mc_score"]   = entry.get("mc_score")
        game["steam_cats"] = set(entry.get("categories", []))


# ── Play mode selection ───────────────────────────────────────────────────────

def pick_play_mode(games: list[dict]) -> tuple[str, str, str]:
    """Return (mode_label_for_tag, ui_label, hltb_field)."""
    # Find which top-level modes have at least one matching game
    present: list[str] = [
        mode for mode, cfg in PLAY_MODE_GROUPS.items()
        if any(cfg["steam_cats"] & g["steam_cats"] for g in games)
    ]

    if not present:
        console.print("[yellow]No play-mode data from Steam. Defaulting to all games.[/yellow]")
        present = list(PLAY_MODE_GROUPS.keys())

    console.print("\n[bold]Play mode?[/bold]")
    for i, mode in enumerate(present, 1):
        cats = PLAY_MODE_GROUPS[mode]["steam_cats"]
        count = sum(1 for g in games if cats & g["steam_cats"])
        console.print(f"  {i}. {mode} [dim]({count} games)[/dim]")
    mode_idx = ask(f"Choose (1–{len(present)}): ", 1, len(present)) - 1
    chosen_mode = present[mode_idx]
    cfg = PLAY_MODE_GROUPS[chosen_mode]

    # For Solo, offer multiple HLTB metrics; others have exactly one
    fields = cfg["hltb_fields"]
    if len(fields) > 1:
        console.print("\n[bold]Sort by?[/bold]")
        for i, (label, _) in enumerate(fields, 1):
            console.print(f"  {i}. {label}")
        field_idx = ask(f"Choose (1–{len(fields)}): ", 1, len(fields)) - 1
    else:
        field_idx = 0

    ui_label, hltb_field = fields[field_idx]
    return cfg["tag_label"], ui_label, hltb_field


# ── Metacritic filter ─────────────────────────────────────────────────────────

def ask_metacritic_filter() -> int | None:
    """Return minimum Metacritic score or None (no filter)."""
    console.print("\n[bold]Filter by Metacritic score?[/bold]")
    console.print("  1. No filter")
    console.print("  2. Yes — set minimum score")
    if ask("Choose (1–2): ", 1, 2) == 1:
        return None
    while True:
        try:
            v = int(input("Minimum Metacritic score (0–100): "))
            if 0 <= v <= 100:
                return v
        except ValueError:
            pass
        print("  Enter 0–100")


# ── Display ───────────────────────────────────────────────────────────────────

def display(
    games: list[dict],
    mode_label: str,
    ui_label: str,
    hltb_field: str,
    reverse: bool,
    show_mc: bool,
) -> None:
    has_data = sorted(
        [g for g in games if g[hltb_field] is not None],
        key=lambda x: x[hltb_field],
        reverse=reverse,
    )
    no_data = [g for g in games if g[hltb_field] is None]

    def fmt(v) -> str:
        return f"{v:.1f}h" if v is not None else "[dim]-[/dim]"

    order_label = "longest" if reverse else "shortest"
    title = f"Steam Library — {mode_label} | {ui_label} ({order_label} first)"

    table = Table(title=title, show_lines=False)
    table.add_column("#",         style="dim",     width=4, justify="right")
    table.add_column("Game",      style="cyan",    min_width=30)
    table.add_column(ui_label,    justify="right", style="green")
    if show_mc:
        table.add_column("Metacritic", justify="right", style="magenta")
    table.add_column("Steam tags", style="dim",    min_width=20)
    table.add_column("Year",      justify="right", style="dim")

    def relevant_cats(g: dict) -> str:
        """Show only the play-category Steam tags, not achievements/cloud/etc."""
        all_mode_cats: set[str] = set()
        for cfg in PLAY_MODE_GROUPS.values():
            all_mode_cats |= cfg["steam_cats"]
        hits = g["steam_cats"] & all_mode_cats
        return ", ".join(sorted(hits)) if hits else "-"

    def add_row(rank: str, g: dict) -> None:
        year = str(g["year"]) if g.get("year") else "-"
        row  = [rank, g["name"], fmt(g[hltb_field])]
        if show_mc:
            mc = g["mc_score"]
            row.append(str(mc) if mc is not None else "[dim]-[/dim]")
        row += [relevant_cats(g), year]
        table.add_row(*row)

    for rank, g in enumerate(has_data, 1):
        add_row(str(rank), g)
    if no_data:
        table.add_section()
        for g in no_data:
            add_row("-", g)

    console.print(table)
    console.print(
        f"\n[dim]{len(has_data)} games with {ui_label} data"
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


def write_steam_categories(
    games: list[dict],
    mode_label: str,
    hltb_field: str,
    steam_id64: str,
    use_mc: bool,
) -> None:
    example = steam_tag(mode_label, 10.0, 90 if use_mc else None, use_mc)
    console.print("\n[bold]Write categories to Steam?[/bold]")
    console.print(f"  Example tag: [cyan]{example}[/cyan]")
    console.print("  [yellow]⚠ Close Steam first — it overwrites this file on exit.[/yellow]")
    console.print("  1. Yes")
    console.print("  2. No")
    if ask("Choose (1–2): ", 1, 2) == 2:
        return

    config_path = _find_sharedconfig(steam_id64)
    if config_path is None:
        console.print(
            "[red]sharedconfig.vdf not found.[/red]\n"
            "[dim]Run Steam at least once and ensure this is your own profile.[/dim]"
        )
        return

    console.print(f"[dim]Config: {config_path}[/dim]")
    backup = config_path.with_suffix(".vdf.bak")
    backup.write_bytes(config_path.read_bytes())
    console.print(f"[dim]Backup → {backup}[/dim]")

    with config_path.open(encoding="utf-8") as f:
        data = vdf.load(f)

    try:
        apps = data["UserRoamingConfigStore"]["Software"]["Valve"]["Steam"]["Apps"]
    except KeyError:
        console.print("[red]Unexpected VDF structure — aborting.[/red]")
        return

    for game in games:
        app_id    = game["appID"]
        new_label = steam_tag(mode_label, game[hltb_field], game["mc_score"], use_mc)

        if app_id not in apps:
            apps[app_id] = {}
        existing: dict = apps[app_id].get("tags", {})
        kept = {k: v for k, v in existing.items() if not str(v).startswith(TAG_PREFIX)}
        next_idx = str(max((int(k) for k in kept), default=-1) + 1)
        kept[next_idx] = new_label
        apps[app_id]["tags"] = kept

    with config_path.open("w", encoding="utf-8") as f:
        vdf.dump(data, f, pretty=True)

    console.print(
        f"[green]Done.[/green] Tagged {len(games)} games. "
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

    # Fetch Steam store data (categories + Metacritic) — one API call per game
    fetch_steam_store_data(games)

    # Pick play mode — based on actual Steam category tags
    mode_label, ui_label, hltb_field = pick_play_mode(games)

    # Filter to games that Steam says support this mode
    mode_cats = PLAY_MODE_GROUPS[
        next(k for k, v in PLAY_MODE_GROUPS.items() if v["tag_label"] == mode_label)
    ]["steam_cats"]
    games = [g for g in games if mode_cats & g["steam_cats"]]
    console.print(f"[dim]{len(games)} games match [{mode_label}] according to Steam.[/dim]")

    if not games:
        console.print("[red]No games found for that play mode.[/red]")
        sys.exit(1)

    # Optional Metacritic filter
    min_mc = ask_metacritic_filter()
    use_mc = min_mc is not None
    if use_mc:
        before = len(games)
        games = [g for g in games if g["mc_score"] is not None and g["mc_score"] >= min_mc]
        console.print(
            f"Filtered to [bold]{len(games)}[/bold] games "
            f"(Metacritic ≥ {min_mc}, dropped {before - len(games)})."
        )
        if not games:
            console.print("[red]No games pass the Metacritic filter.[/red]")
            sys.exit(1)

    console.print("\n[bold]Order?[/bold]")
    console.print("  1. Shortest first")
    console.print("  2. Longest first")
    order = ask("Choose (1–2): ", 1, 2)

    display(games, mode_label, ui_label, hltb_field, reverse=(order == 2), show_mc=use_mc)
    write_steam_categories(games, mode_label, hltb_field, steam_id64, use_mc=use_mc)


if __name__ == "__main__":
    main()
