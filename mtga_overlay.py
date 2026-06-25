#!/usr/bin/env python3
"""
MTGA Linux Overlay — a native, transparent in-game deck tracker for MTG Arena.

Native Qt window (transparent on Linux, no Wine wall) that reads MTGA's Player.log
(the open data source, no game-memory reads). Shows your library live, plus your
opponent's revealed cards.

Setup:
    python3 -m pip install --user PySide6        # only dependency (rest is stdlib)
    python3 mtga_overlay.py
    python3 mtga_overlay.py --log "/path/to/Player.log"

Run Arena in Borderless/Windowed.
  • Click a panel header -> collapse/expand        • Drag header -> move
  • Hover a card        -> card art + draw odds    • Scroll -> opacity
  • Right-click         -> options                 • Ctrl+Q -> quit
Per-panel position/opacity/state are remembered between runs.
"""
import os, sys, json, time, math, re, glob, sqlite3, subprocess, argparse, threading, urllib.request, urllib.error
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
from PySide6 import QtCore, QtGui, QtWidgets
try:
    from PySide6.QtSvg import QSvgRenderer        # for real mana symbol SVGs
except Exception:
    QSvgRenderer = None

MTGA_APPID = "2141910"
# subpaths inside a Steam library
_LOG_SUB = ("steamapps/compatdata/" + MTGA_APPID + "/pfx/drive_c/users/steamuser/"
            "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log")
_RAW_SUB = "steamapps/common/MTGA/MTGA_Data/Downloads/Raw"


def _steam_roots():
    """Locations a Steam install (Flatpak or native) might live."""
    out = []
    for c in ("~/.local/share/Steam", "~/.steam/steam", "~/.steam/root",
              "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"):
        p = os.path.expanduser(c)
        if os.path.isdir(os.path.join(p, "steamapps")):
            out.append(p)
    return out


def _library_dirs():
    """All Steam library folders, including extra drives from libraryfolders.vdf."""
    libs, seen = [], set()
    for root in _steam_roots():
        for lib in [root] + re.findall(
                r'"path"\s*"([^"]+)"',
                _read(os.path.join(root, "steamapps", "libraryfolders.vdf"))):
            lib = lib.replace("\\\\", "/")
            if lib not in seen and os.path.isdir(os.path.join(lib, "steamapps")):
                seen.add(lib); libs.append(lib)
    return libs


