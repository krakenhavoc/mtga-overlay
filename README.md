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
- Python 3.9+ and `python3-venv`
- `x11-utils` (for the "show only over Arena" feature) — `sudo apt install x11-utils`

## Install & run

```bash
git clone https://github.com/krakenhavoc/mtga-linux-overlay.git
cd mtga-linux-overlay
./run.sh        # first run sets up a venv + PySide6, then launches
```

By default it reads the log from the Flatpak-Steam Proton prefix. To point it elsewhere:

```bash
./run.sh --log "/path/to/Player.log"
```

If your MTGA install isn't at the default path, set `MTGA_RAW_DIR` to its
`MTGA_Data/Downloads/Raw` folder (this is where card names come from):

```bash
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

- **On-card draft ratings** — stamps win rates directly onto each card in the pack. Requires
  calibrating to your resolution and Arena's draft layout; fragile by nature (the layout
  reflows as you pick). *In progress.*

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
