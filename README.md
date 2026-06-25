# MTGA Linux Overlay

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)

A native, transparent **MTG Arena deck tracker and draft assistant for Linux** — built
because every existing tracker is either Windows-only (and breaks under Wine) or abandoned.

> Includes something no other Linux MTGA tool offers: **on-card draft ratings** — 17Lands
> win rates stamped onto each card in the pack.

It runs as a real Linux (Qt) window, so it draws transparently over Arena with no Wine
rendering tricks, and it reads everything from MTGA's `Player.log` plus MTGA's own card
database — never from game memory (which doesn't work under Wine). That's the whole trick:
it's built around the things that actually work on Linux, not the ones that don't.

## Features

**Match tracker**
- Live **library tracker** — cards remaining in your deck, updating as you draw and play
- **Mana curve** and **draw odds** (chance to draw a card by your next/3rd draw)
- Mana-cost symbols, color-coded counts, type grouping
- **Opponent panel** — cards your opponent has revealed this game
- **Hover any card** for its full art

**Deck builder**
- **Mana base** panel — per-color count of land sources in the deck you're editing, updating as Arena auto-saves
- Sources come from each land's *actual* mana abilities (a dual counts for both colors; "any color" and "becomes a basic land type" lands count for all five) — not a color-identity guess

**Draft assistant**
- Ranks each pack by **17Lands** games-in-hand win rate, best pick highlighted
- Works for any set — card names come from MTGA's own DB, so even day-one releases resolve

**Quality of life**
- Names resolve from MTGA's local card database (no waiting on Scryfall to index new sets)
- Overlay is **married to Arena** — only visible while Arena is focused
- Every panel is draggable, collapsible, opacity-adjustable, and remembers its place
- Right-click → **Experiments** to toggle experimental features

## Requirements

- Linux with a compositor that honors transparency (tested on KDE Plasma / KWin)
- MTG Arena installed via Steam/Proton (Flatpak Steam supported)
- Arena's **Detailed Logs (Plugin Support)** enabled (Settings → Account)
- Python 3.9+ with a working `venv` **and** `pip`. On Debian/Ubuntu:
  `sudo apt install python3-full` — the bare `python3-venv` package may omit
  `ensurepip` for a newer system Python (e.g. 3.13/3.14), which leaves you with a
  pip-less venv. `run.sh` detects this and bootstraps pip itself if needed.
- `x11-utils` and `x11-xserver-utils` — `xprop`/`xwininfo` drive the "show only over
  Arena" feature, and `xrandr` lets panels auto-place on the monitor Arena runs on
  (it falls back to the primary screen if `xrandr` is missing).
  `sudo apt install x11-utils x11-xserver-utils`

## Install & run

```bash
git clone https://github.com/krakenhavoc/mtga-overlay.git
cd mtga-overlay
./run.sh        # first run sets up a venv + PySide6, then launches
```

It **auto-detects** your `Player.log` and MTGA card database across Flatpak Steam, native
Steam, and games installed on a second drive (it reads Steam's `libraryfolders.vdf`). If
yours lives somewhere unusual, override either:

```bash
./run.sh --log "/path/to/Player.log"
MTGA_RAW_DIR="/path/to/MTGA/MTGA_Data/Downloads/Raw" ./run.sh
```

Run Arena in a **borderless / windowed-fullscreen** mode so the compositor keeps the overlay
on top. (A KWin window rule forcing "No titlebar and frame" + screen size gives a clean
borderless fullscreen if Arena lacks the option.)

## Usage

- **Click a panel header** — collapse / expand
- **Drag a header** — move it (position is saved)
- **Hover a card** — art + details (draft: win rate)
- **Scroll on a panel** — opacity
- **Right-click** — options, including the Experiments menu
- **Ctrl+Q** — quit

## Experimental features

Toggle these via right-click → **Experiments**. They're off by default and may be rough or
need per-setup tuning:

- **On-card draft ratings** — stamps the 17Lands win rate directly onto each card in the pack
  (it reproduces Arena's pack-sort to know each card's position). Working, but you calibrate
  the grid to your resolution once: enable it, then right-click → Experiments → *Calibrate
  on-card grid* and nudge the boxes onto the cards with the arrow keys.

## Development

```bash
./run.sh            # creates the venv (one time)
./dev-watch.sh      # auto-restarts the app on every edit to mtga_overlay.py
```

The app is currently a single file (`mtga_overlay.py`) being split into modules gradually.
Stable code stays put; new/risky work goes behind an `experiment()` flag (see `EXPERIMENTS`).

## How it works (short version)

- Tails `Player.log`, extracting GRE game-state, deck (`deckMessage`), and draft (`BotDraft`) events
- Resolves card grpIds via MTGA's `Raw_CardDatabase_*.mtga` (SQLite); Scryfall only for hover art
- Draft ratings from `17lands.com/card_ratings`, matched by card name
- Renders native Qt panels, kept above Arena and shown only while Arena is focused

## Credits & data

- Card data: MTG Arena's own client database
- Draft win rates: [17Lands](https://www.17lands.com)
- Card art & mana symbols: [Scryfall](https://scryfall.com)

Not affiliated with or endorsed by Wizards of the Coast, 17Lands, or Scryfall.

## License

MIT — see [LICENSE](LICENSE).