def _read(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def find_log():
    if os.environ.get("MTGA_LOG"):
        return os.environ["MTGA_LOG"]
    for lib in _library_dirs():
        p = os.path.join(lib, _LOG_SUB)
        if os.path.exists(p):
            return p
    # fallback: Flatpak default (so the warning at least points somewhere sane)
    return os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam/" + _LOG_SUB)


def find_mtga_raw():
    if os.environ.get("MTGA_RAW_DIR"):
        return os.environ["MTGA_RAW_DIR"]
    for lib in _library_dirs():
        p = os.path.join(lib, _RAW_SUB)
        if os.path.isdir(p):
            return p
    return ""        # not found -> Scryfall fallback for names


DEFAULT_LOG = find_log()
MTGA_RAW_DIR = find_mtga_raw()
CACHE_DIR = Path(os.path.expanduser("~/.cache/mtga-overlay"))
IMG_DIR = CACHE_DIR / "img"
RATINGS_DIR = CACHE_DIR / "ratings"
MANA_DIR = CACHE_DIR / "mana"
SYMBOLOGY_FILE = MANA_DIR / "symbology.json"
CARD_CACHE = CACHE_DIR / "cards.json"
CONFIG_DIR = Path(os.path.expanduser("~/.config/mtga-overlay"))
CONFIG_FILE = CONFIG_DIR / "config.json"
# window classes that count as "Arena" for show-only-over-Arena behavior
ARENA_MATCH = ("mtga", "2141910", "magic")

VERSION = "1.4"
CHANGES = [
    "1.4  full-history 17Lands data (fixes sparse ratings for older sets) + robust name matching",
    "1.3  EXPERIMENTAL on-card draft ratings: win rate stamped on each card (right-click ▸ Experiments)",
    "1.2  auto-detect Player.log and MTGA card DB across Steam install types (Flatpak/native/extra drives)",
    "1.1  experimental-features framework (right-click ▸ Experiments); draft panel position fix",
    "1.0  draft pick overlay: ranks each pack by 17Lands win rate, best pick highlighted",
    "0.9  re-resolve cards stuck as #id (stale cache) through the MTGA database",
    "0.8  names from MTGA's own card DB (fixes new-set cards + curve); overlay shows only over Arena",
    "0.7  overlay forced above the game (bypass WM) so it stays on top when Arena is focused",
    "0.6  real mana-symbol SVGs; cards too new for Scryfall show as #id until it indexes them",
    "0.5  opponent panel, mana pips, mana curve, hover card art + draw odds",
    "0.4  deck read from per-match deckMessage; resets cleanly between games",
    "0.3  collapse/expand, type sections, color badges, opacity, saved layout",
    "0.2  transparent always-on-top overlay; live library tracking",
]

BG, BG2 = QtGui.QColor(20, 22, 30, 236), QtGui.QColor(29, 32, 44, 236)
BORDER = QtGui.QColor(255, 255, 255, 30)
ACCENT = QtGui.QColor(95, 200, 255)
ACCENT_OPP = QtGui.QColor(240, 130, 120)
TEXT, TEXT_DIM = QtGui.QColor(233, 236, 241), QtGui.QColor(122, 128, 142)
SECTION = QtGui.QColor(150, 158, 175)
HOVER = QtGui.QColor(255, 255, 255, 22)
PIP = {"W": QtGui.QColor(249, 245, 221), "U": QtGui.QColor(80, 156, 230),
       "B": QtGui.QColor(150, 132, 165), "R": QtGui.QColor(224, 100, 88),
       "G": QtGui.QColor(110, 188, 130), "M": QtGui.QColor(214, 178, 92),
       "C": QtGui.QColor(150, 154, 166)}
MANA_RE = re.compile(r"\{([^}]+)\}")


def badge_color(ci):
    if not ci: return PIP["C"]
    return PIP["M"] if len(ci) > 1 else PIP.get(ci[0], PIP["C"])


def name_tint(ci):
    if not ci: return TEXT
    base = PIP["M"] if len(ci) > 1 else PIP.get(ci[0], PIP["C"])
    # blend toward white for readability
    return QtGui.QColor((base.red() + 2 * 255) // 3, (base.green() + 2 * 255) // 3,
                        (base.blue() + 2 * 255) // 3)


def hyper_at_least_one(L, copies, draws):
    """P(draw >=1 of `copies` in next `draws`) from a library of size L."""
    if L <= 0 or copies <= 0 or draws <= 0: return 0.0
    if draws >= L: return 1.0 if copies else 0.0
    p_none = 1.0
    for i in range(draws):
        p_none *= (L - copies - i) / (L - i)
        if p_none <= 0: return 1.0
    return 1.0 - p_none


def active_window_is_arena():
    """True if the focused window looks like MTG Arena (so we only show over it).
    Fails OPEN (returns True) if it can't tell, so the overlay still works."""
    try:
        out = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                             capture_output=True, text=True, timeout=1).stdout
        wid = out.strip().split()[-1]
        if not wid.startswith("0x"):
            return True
        info = subprocess.run(["xprop", "-id", wid, "WM_CLASS", "WM_NAME"],
                              capture_output=True, text=True, timeout=1).stdout.lower()
        return any(k in info for k in ARENA_MATCH)
    except Exception:
        return True


# ---------------------------------------------------------------- card data
class MtgaDB:
    """MTGA's own card database (SQLite) — authoritative, current, offline.
    Resolves grpId -> name/type/colors/mana even for cards too new for Scryfall."""
    COLORS = {1: "W", 2: "U", 3: "B", 4: "R", 5: "G"}

    def __init__(self):
        self.con = None
        try:
            dbs = sorted(glob.glob(os.path.join(MTGA_RAW_DIR, "Raw_CardDatabase_*.mtga")))
            if dbs:
                self.con = sqlite3.connect(f"file:{dbs[-1]}?mode=ro", uri=True, check_same_thread=False)
                self.lock = threading.Lock()
                print(f"[carddb] using MTGA database: {os.path.basename(dbs[-1])}")
            else:
                print("[carddb] MTGA database not found; falling back to Scryfall for names")
        except Exception as e:
            print(f"[carddb] MTGA database unavailable ({e}); using Scryfall")
            self.con = None

    @staticmethod
    def _mana(s):
        out, cmc = [], 0
        for t in re.findall(r"o(\([^)]*\)|[^o]+)", s or ""):
            t = t.strip("()")
            out.append("{" + t + "}")
            if t.isdigit(): cmc += int(t)
            elif t in ("X", "Y", "Z"): pass
            else: cmc += 1
        return "".join(out), cmc

    def lookup(self, grp):
        if not self.con:
            return None
        try:
            with self.lock:
                cur = self.con.cursor()
                row = cur.execute("SELECT TitleId,Types,ColorIdentity,OldSchoolManaText,"
                                  "Rarity,Colors,CollectorNumber "
                                  "FROM Cards WHERE GrpId=?", (grp,)).fetchone()
                if not row:
                    return None
                tid, types, ci, m, rarity, colors, cn = row
                nm = cur.execute("SELECT Loc FROM Localizations_enUS WHERE LocId=? "
                                 "ORDER BY Formatted LIMIT 1", (tid,)).fetchone()
        except Exception:
            return None
        tv = set(int(x) for x in (types or "").split(",") if x.strip().isdigit())
        typ = "Land" if 5 in tv else "Creature" if 2 in tv else "Spell"
        cil = [self.COLORS[int(x)] for x in (ci or "").split(",")
               if x.strip().isdigit() and int(x) in self.COLORS]
        ms, cmc = self._mana(m or "")
        try:
            cnv = int(re.sub(r"\D", "", str(cn)) or 0)
        except Exception:
            cnv = 0
        return {"name": nm[0] if nm else f"#{grp}", "ci": cil,
                "type": typ, "cmc": cmc, "mana": ms, "img": None,
                "rarity": int(rarity or 0), "colors": colors or "", "cn": cnv}


class CardDB:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.mtga = MtgaDB()
        self.cards = {}
        if CARD_CACHE.exists():
            try:
                for k, v in json.loads(CARD_CACHE.read_text()).items():
                    if isinstance(v, dict) and "mana" in v:   # full current format only
                        self.cards[int(k)] = v
            except Exception:
                self.cards = {}
        self._pending, self._lock, self._dirty = set(), threading.Lock(), False
        threading.Thread(target=self._worker, daemon=True).start()

    def name(self, aid):
        c = self.card(aid)
        return c["name"] if c else None

    def card(self, aid):
        c = self.cards.get(aid)
        # re-resolve placeholders AND old-format entries missing the draft-sort fields
        if c and not c["name"].startswith("#") and "rarity" in c:
            return c
        mc = self.mtga.lookup(aid)                # MTGA DB: upgrades #id + stale entries
        if mc:
            self.cards[aid] = mc; self._dirty = True
            return mc
        if c:
            return c                              # keep showing what we have
        with self._lock: self._pending.add(aid)   # not in MTGA DB -> try Scryfall
        return None

    @staticmethod
    def _bucket(tl):
        tl = tl or ""
        if "Land" in tl: return "Land"
        if "Creature" in tl: return "Creature"
        return "Spell"

    def _worker(self):
        backoff = 0.1
        while True:
            with self._lock:
                aid = next(iter(self._pending), None)
            if aid is None:
                if self._dirty: self._save()
                time.sleep(0.3); continue
            done = False
            try:
                req = urllib.request.Request(
                    f"https://api.scryfall.com/cards/arena/{aid}",
                    headers={"User-Agent": "mtga-linux-overlay/1.0", "Accept": "application/json"})
                d = json.loads(urllib.request.urlopen(req, timeout=15).read())
                img = (d.get("image_uris") or {}).get("normal")
                if not img:
                    faces = d.get("card_faces") or []
                    if faces:
                        img = (faces[0].get("image_uris") or {}).get("normal")
                self.cards[aid] = {"name": d.get("name", f"#{aid}"),
                                   "ci": d.get("color_identity", []),
                                   "type": self._bucket(d.get("type_line", "")),
                                   "cmc": d.get("cmc", 0) or 0,
                                   "mana": d.get("mana_cost", "") or "",
                                   "img": img}
                self._dirty = True; done = True; backoff = 0.1
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.cards[aid] = {"name": f"#{aid}", "ci": [], "type": "Spell",
                                       "cmc": 99, "mana": "", "img": None}
                    self._dirty = True; done = True
                else:
                    time.sleep(backoff); backoff = min(backoff * 2, 10)
            except Exception:
                time.sleep(backoff); backoff = min(backoff * 2, 10)
            if done:
                with self._lock: self._pending.discard(aid)
            time.sleep(0.1)

    def _save(self):
        try:
            CARD_CACHE.write_text(json.dumps(self.cards)); self._dirty = False
        except Exception:
            pass


class ImageCache:
    """On hover, fetches card art from Scryfall by arena_id and caches to disk."""
    def __init__(self):
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        self._pix = {}
        self._pending, self._lock = set(), threading.Lock()
        self._failed = set()
        threading.Thread(target=self._worker, daemon=True).start()

    def pixmap(self, aid):
        if aid in self._pix: return self._pix[aid]
        f = IMG_DIR / f"{aid}.jpg"
        if f.exists():
            pm = QtGui.QPixmap(str(f))
            if not pm.isNull():
                self._pix[aid] = pm
                return pm
        if aid not in self._failed:
            with self._lock: self._pending.add(aid)
        return None

    def _worker(self):
        while True:
            with self._lock:
                aid = next(iter(self._pending), None)
            if aid is None:
                time.sleep(0.2); continue
            try:
                req = urllib.request.Request(
                    f"https://api.scryfall.com/cards/arena/{aid}",
                    headers={"User-Agent": "mtga-linux-overlay/1.0", "Accept": "application/json"})
                d = json.loads(urllib.request.urlopen(req, timeout=15).read())
                url = (d.get("image_uris") or {}).get("normal")
                if not url:
                    faces = d.get("card_faces") or []
                    if faces:
                        url = (faces[0].get("image_uris") or {}).get("normal")
                if url:
                    img = urllib.request.urlopen(
                        urllib.request.Request(url, headers={"User-Agent": "mtga-linux-overlay/1.0"}),
                        timeout=20).read()
                    (IMG_DIR / f"{aid}.jpg").write_bytes(img)
                else:
                    self._failed.add(aid)
            except Exception:
                self._failed.add(aid)   # e.g. 404 for brand-new cards: don't retry forever
            with self._lock:
                self._pending.discard(aid)
            time.sleep(0.1)


class Ratings:
    """17Lands GIH (games-in-hand) win rates per set, for draft pick suggestions."""
    FORMAT = "PremierDraft"

    def __init__(self):
        RATINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.data = {}                     # set code -> {card name: win rate 0..1}
        self.pending, self.lock = set(), threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("`", "'").strip().lower())

    def winrate(self, setc, name):
        if not setc:
            return None
        m = self.data.get(setc)
        if m is None:
            self._ensure(setc)
            return None
        return m.get(self._norm(name))

    def _ensure(self, setc):
        if setc in self.data:
            return
        f = RATINGS_DIR / f"{setc}_{self.FORMAT}_full.json"
        if f.exists():
            try:
                self.data[setc] = json.loads(f.read_text()); return
            except Exception:
                pass
        with self.lock:
            self.pending.add(setc)

    def _worker(self):
        while True:
            with self.lock:
                setc = next(iter(self.pending), None)
            if setc is None:
                time.sleep(0.3); continue
            try:
                # explicit full-history date range: an older set may have sparse data in
                # 17Lands' default (recent) window, so pull everything since 2019.
                today = time.strftime("%Y-%m-%d")
                url = (f"https://www.17lands.com/card_ratings/data?"
                       f"expansion={setc}&format={self.FORMAT}"
                       f"&start_date=2019-01-01&end_date={today}")
                req = urllib.request.Request(url, headers={"User-Agent": "mtga-linux-overlay/1.0"})
                arr = json.loads(urllib.request.urlopen(req, timeout=25).read())
                m = {self._norm(c["name"]): c["ever_drawn_win_rate"] for c in arr
                     if c.get("name") and c.get("ever_drawn_win_rate")}
                self.data[setc] = m
                (RATINGS_DIR / f"{setc}_{self.FORMAT}_full.json").write_text(json.dumps(m))
                print(f"[ratings] loaded {len(m)} cards for {setc}")
            except Exception as e:
                print(f"[ratings] {setc} failed: {e}")
                self.data[setc] = {}        # avoid hammering; no ratings available
            with self.lock:
                self.pending.discard(setc)
            time.sleep(0.2)


