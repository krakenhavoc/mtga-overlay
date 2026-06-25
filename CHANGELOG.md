# Changelog

## 1.5
- Deck-builder **mana base** panel: per-color count of land sources, shown while you edit a deck
- Sources are parsed from each land's actual mana abilities (duals count for both colors; "any color" and "becomes a basic land type" lands count for all five), not guessed from color identity
- Library/Opponent panels now hide while you're in the deck builder

## 1.4
- Full-history 17Lands data + robust name matching; draft refreshes as ratings load

## 1.3
- EXPERIMENTAL on-card draft ratings (win rate stamped on each card, with calibration)

## 1.2
- Auto-detect Player.log and MTGA card DB across Steam install types

## 1.1
- Experimental-features framework (right-click ▸ Experiments)
- Draft panel now defaults to the deck tracker's position

## 1.0
- Draft pick overlay: ranks each pack by 17Lands win rate, best pick highlighted

## 0.9
- Re-resolve cards stuck as `#id` (stale cache) through the MTGA database

## 0.8
- Card names from MTGA's own card database (fixes brand-new-set cards and the mana curve)
- Overlay shows only while Arena is the focused window

## 0.7
- Overlay forced above the game so it stays on top when Arena is focused

## 0.6
- Real mana-symbol SVGs

## 0.5
- Opponent panel, mana pips, mana curve, hover card art + draw odds

## 0.4
- Deck read from the per-match `deckMessage`; resets cleanly between games

## 0.3
- Collapse/expand, type sections, color badges, opacity, saved layout

## 0.2
- Transparent always-on-top overlay; live library tracking