class ManaSymbols:
    """Official Scryfall mana-symbol SVGs, cached; rendered via QSvgRenderer."""
    def __init__(self):
        MANA_DIR.mkdir(parents=True, exist_ok=True)
        self.map = {}
        if SYMBOLOGY_FILE.exists():
            try:
                self.map = json.loads(SYMBOLOGY_FILE.read_text())
            except Exception:
                self.map = {}
        self.renderers = {}
        self.pending, self.lock = set(), threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def _safe(s):
        return re.sub(r"[^A-Za-z0-9]", "_", s)

    def renderer(self, sym):
        """Main-thread: QSvgRenderer for symbol, or None (queues download)."""
        if QSvgRenderer is None:
            return None
        if sym in self.renderers:
            return self.renderers[sym]
        f = MANA_DIR / (self._safe(sym) + ".svg")
        if f.exists():
            r = QSvgRenderer(str(f))
            if r.isValid():
                self.renderers[sym] = r
                return r
            return None
        with self.lock:
            self.pending.add(sym)
        return None

    def _worker(self):
        while True:
            if not self.map:
                try:
                    req = urllib.request.Request(
                        "https://api.scryfall.com/symbology",
                        headers={"User-Agent": "mtga-linux-overlay/1.0", "Accept": "application/json"})
                    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
                    self.map = {e["symbol"].strip("{}"): e["svg_uri"]
                                for e in d.get("data", []) if e.get("svg_uri")}
                    SYMBOLOGY_FILE.write_text(json.dumps(self.map))
                except Exception:
                    time.sleep(3); continue
            with self.lock:
                sym = next(iter(self.pending), None)
            if sym is None:
                time.sleep(0.3); continue
            url = self.map.get(sym)
            if url:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "mtga-linux-overlay/1.0"})
                    (MANA_DIR / (self._safe(sym) + ".svg")).write_bytes(
                        urllib.request.urlopen(req, timeout=15).read())
                except Exception:
                    pass
            with self.lock:
                self.pending.discard(sym)
            time.sleep(0.05)


# ---------------------------------------------------------------- log parsing
def iter_json_objects(buf):
    i, n = 0, len(buf)
    while i < n:
        if buf[i] == "{":
            depth = 0; j = i; instr = False; esc = False
            while j < n:
                c = buf[j]
                if instr:
                    if esc: esc = False
                    elif c == "\\": esc = True
                    elif c == '"': instr = False
                else:
                    if c == '"': instr = True
                    elif c == "{": depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            yield buf[i:j + 1], j + 1; i = j; break
                j += 1
            else:
                return
        i += 1


def walk_dicts(x):
    stack = [x]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur; stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def display_sort_key(card):
    """Reproduce how Arena lays a draft pack out on screen:
    rarity descending, then mono-color WUBRG, then multicolor, then colorless,
    then collector number ascending. (Verified against real packs.)"""
    if not card:
        return (0, 9, 0)
    cols = [c for c in (card.get("colors") or "").split(",") if c]
    cgroup = 9 if not cols else (8 if len(cols) > 1 else int(cols[0]))
    return (-int(card.get("rarity") or 0), cgroup, card.get("cn") or 0)


class MatchState:
    def __init__(self):
        self.reset_all()

    def reset_all(self):
        self.my_seat = None
        self.deck_ids = Counter()
        self.draft = None
        self.reset_game()

    def reset_game(self):
        self.inst = {}
        self.zone_type = {}

    def feed(self, obj, carddb):
        changed = False
        for node in walk_dicts(obj):
            if node.get("type") == "GREMessageType_ConnectResp" and node.get("systemSeatIds"):
                self.my_seat = node["systemSeatIds"][0]
                self.draft = None                      # entered a match -> not drafting
                self.reset_game()
            if node.get("CurrentModule") == "BotDraft" and isinstance(node.get("Payload"), str):
                try:
                    pl = json.loads(node["Payload"])
                except Exception:
                    pl = None
                if pl and isinstance(pl.get("DraftPack"), list) and pl.get("DraftPack"):
                    ev = pl.get("EventName", "")
                    self.draft = {
                        "set": ev.split("_")[1] if "_" in ev else "",
                        "pack_num": pl.get("PackNumber"), "pick_num": pl.get("PickNumber"),
                        "pack": [int(x) for x in pl["DraftPack"]],
                    }
                    changed = True
            dm = node.get("deckMessage")
            if isinstance(dm, dict) and isinstance(dm.get("deckCards"), list):
                try:
                    self.deck_ids = Counter(int(c) for c in dm["deckCards"]); changed = True
                except Exception:
                    pass
            gsm = node.get("gameStateMessage")
            if gsm:
                if gsm.get("type") == "GameStateType_Full":
                    self.reset_game()
                for z in gsm.get("zones", []):
                    if "zoneId" in z:
                        self.zone_type[z["zoneId"]] = z.get("type")
                for go in gsm.get("gameObjects", []):
                    iid = go.get("instanceId")
                    if iid is None: continue
                    e = self.inst.setdefault(iid, {"grp": None, "owner": None, "zone": None})
                    if "grpId" in go: e["grp"] = go["grpId"]
                    if "ownerSeatId" in go: e["owner"] = go["ownerSeatId"]
                    if "zoneId" in go: e["zone"] = go["zoneId"]
                if (gsm.get("gameInfo") or {}).get("stage") == "GameStage_GameOver":
                    self.reset_game()
                changed = True
        for aid in list(self.deck_ids):
            carddb.name(aid)
        return changed

    def remaining(self, carddb):
        deck, name_id = Counter(), {}
        for aid, q in self.deck_ids.items():
            nm = carddb.name(aid) or f"#{aid}"
            deck[nm] += q; name_id.setdefault(nm, aid)
        out = Counter()
        for e in self.inst.values():
            if e["owner"] != self.my_seat or e["grp"] is None: continue
            zt = self.zone_type.get(e["zone"])
            if zt and zt != "ZoneType_Library":
                out[carddb.name(e["grp"]) or f"#{e['grp']}"] += 1
        return [(nm, max(0, tot - out.get(nm, 0)), tot, name_id.get(nm)) for nm, tot in deck.items()]

    def opponent(self, carddb):
        if self.my_seat is None: return []
        opp = 1 if self.my_seat == 2 else 2
        c, ids = Counter(), {}
        for e in self.inst.values():
            if e["owner"] == opp and e["grp"] is not None:
                nm = carddb.name(e["grp"]) or f"#{e['grp']}"
                c[nm] += 1; ids.setdefault(nm, e["grp"])
        return [(nm, n, ids[nm]) for nm, n in c.items()]

    def draft_payload(self, carddb, ratings):
        if not self.draft or not self.draft.get("pack"):
            return None
        setc = self.draft["set"]
        cards = []
        for grp in self.draft["pack"]:
            nm = carddb.name(grp) or f"#{grp}"
            cards.append((nm, grp, ratings.winrate(setc, nm)))
        # display order = exactly how Arena lays the pack out on screen
        ordered = sorted(cards, key=lambda r: display_sort_key(carddb.card(r[1])))
        cards = sorted(cards, key=lambda r: (r[2] is None, -(r[2] or 0)))  # side panel: best WR first
        return {"set": setc, "pack_num": self.draft.get("pack_num"),
                "pick_num": self.draft.get("pick_num"), "cards": cards, "ordered": ordered}


class LogReader(QtCore.QThread):
    updated = QtCore.Signal(list, list, object)

    def __init__(self, path, carddb, ratings):
        super().__init__()
        self.path, self.carddb, self.ratings = path, carddb, ratings
        self.state = MatchState()

    def run(self):
        buf, pos = "", 0
        while True:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                time.sleep(1.0); continue
            if size < pos:
                pos, buf = 0, ""; self.state.reset_all()
            if size > pos:
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos); buf += f.read(); pos = f.tell()
                last, changed = 0, False
                for obj_str, end in iter_json_objects(buf):
                    if any(k in obj_str for k in
                           ("greToClientEvent", "gameStateMessage", "deckMessage",
                            "systemSeatIds", "BotDraft")):
                        try:
                            changed |= self.state.feed(json.loads(obj_str), self.carddb)
                        except Exception:
                            pass
                    last = end
                buf = buf[last:]
                if len(buf) > 2_000_000: buf = buf[-500_000:]
                if changed:
                    self.updated.emit(self.state.remaining(self.carddb),
                                      self.state.opponent(self.carddb),
                                      self.state.draft_payload(self.carddb, self.ratings))
            time.sleep(0.7)


# ---------------------------------------------------------------- drawing helpers
def draw_mana(p, right_x, cy, mana, ms, size=12):
    """Draw mana symbols ending at right_x, vertically centered on cy. Returns width.
    Uses official Scryfall SVGs (via ms); falls back to a colored dot until downloaded."""
    syms = MANA_RE.findall(mana or "")
    if not syms: return 0
    w = len(syms) * (size + 1)
    x = right_x - w
    for s in syms:
        rect = QtCore.QRectF(x, cy - size / 2, size, size)
        r = ms.renderer(s) if ms else None
        if r is not None:
            r.render(p, rect)
        else:
            col = PIP.get(s)
            if col is None:
                if s.isdigit() or s in ("X", "Y", "Z"): col = QtGui.QColor(165, 168, 178)
                elif "/" in s: col = next((PIP[q] for q in s.split("/") if q in PIP), PIP["M"])
                else: col = QtGui.QColor(165, 168, 178)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(col); p.drawEllipse(rect)
            if s.isdigit() or s in ("X", "Y", "Z"):
                p.setPen(QtGui.QColor(25, 27, 33))
                p.setFont(QtGui.QFont("Sans", int(size * 0.62), QtGui.QFont.Bold))
                p.drawText(rect, QtCore.Qt.AlignCenter, s)
        x += size + 1
    return w


# ---------------------------------------------------------------- card art popup
class ImagePopup(QtWidgets.QWidget):
    def __init__(self, imgcache):
        super().__init__(None, QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.X11BypassWindowManagerHint | QtCore.Qt.WindowTransparentForInput)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.imgcache = imgcache
        self.pm = None
        self.lines = []
        self.resize(250, 350)
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.update); self._t.start(400)

    def show_card(self, card, lines, anchor):
        self.lines = lines
        self.pm = self.imgcache.pixmap(card["sidkey"]) if card else None
        sw = QtWidgets.QApplication.primaryScreen().availableGeometry()
        x = anchor.x() - self.width() - 8
        if x < 0: x = anchor.x() + 8
        y = max(0, min(anchor.y() - 60, sw.height() - self.height()))
        self.move(x, y); self.show(); self.raise_()

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        y0 = 0
        if self.pm and not self.pm.isNull():
            pm = self.pm.scaledToWidth(244, QtCore.Qt.SmoothTransformation)
            p.drawPixmap(3, 3, pm)
            y0 = pm.height() + 8
        else:
            p.setBrush(QtGui.QColor(15, 17, 22, 220)); p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(3, 3, 244, 60, 8, 8)
            p.setPen(TEXT_DIM); p.setFont(QtGui.QFont("Sans", 9))
            p.drawText(14, 30, "loading art…"); y0 = 70
        if self.lines:
            box = QtCore.QRectF(3, y0, 244, 20 + 16 * len(self.lines))
            p.setBrush(QtGui.QColor(15, 17, 22, 230)); p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(box, 8, 8)
            p.setFont(QtGui.QFont("Sans", 9))
            for i, (txt, col) in enumerate(self.lines):
                p.setPen(col); p.drawText(14, int(y0 + 18 + i * 16), txt)


# ---------------------------------------------------------------- panel
OUTER, HEAD_H, ROW_H, SEC_H, PAD = 12, 32, 21, 19, 8
GROUP_ORDER = ["Creature", "Spell", "Land", "Other"]


class CardPanel(QtWidgets.QWidget):
    def __init__(self, mode, carddb, imgcache, popup, ms, cfg):
        super().__init__()
        self.mode = mode                 # "deck" or "opp"
        self.carddb, self.imgcache, self.popup, self.ms = carddb, imgcache, popup, ms
        self.cfg = cfg
        self.width_px = 268 if mode == "deck" else 232
        self.rows = []
        self.collapsed = cfg.get("collapsed", False)
        self.sections = cfg.get("sections", True) if mode == "deck" else False
        self._opacity = cfg.get("opacity", 1.0)
        self._press = None; self._dragging = False
        self._hover = None; self._hitboxes = []
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.X11BypassWindowManagerHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True); self.setWindowOpacity(self._opacity)
        self.move(cfg.get("x", 40 if mode == "deck" else 330), cfg.get("y", 80))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, QtWidgets.QApplication.quit)
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.update); self._t.start(1000)
        self.relayout()

    # ---- data
    @QtCore.Slot(list)
    def set_rows(self, rows):
        self.rows = rows; self.relayout()

    def _lib(self):
        return sum(r[1] for r in self.rows) if self.mode == "deck" else 0

    def _grouped(self):
        groups = {g: [] for g in GROUP_ORDER}
        for r in self.rows:
            name, count, sid = r[0], r[1], r[-1]
            c = self.carddb.card(sid) if sid is not None else None
            typ = c.get("type", "Spell") if c else ("Other" if name.startswith("#") else "Spell")
            cmc = c.get("cmc", 99) if c else 99
            mana = c.get("mana", "") if c else ""
            ci = c.get("ci", []) if c else []
            total = r[2] if self.mode == "deck" else count
            groups.setdefault(typ, []).append((name, count, total, sid, ci, cmc, mana))
        order_key = ((lambda r: (r[5], r[0])) if self.mode == "deck"
                     else (lambda r: (-r[1], r[5], r[0])))
        for g in groups: groups[g].sort(key=order_key)
        return groups

    def relayout(self):
        if self.collapsed:
            h = HEAD_H
        else:
            groups = self._grouped()
            n = sum(len(v) for v in groups.values())
            if n == 0:
                h = HEAD_H + PAD + ROW_H + PAD
            else:
                h = HEAD_H + PAD
                for g in GROUP_ORDER:
                    if not groups[g]: continue
                    if self.mode == "deck" and self.sections: h += SEC_H
                    h += ROW_H * len(groups[g])
                if self.mode == "deck":
                    h += 8 + 34                # mana-curve footer
                h += PAD
        self.setFixedSize(self.width_px + 2 * OUTER, int(h) + 2 * OUTER)
        self.update()

    # ---- paint
    def paintEvent(self, _):
        W = self.width_px
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        panel = QtCore.QRectF(OUTER, OUTER, W, self.height() - 2 * OUTER)
        for i in range(OUTER, 0, -1):
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(0, 0, 0, 7))
            p.drawRoundedRect(panel.adjusted(-i, -i + 2, i, i + 2), 12 + i, 12 + i)
        grad = QtGui.QLinearGradient(panel.topLeft(), panel.bottomLeft())
        grad.setColorAt(0, BG2); grad.setColorAt(1, BG)
        p.setBrush(grad); p.setPen(QtGui.QPen(BORDER, 1)); p.drawRoundedRect(panel, 11, 11)

        x = OUTER + 12
        accent = ACCENT if self.mode == "deck" else ACCENT_OPP
        title = "Library" if self.mode == "deck" else "Opponent"
        cnt = self._lib() if self.mode == "deck" else sum(r[1] for r in self.rows)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(accent); p.drawEllipse(QtCore.QRectF(x, OUTER + 12, 7, 7))
        p.setFont(QtGui.QFont("Sans", 10, QtGui.QFont.Bold)); p.setPen(TEXT)
        p.drawText(x + 14, OUTER + 21, title)
        p.setPen(TEXT_DIM); p.setFont(QtGui.QFont("Sans", 9))
        p.drawText(QtCore.QRectF(OUTER, OUTER, W - 30, HEAD_H),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, str(cnt))
        p.setPen(SECTION)
        p.drawText(QtCore.QRectF(OUTER, OUTER, W - 12, HEAD_H),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   "▸" if self.collapsed else "▾")
        self._hitboxes = []
        if self.collapsed: return
        p.setPen(QtGui.QPen(BORDER, 1))
        p.drawLine(OUTER + 10, OUTER + HEAD_H, OUTER + W - 10, OUTER + HEAD_H)

        groups = self._grouped()
        n = sum(len(v) for v in groups.values())
        y = OUTER + HEAD_H + PAD + 4
        if n == 0:
            p.setFont(QtGui.QFont("Sans", 9)); p.setPen(TEXT_DIM)
            p.drawText(x, y + 8, "Waiting for a match…" if self.mode == "deck"
                       else "No cards revealed yet")
            return
        f_sec = QtGui.QFont("Sans", 8, QtGui.QFont.Bold)
        f_row = QtGui.QFont("Sans", 9)
        idx = 0
        for g in GROUP_ORDER:
            items = groups[g]
            if not items: continue
            if self.mode == "deck" and self.sections:
                p.setFont(f_sec); p.setPen(SECTION)
                label = {"Creature": "CREATURES", "Spell": "SPELLS", "Land": "LANDS",
                         "Other": "RESOLVING…"}[g]
                p.drawText(x, y + 10, label)
                p.drawText(QtCore.QRectF(OUTER, y, W - 14, SEC_H),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                           str(sum(r[1] for r in items)))
                y += SEC_H
            for name, count, total, sid, ci, cmc, mana in items:
                rr = QtCore.QRect(OUTER + 4, y, W - 8, ROW_H)
                if self._hover == idx:
                    p.setPen(QtCore.Qt.NoPen); p.setBrush(HOVER); p.drawRoundedRect(rr, 5, 5)
                self._hitboxes.append((y, y + ROW_H, idx, sid, count, total))
                dim = (self.mode == "deck" and count == 0)
                bc = QtGui.QColor(badge_color(ci))
                if dim: bc.setAlpha(70)
                p.setPen(QtCore.Qt.NoPen); p.setBrush(bc)
                p.drawRoundedRect(QtCore.QRectF(x, y + 3, 17, 15), 4, 4)
                p.setFont(QtGui.QFont("Sans", 8, QtGui.QFont.Bold))
                p.setPen(QtGui.QColor(18, 20, 26) if not dim else QtGui.QColor(18, 20, 26, 120))
                p.drawText(QtCore.QRectF(x, y + 3, 17, 15), QtCore.Qt.AlignCenter, str(count))
                # mana pips (right)
                pipw = 0
                if mana:
                    pipw = draw_mana(p, OUTER + W - 12, y + ROW_H / 2, mana, self.ms, 12)
                p.setFont(f_row); p.setPen(TEXT_DIM if dim else name_tint(ci))
                avail = W - 8 - 24 - pipw - 12
                nm = name
                fm = QtGui.QFontMetrics(f_row)
                if fm.horizontalAdvance(nm) > avail:
                    while nm and fm.horizontalAdvance(nm + "…") > avail: nm = nm[:-1]
                    nm += "…"
                p.drawText(x + 24, y + 14, nm)
                y += ROW_H; idx += 1

        if self.mode == "deck":
            self._draw_curve(p, OUTER + 12, y + 6, W - 24)

    def _draw_curve(self, p, x, y, w):
        buckets = [0] * 8
        for name, rem, tot, sid in self.rows:
            if name.startswith("#"): continue            # unresolved card: skip (no real cmc)
            c = self.carddb.card(sid) if sid is not None else None
            if not c or c.get("type") == "Land": continue
            buckets[min(int(c.get("cmc", 0)), 7)] += rem
        mx = max(buckets) or 1
        p.setPen(SECTION); p.setFont(QtGui.QFont("Sans", 7, QtGui.QFont.Bold))
        p.drawText(x, y + 6, "CURVE")
        bw = (w - 0) / 8.0
        base = y + 30
        for i, v in enumerate(buckets):
            bx = x + i * bw
            bh = 18 * v / mx
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(95, 200, 255, 150))
            p.drawRoundedRect(QtCore.QRectF(bx, base - bh, bw - 3, bh), 2, 2)
            p.setPen(TEXT_DIM); p.setFont(QtGui.QFont("Sans", 6))
            p.drawText(QtCore.QRectF(bx, base + 1, bw - 3, 8), QtCore.Qt.AlignHCenter,
                       "7+" if i == 7 else str(i))

    # ---- interaction
    def _in_header(self, pos): return pos.y() <= OUTER + HEAD_H

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._press_local = e.position().toPoint()
            self._win = self.frameGeometry().topLeft(); self._dragging = False

    def mouseMoveEvent(self, e):
        if self._press is not None:
            d = e.globalPosition().toPoint() - self._press
            if self._dragging or d.manhattanLength() > 6:
                self._dragging = True; self.move(self._win + d)
            return
        y = e.position().y(); hv = None; hit = None
        for y0, y1, idx, sid, cnt, tot in self._hitboxes:
            if y0 <= y < y1: hv = idx; hit = (sid, cnt, tot); break
        if hv != self._hover:
            self._hover = hv; self.update()
            if hit and hit[0] is not None:
                self._show_popup(*hit, e.globalPosition().toPoint())
            else:
                self.popup.hide()

    def _show_popup(self, sid, cnt, tot, gpos):
        c = self.carddb.card(sid)
        if not c:
            self.popup.hide(); return
        card = dict(c); card["sidkey"] = sid
        lines = []
        if self.mode == "deck":
            L = self._lib()
            lines.append((f"{cnt} of {tot} left", TEXT))
            if cnt:
                lines.append((f"{hyper_at_least_one(L, cnt, 1)*100:.0f}% next draw", TEXT_DIM))
                lines.append((f"{hyper_at_least_one(L, cnt, 3)*100:.0f}% within 3 draws", TEXT_DIM))
        else:
            lines.append((f"seen {cnt}", TEXT))
        self.popup.show_card(card, lines, gpos)

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._dragging and self._in_header(self._press_local):
            self.collapsed = not self.collapsed; self.relayout()
        self._press = None; self._dragging = False; self._save_cfg()

    def leaveEvent(self, _):
        if self._hover is not None: self._hover = None; self.update()
        self.popup.hide()

    def wheelEvent(self, e):
        self._opacity = max(0.35, min(1.0, self._opacity + (0.05 if e.angleDelta().y() > 0 else -0.05)))
        self.setWindowOpacity(self._opacity); self._save_cfg()

    def contextMenuEvent(self, e):
        m = QtWidgets.QMenu(self)
        if self.mode == "deck":
            a1 = m.addAction("Group by type"); a1.setCheckable(True); a1.setChecked(self.sections)
        else:
            a1 = None
        a3 = m.addAction("Reset opacity")
        m.addSeparator()
        exp_menu = m.addMenu("Experiments")
        exp_acts = {}
        for key, label, tip in EXPERIMENTS:
            a = exp_menu.addAction(label); a.setCheckable(True); a.setChecked(experiment(key))
            a.setToolTip(tip); exp_acts[a] = key
        exp_menu.addSeparator()
        cal = exp_menu.addAction("Calibrate on-card grid")
        m.addSeparator()
        aq = m.addAction("Quit")
        act = m.exec(e.globalPos())
        if a1 and act == a1: self.sections = not self.sections; self.relayout()
        elif act == a3: self._opacity = 1.0; self.setWindowOpacity(1.0)
        elif act in exp_acts: set_experiment(exp_acts[act], not experiment(exp_acts[act]))
        elif act == cal: calibrate_oncard()
        elif act == aq: QtWidgets.QApplication.quit()
        self._save_cfg()

    def _save_cfg(self):
        self.cfg.update({"x": self.x(), "y": self.y(), "collapsed": self.collapsed,
                         "opacity": self._opacity, "sections": self.sections})
        save_config()

    def closeEvent(self, e):
        self._save_cfg(); super().closeEvent(e)


class DraftPanel(QtWidgets.QWidget):
    """During a draft: ranks the current pack by 17Lands GIH win rate."""
    GOOD = QtGui.QColor(150, 210, 120)

    def __init__(self, carddb, imgcache, popup, cfg):
        super().__init__()
        self.carddb, self.imgcache, self.popup, self.cfg = carddb, imgcache, popup, cfg
        self.width_px = 252
        self.payload = None
        self.collapsed = cfg.get("collapsed", False)
        self._opacity = cfg.get("opacity", 1.0)
        self._press = None; self._dragging = False; self._hover = None; self._hitboxes = []
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.X11BypassWindowManagerHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True); self.setWindowOpacity(self._opacity)
        self.move(cfg.get("x", 40), cfg.get("y", 80))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, QtWidgets.QApplication.quit)
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.update); self._t.start(1000)
        self.relayout()

    def has_cards(self):
        return bool(self.payload and self.payload.get("cards"))

    @QtCore.Slot(object)
    def set_draft(self, payload):
        self.payload = payload; self.relayout()

    def relayout(self):
        n = len(self.payload["cards"]) if self.has_cards() else 1
        h = HEAD_H if self.collapsed else HEAD_H + PAD + n * ROW_H + PAD
        self.setFixedSize(self.width_px + 2 * OUTER, int(h) + 2 * OUTER)
        self.update()

    def paintEvent(self, _):
        try:
            self._paint()
        except Exception:
            import traceback; traceback.print_exc()

    def _paint(self):
        W = self.width_px
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        panel = QtCore.QRectF(OUTER, OUTER, W, self.height() - 2 * OUTER)
        for i in range(OUTER, 0, -1):
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(0, 0, 0, 7))
            p.drawRoundedRect(panel.adjusted(-i, -i + 2, i, i + 2), 12 + i, 12 + i)
        grad = QtGui.QLinearGradient(panel.topLeft(), panel.bottomLeft())
        grad.setColorAt(0, BG2); grad.setColorAt(1, BG)
        p.setBrush(grad); p.setPen(QtGui.QPen(BORDER, 1)); p.drawRoundedRect(panel, 11, 11)
        x = OUTER + 12
        p.setPen(QtCore.Qt.NoPen); p.setBrush(self.GOOD); p.drawEllipse(QtCore.QRectF(x, OUTER + 12, 7, 7))
        p.setFont(QtGui.QFont("Sans", 10, QtGui.QFont.Bold)); p.setPen(TEXT)
        title = "Draft"
        if self.has_cards():
            title = f"Draft  P{(self.payload.get('pack_num') or 0)+1}P{self.payload.get('pick_num') or 0}"
        p.drawText(x + 14, OUTER + 21, title)
        if self.has_cards():
            p.setPen(TEXT_DIM); p.setFont(QtGui.QFont("Sans", 9))
            p.drawText(QtCore.QRectF(OUTER, OUTER, W - 30, HEAD_H),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, self.payload.get("set", ""))
        p.setPen(SECTION)
        p.drawText(QtCore.QRectF(OUTER, OUTER, W - 12, HEAD_H),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "▸" if self.collapsed else "▾")
        self._hitboxes = []
        if self.collapsed:
            return
        p.setPen(QtGui.QPen(BORDER, 1)); p.drawLine(OUTER + 10, OUTER + HEAD_H, OUTER + W - 10, OUTER + HEAD_H)
        y = OUTER + HEAD_H + PAD + 4
        if not self.has_cards():
            p.setFont(QtGui.QFont("Sans", 9)); p.setPen(TEXT_DIM)
            p.drawText(x, y + 8, "Waiting for a pack…"); return
        f = QtGui.QFont("Sans", 9)
        for idx, (name, grp, wr) in enumerate(self.payload["cards"]):
            rr = QtCore.QRect(OUTER + 4, y, W - 8, ROW_H)
            if idx == 0:
                p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(150, 210, 120, 30)); p.drawRoundedRect(rr, 5, 5)
            elif self._hover == idx:
                p.setPen(QtCore.Qt.NoPen); p.setBrush(HOVER); p.drawRoundedRect(rr, 5, 5)
            self._hitboxes.append((y, y + ROW_H, idx, grp, wr))
            p.setFont(QtGui.QFont("Sans", 9, QtGui.QFont.Bold))
            if wr is not None:
                p.setPen(self.GOOD if idx == 0 else TEXT)
                p.drawText(QtCore.QRectF(x, y, 42, ROW_H), QtCore.Qt.AlignVCenter, f"{wr*100:.1f}")
            else:
                p.setPen(TEXT_DIM)
                p.drawText(QtCore.QRectF(x, y, 42, ROW_H), QtCore.Qt.AlignVCenter, "—")
            p.setFont(f); p.setPen(TEXT if idx == 0 else QtGui.QColor(210, 213, 219))
            nm = name; fm = QtGui.QFontMetrics(f); avail = W - 8 - 46 - 12
            if fm.horizontalAdvance(nm) > avail:
                while nm and fm.horizontalAdvance(nm + "…") > avail: nm = nm[:-1]
                nm += "…"
            p.drawText(x + 46, y + 14, nm)
            y += ROW_H

    def _in_header(self, pos): return pos.y() <= OUTER + HEAD_H

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._press = e.globalPosition().toPoint(); self._press_local = e.position().toPoint()
            self._win = self.frameGeometry().topLeft(); self._dragging = False

    def mouseMoveEvent(self, e):
        if self._press is not None:
            d = e.globalPosition().toPoint() - self._press
            if self._dragging or d.manhattanLength() > 6:
                self._dragging = True; self.move(self._win + d)
            return
        y = e.position().y(); hv = None; hit = None
        for y0, y1, idx, grp, wr in self._hitboxes:
            if y0 <= y < y1: hv = idx; hit = (grp, wr); break
        if hv != self._hover:
            self._hover = hv; self.update()
            if hit:
                c = self.carddb.card(hit[0])
                if c:
                    card = dict(c); card["sidkey"] = hit[0]
                    lines = [(card["name"], TEXT)]
                    if hit[1] is not None:
                        lines.append((f"{hit[1]*100:.1f}% GIH win rate", TEXT_DIM))
                    self.popup.show_card(card, lines, e.globalPosition().toPoint())
            else:
                self.popup.hide()

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._dragging and self._in_header(self._press_local):
            self.collapsed = not self.collapsed; self.relayout()
        self._press = None; self._dragging = False; self._save()

    def leaveEvent(self, _):
        if self._hover is not None: self._hover = None; self.update()
        self.popup.hide()

    def wheelEvent(self, e):
        self._opacity = max(0.35, min(1.0, self._opacity + (0.05 if e.angleDelta().y() > 0 else -0.05)))
        self.setWindowOpacity(self._opacity); self._save()

    def contextMenuEvent(self, e):
        m = QtWidgets.QMenu(self); a = m.addAction("Reset opacity")
        m.addSeparator()
        exp_menu = m.addMenu("Experiments"); exp_acts = {}
        for key, label, tip in EXPERIMENTS:
            xa = exp_menu.addAction(label); xa.setCheckable(True); xa.setChecked(experiment(key))
            xa.setToolTip(tip); exp_acts[xa] = key
        exp_menu.addSeparator(); cal = exp_menu.addAction("Calibrate on-card grid")
        m.addSeparator(); q = m.addAction("Quit")
        act = m.exec(e.globalPos())
        if act == a: self._opacity = 1.0; self.setWindowOpacity(1.0)
        elif act in exp_acts: set_experiment(exp_acts[act], not experiment(exp_acts[act]))
        elif act == cal: calibrate_oncard()
        elif act == q: QtWidgets.QApplication.quit()
        self._save()

    def _save(self):
        self.cfg.update({"x": self.x(), "y": self.y(), "collapsed": self.collapsed, "opacity": self._opacity})
        save_config()


class OnCardOverlay(QtWidgets.QWidget):
    """EXPERIMENTAL: stamps the win rate onto each card in the draft pack, using the
    verified display sort to know each card's grid position. ALWAYS click-through
    (never grabs input); a small CalibrationControl window aligns the grid."""
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.payload = None
        self.guides = False
        g = cfg.setdefault("grid", {})         # GLOBAL screen pixels, calibratable
        g.setdefault("x0", 250); g.setdefault("y0", 250)   # position guess (monitor-dependent)
        g.setdefault("dx", 262); g.setdefault("dy", 363); g.setdefault("cols", 5)  # ~2560x1440 spacing
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.X11BypassWindowManagerHint
                            | QtCore.Qt.WindowTransparentForInput)   # never intercepts clicks
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        geo = QtCore.QRect()
        for s in QtWidgets.QApplication.screens():
            geo = geo.united(s.geometry())
        self._origin = geo.topLeft()
        self.setGeometry(geo)
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.update); self._t.start(800)

    @QtCore.Slot(object)
    def set_payload(self, payload):
        self.payload = payload; self.update()

    def has_cards(self):
        return bool(self.payload and self.payload.get("ordered"))

    def _pos(self, i):
        g = self.cfg["grid"]
        col, row = i % g["cols"], i // g["cols"]
        return (g["x0"] + col * g["dx"] - self._origin.x(),
                g["y0"] + row * g["dy"] - self._origin.y())

    def paintEvent(self, _):
        try:
            self._paint()
        except Exception:
            import traceback; traceback.print_exc()

    def _paint(self):
        if not self.has_cards() and not self.guides:
            return
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        cards = self.payload["ordered"] if self.has_cards() else [("", 0, None)] * 15
        best = max((wr for _, _, wr in cards if wr is not None), default=None)
        for i, (name, grp, wr) in enumerate(cards):
            x, y = self._pos(i)
            if self.guides:
                p.setPen(QtGui.QPen(QtGui.QColor(110, 200, 255, 220), 2)); p.setBrush(QtCore.Qt.NoBrush)
                p.drawRect(int(x), int(y), 44, 24)
            txt = f"{wr*100:.0f}" if wr is not None else "—"
            top = wr is not None and best is not None and abs(wr - best) < 1e-9
            bg = QtGui.QColor(95, 200, 130, 235) if top else QtGui.QColor(20, 22, 30, 230)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(bg)
            p.drawRoundedRect(QtCore.QRectF(x, y, 42, 22), 6, 6)
            p.setPen(QtGui.QColor(18, 20, 26) if top else QtGui.QColor(235, 238, 244))
            p.setFont(QtGui.QFont("Sans", 10, QtGui.QFont.Bold))
            p.drawText(QtCore.QRectF(x, y, 42, 22), QtCore.Qt.AlignCenter, txt)


class CalibrationControl(QtWidgets.QWidget):
    """Small focusable window to nudge the on-card grid with arrow keys. The big overlay
    stays click-through; only this little panel takes the keyboard, so the screen is usable."""
    def __init__(self, cfg, oncard):
        super().__init__()
        self.cfg = cfg; self.oncard = oncard
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                            | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setFixedSize(380, 132)
        self.move(80, 80)

    def start(self):
        self.oncard.guides = True; self.oncard.update()
        self.show(); self.raise_(); self.activateWindow(); self.setFocus()

    def finish(self):
        save_config()
        self.oncard.guides = False; self.oncard.update()
        self.hide()

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor(15, 17, 22, 240)); p.setPen(QtGui.QPen(QtGui.QColor(110, 200, 255, 120), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        p.setPen(QtGui.QColor(110, 200, 255)); p.setFont(QtGui.QFont("Sans", 11, QtGui.QFont.Bold))
        p.drawText(16, 28, "Calibrate on-card grid")
        p.setPen(QtGui.QColor(210, 213, 219)); p.setFont(QtGui.QFont("Sans", 9))
        p.drawText(16, 50, "Arrows: move    ·    Shift+Arrows: spacing    ·    Ctrl: fine")
        p.drawText(16, 68, "Align the blue boxes to the cards' top-left corners")
        p.drawText(16, 86, "Enter / Esc: save & finish")
        g = self.cfg["grid"]
        p.setPen(QtGui.QColor(150, 158, 175)); p.setFont(QtGui.QFont("Sans", 9, QtGui.QFont.Bold))
        p.drawText(16, 112, f"x0={g['x0']}  y0={g['y0']}  dx={g['dx']}  dy={g['dy']}  cols={g['cols']}")

    def keyPressEvent(self, e):
        g = self.cfg["grid"]; k = e.key(); mods = e.modifiers()
        shift = mods & QtCore.Qt.ShiftModifier
        step = 1 if (mods & QtCore.Qt.ControlModifier) else 5
        if k == QtCore.Qt.Key_Left:    g["dx" if shift else "x0"] -= step
        elif k == QtCore.Qt.Key_Right:  g["dx" if shift else "x0"] += step
        elif k == QtCore.Qt.Key_Up:     g["dy" if shift else "y0"] -= step
        elif k == QtCore.Qt.Key_Down:   g["dy" if shift else "y0"] += step
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Escape):
            self.finish(); return
        else:
            return
        self.oncard.update(); self.update()

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None:
            self.move(e.globalPosition().toPoint() - self._drag)


# ---------------------------------------------------------------- config + experiments
_CONFIG = {}

# Experimental features: off by default, toggled in config or the right-click menu.
# Keep the stable core untouched; gate anything risky/unfinished behind a flag here.
EXPERIMENTS = [
    ("on_card_ratings", "On-card draft ratings", "Stamp win rates onto each card in the "
     "pack (needs per-resolution calibration; fragile)."),
]


def experiment(name, default=False):
    return bool(_CONFIG.get("experiments", {}).get(name, default))


def set_experiment(name, value):
    _CONFIG.setdefault("experiments", {})[name] = bool(value)
    save_config()


_ONCARD = None      # the on-card overlay
_CALIB = None       # the calibration control window


def calibrate_oncard():
    if _CALIB is not None:
        _CALIB.start()


def save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(_CONFIG))
    except Exception:
        pass


def main():
    global _CONFIG
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--changes", action="store_true", help="print full changelog and exit")
    args = ap.parse_args()
    if args.changes:
        print("\n".join(CHANGES)); return
    print(f"┌─ MTGA Linux Overlay v{VERSION}")
    print(f"└─ this build: {CHANGES[0].split('  ', 1)[1]}")
    if os.path.exists(args.log):
        print(f"[log]  {args.log}")
    else:
        print(f"[warn] Player.log not found. Looked across your Steam libraries.\n"
              f"       Is MTGA installed and 'Detailed Logs (Plugin Support)' enabled?\n"
              f"       Override with:  --log /path/to/Player.log")
    if not MTGA_RAW_DIR:
        print("[warn] MTGA card database not found; card names will use Scryfall only.\n"
              "       Set MTGA_RAW_DIR=/path/to/MTGA_Data/Downloads/Raw to fix new-set names.")
    try:
        _CONFIG = json.loads(CONFIG_FILE.read_text())
    except Exception:
        _CONFIG = {}
    _CONFIG.setdefault("deck", {}); _CONFIG.setdefault("opp", {}); _CONFIG.setdefault("draft", {})
    _CONFIG.setdefault("experiments", {}); _CONFIG.setdefault("oncard", {})

    global _ONCARD, _CALIB
    app = QtWidgets.QApplication(sys.argv)
    carddb = CardDB(); imgcache = ImageCache(); mana = ManaSymbols(); ratings = Ratings()
    popup = ImagePopup(imgcache)
    deck = CardPanel("deck", carddb, imgcache, popup, mana, _CONFIG["deck"])
    opp = CardPanel("opp", carddb, imgcache, popup, mana, _CONFIG["opp"])
    # first run: place the draft panel where the deck tracker already lives
    if "x" not in _CONFIG["draft"] and "x" in _CONFIG["deck"]:
        _CONFIG["draft"]["x"] = _CONFIG["deck"]["x"]
        _CONFIG["draft"]["y"] = _CONFIG["deck"].get("y", 80)
    draft = DraftPanel(carddb, imgcache, popup, _CONFIG["draft"])
    oncard = OnCardOverlay(_CONFIG["oncard"]); _ONCARD = oncard
    _CALIB = CalibrationControl(_CONFIG["oncard"], oncard)
    reader = LogReader(args.log, carddb, ratings)
    reader.updated.connect(lambda d, o, dr: (deck.set_rows(d), opp.set_rows(o),
                                             draft.set_draft(dr), oncard.set_payload(dr)))
    reader.start()

    # only visible while Arena is focused; draft panel during a draft, deck/opp during a match
    def setvis(w, v):
        if v and not w.isVisible():
            w.show(); w.raise_()
        elif not v and w.isVisible():
            w.hide()

    def update_visibility():
        arena = active_window_is_arena()
        drafting = draft.has_cards()
        setvis(deck, arena and not drafting)
        setvis(opp, arena and not drafting)
        setvis(draft, arena and drafting)
        # on-card overlay (experimental): over the pack while drafting, or anytime when calibrating
        show_oncard = (arena and drafting and experiment("on_card_ratings")) or oncard.guides
        if show_oncard and not oncard.isVisible():
            oncard.show(); oncard.raise_()
        elif not show_oncard and oncard.isVisible():
            oncard.hide()
        if not arena:
            popup.hide()
    vtimer = QtCore.QTimer(deck)
    vtimer.timeout.connect(update_visibility)
    vtimer.start(600)
    update_visibility()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
