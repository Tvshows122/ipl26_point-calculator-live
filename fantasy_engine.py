"""
fantasy_engine.py — Dream11-style fantasy point calculator.

Input:  innings1_raw dict  { "Innings1": { "BattingCard": [...], ... } }
        innings2_raw dict  { "Innings2": { "Innings2": { ... } } }

Output: sorted list of player dicts with full breakdown + rank.

Key design choices:
  - Player names are normalized (collapse whitespace) to avoid duplicate
    records caused by API inconsistencies like "Jacob Duffy  (RP)" vs
    "Jacob Duffy (RP)".
  - ManhattanWickets is filtered by InningsNo so wickets from the OTHER
    innings don't get double-counted when both feeds embed full-match data.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SR_MIN_BALLS  = 10
SR_MIN_RUNS   = 20
ECO_MIN_OVERS = 2.0   # floating overs


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _safe_int(val, default=0) -> int:
    try:
        return int(val) if val not in (None, "", "null") else default
    except (TypeError, ValueError):
        return default


def _overs_to_balls(overs_val) -> int:
    """Convert IPL over notation '3.2' → 20 balls, '4' → 24 balls."""
    try:
        overs   = float(overs_val) if overs_val else 0.0
        full    = int(overs)
        partial = round((overs - full) * 10)
        return full * 6 + partial
    except (TypeError, ValueError):
        return 0


def normalize_name(name) -> str:
    """
    Collapse multiple spaces so 'Jacob Duffy  (RP)' == 'Jacob Duffy (RP)'.
    Keeps role suffixes like (wk), (c), (IP), (RP) as part of the name.
    """
    return " ".join(str(name or "").split()).strip()


# Role suffixes that appear in BattingCard/BowlingCard but NOT in OutDesc
# Matches one or more suffixes like (c)(wk) or (IP)
_ROLE_SUFFIX_RE = re.compile(
    r"(?:\s*\((?:wk|c|IP|RP|WK|C)\))+$", re.IGNORECASE
)


def base_name(name: str) -> str:
    """
    Strip trailing role suffixes so 'Jitesh Sharma (wk)' → 'Jitesh Sharma'
    and 'Devdutt Padikkal (IP)' → 'Devdutt Padikkal'.
    Used to match OutDesc fielder names (which have no suffixes) against
    canonical card names.
    """
    return _ROLE_SUFFIX_RE.sub("", normalize_name(name)).strip()


def _build_base_to_canonical(batting_card: list, bowling_card: list) -> dict:
    """
    Build {base_name: canonical_normalized_name} lookup from card data.
    When a fielder appears in OutDesc without a suffix, we resolve them
    to their full canonical name (e.g. 'Jitesh Sharma' → 'Jitesh Sharma (wk)').
    """
    lookup: dict[str, str] = {}
    for card in (batting_card, bowling_card):
        for entry in card:
            canonical = normalize_name(entry.get("PlayerName", ""))
            if not canonical:
                continue
            b = base_name(canonical)
            # Prefer the name WITH a suffix (more informative)
            existing = lookup.get(b)
            if existing is None or len(canonical) > len(existing):
                lookup[b] = canonical
    return lookup


def _resolve_player(raw_name: str, lookup: dict) -> str:
    """
    Given a raw player name (which may lack a suffix), return
    the canonical name from the player lookup, or the normalized name
    if no match found. Uses base_name for mapping.
    """
    norm = normalize_name(raw_name).lstrip("†").strip()  # strip dagger
    # Direct hit (name already has suffix)
    if norm in lookup.values():
        return norm
    # Try base name match
    b = base_name(norm)
    return lookup.get(b, norm)


def _unwrap(innings_dict: dict | None, key: str) -> dict:
    """Return inner innings dict, e.g. innings1_raw['Innings1']."""
    if not innings_dict:
        return {}
    return innings_dict.get(key, {})


# ---------------------------------------------------------------------------
# Batting points
# ---------------------------------------------------------------------------

def _batting_points(bat: dict, is_bowler: bool) -> int:
    runs        = _safe_int(bat.get("Runs"))
    balls_faced = _safe_int(bat.get("Balls"))
    fours       = _safe_int(bat.get("Fours"))
    sixes       = _safe_int(bat.get("Sixes"))
    out_desc    = str(bat.get("OutDesc") or bat.get("ShortOutDesc") or "").strip().lower()
    is_out      = out_desc not in ("", "not out", "dnb", "did not bat")

    pts  = runs * 1
    pts += fours * 4
    pts += sixes * 6

    # Milestone bonuses
    if runs >= 100:
        pts += 16
    elif runs >= 75:
        pts += 12
    elif runs >= 50:
        pts += 8
    elif runs >= 25:
        pts += 4

    # Duck penalty: -2 only for Batters, WKs, and ARs. 
    # The `is_bowler` flag is passed in (based on batting order + bowling data)
    # Bowlers get 0 penalty for a duck.
    if is_out and runs == 0 and not is_bowler:
        pts -= 2

    # Strike rate (applies only when min criteria met)
    if balls_faced > 0 and (runs >= SR_MIN_RUNS or balls_faced >= SR_MIN_BALLS):
        sr = (runs / balls_faced) * 100
        if sr < 50:
            pts -= 6
        elif sr < 60:
            pts -= 4
        elif sr < 70:
            pts -= 2
        elif sr < 130:
            pts += 0
        elif sr < 150:
            pts += 2
        elif sr < 170:
            pts += 4
        else:
            pts += 6

    return pts


# ---------------------------------------------------------------------------
# Bowling points
# ---------------------------------------------------------------------------

def _bowling_points(bowl: dict, lbw_bowled_count: int) -> int:
    wickets    = _safe_int(bowl.get("Wickets"))
    maidens    = _safe_int(bowl.get("Maidens"))
    runs_given = _safe_int(bowl.get("Runs"))
    dots       = _safe_int(bowl.get("DotBalls"))
    balls_bowled  = _overs_to_balls(bowl.get("Overs", 0))
    overs_decimal = balls_bowled / 6.0

    pts  = dots    * 1
    pts += wickets * 30
    pts += maidens * 12
    pts += lbw_bowled_count * 8

    # Haul bonuses
    if wickets >= 5:
        pts += 12
    elif wickets >= 4:
        pts += 8
    elif wickets >= 3:
        pts += 4

    # Economy (min 2 overs)
    if overs_decimal >= ECO_MIN_OVERS and balls_bowled > 0:
        eco = (runs_given / balls_bowled) * 6
        if eco < 5:
            pts += 6
        elif eco < 6:
            pts += 4
        elif eco < 7:
            pts += 2
        elif eco < 10:
            pts += 0
        elif eco < 11:
            pts -= 2
        elif eco < 12:
            pts -= 4
        else:
            pts -= 6

    return pts


# ---------------------------------------------------------------------------
# Fielding — derived from ManhattanWickets OutDesc
# ---------------------------------------------------------------------------

def _parse_fielding(manhattan_wkts: list, innings_no: int, lookup: dict) -> dict:
    """
    Parse ManhattanWickets to extract fielding contributions.
    Uses lookup to resolve OutDesc names (no suffix) to canonical names (with suffix).
    Filters by InningsNo to prevent double-counting.
    """
    fielding: dict[str, dict] = {}

    def _add(raw: str, key: str):
        name = _resolve_player(raw, lookup)
        if not name:
            return
        rec = fielding.setdefault(name, {
            "catches": 0, "stumpings": 0,
            "run_out_direct": 0, "run_out_indirect": 0,
        })
        rec[key] += 1

    for entry in manhattan_wkts:
        # Filter to only process wickets for this innings
        row_innings = _safe_int(entry.get("InningsNo"), default=-1)
        if row_innings != innings_no:
            continue

        desc = str(entry.get("OutDesc", "")).strip()
        if not desc:
            continue

        desc_lower = desc.lower()

        # caught and bowled  →  "c & b BowlerName"
        if re.match(r"c\s*&\s*b\s+", desc_lower):
            m = re.match(r"c\s*&\s*b\s+(.+)", desc, re.IGNORECASE)
            if m:
                _add(m.group(1).strip(), "catches")

        # caught  →  "c FielderName b BowlerName"
        elif desc_lower.startswith("c ") and " b " in desc_lower:
            m = re.match(r"c\s+(.+?)\s+b\s+", desc, re.IGNORECASE)
            if m:
                fielder = m.group(1).strip()
                if fielder.lower() not in ("", "sub"):
                    _add(fielder, "catches")

        # stumped  →  "st †KeeperName b BowlerName"
        elif desc_lower.startswith("st "):
            m = re.match(r"st\s+[†]?(.+?)\s+b\s+", desc, re.IGNORECASE)
            if m:
                _add(m.group(1).strip(), "stumpings")

        # run out  →  "run out (FielderA/FielderB)" or "run out (FielderA)"
        elif "run out" in desc_lower:
            m = re.search(r"\((.+?)\)", desc)
            if m:
                names = m.group(1).split("/")
                if len(names) == 1:
                    _add(names[0].strip(), "run_out_direct")
                else:
                    _add(names[0].strip(), "run_out_direct")
                    _add(names[1].strip(), "run_out_indirect")

    return fielding


def _parse_lbw_bowled(over_history: list, innings_no: int, lookup: dict) -> dict:
    """
    Count LBW/Bowled wickets per bowler from OverHistory (filtered by InningsNo).
    Returns {canonical_bowler_name: count}
    """
    result: dict[str, int] = {}
    for ball in over_history:
        row_innings = _safe_int(ball.get("InningsNo"), default=-1)
        if row_innings != innings_no:
            continue
        is_wicket = str(ball.get("IsWicket", "0")).strip() == "1"
        if not is_wicket:
            continue
        wtype = str(ball.get("WicketType", "")).strip().lower()
        if wtype in ("lbw", "bowled"):
            bowler = _resolve_player(ball.get("BowlerName", ""), lookup)
            if bowler:
                result[bowler] = result.get(bowler, 0) + 1
    return result


def _fielding_points(catches: int, stumpings: int,
                     run_out_direct: int, run_out_indirect: int) -> int:
    pts  = catches * 8
    pts += 4 if catches >= 3 else 0   # 3-catch bonus
    pts += stumpings * 12
    pts += run_out_direct * 12
    pts += run_out_indirect * 6
    return pts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_points(innings1_raw: dict | None, innings2_raw: dict | None) -> list[dict]:
    """
    Calculate Dream11 fantasy points from one or two innings dicts.
    Returns a ranked list of player records.
    """
    innings1 = _unwrap(innings1_raw, "Innings1")
    innings2 = _unwrap(innings2_raw, "Innings2")

    # normalized_name → record
    players: dict[str, dict] = {}

    def _ensure(name: str, team: str) -> dict:
        key = normalize_name(name)
        if key not in players:
            players[key] = {
                "player": key,      # store normalized name
                "team": team,
                "bat": 0, "bowl": 0, "field": 0, "play": 4,
                "total": 0,
                "catches": 0, "stumpings": 0,
                "run_out_direct": 0, "run_out_indirect": 0,
            }
        return players[key]

    # Build GLOBAL name lookup from ALL cards across both innings.
    # This is critical: fielders in Innings2 may only appear in Innings1's
    # BattingCard (e.g. Devdutt Padikkal (IP) bats in Inn1, fields in Inn2).
    all_batting1  = innings1.get("BattingCard", []) or []
    all_bowling1  = innings1.get("BowlingCard", []) or []
    all_batting2  = innings2.get("BattingCard", []) or []
    all_bowling2  = innings2.get("BowlingCard", []) or []
    global_lookup = _build_base_to_canonical(
        all_batting1 + all_batting2,
        all_bowling1 + all_bowling2,
    )

    # Process each innings
    for inn_data, inn_label, inn_no in [
        (innings1, "Innings1", 1),
        (innings2, "Innings2", 2),
    ]:
        if not inn_data:
            continue

        extras       = (inn_data.get("Extras") or [{}])[0]
        batting_team = str(extras.get("BattingTeamName", "")).strip()
        bowling_team = str(extras.get("BowlingTeamName", "")).strip()
        batting_card = inn_data.get("BattingCard", []) or []
        bowling_card = inn_data.get("BowlingCard", []) or []
        over_history = inn_data.get("OverHistory", []) or []
        mw           = inn_data.get("ManhattanWickets", []) or []

        # LBW/Bowled counts per bowler (filtered to this innings)
        lbw_bowled_map = _parse_lbw_bowled(over_history, inn_no, global_lookup)

        # Fielding credits — use global lookup, filtered to this innings only
        fielding_stats = _parse_fielding(mw, inn_no, global_lookup)

        # --- Batting ---
        bowler_names = {_resolve_player(b.get("PlayerName", ""), global_lookup) for b in bowling_card}

        for idx, bat in enumerate(batting_card):
            name = _resolve_player(bat.get("PlayerName", ""), global_lookup)
            if not name:
                continue

            # Heuristic for "Pure Bowler" (Duck penalty bypass):
            # A player is classified as a pure bowler if they bat at position 8 or below (index 7+)
            # AND they actually bowled in the match (are in the BowlingCard).
            # If they have "(wk)" in their name, they are never a pure bowler.
            is_wk = "(wk)" in name.lower()
            is_pure_bowler = (idx >= 7) and (name in bowler_names) and not is_wk

            _ensure(name, batting_team)["bat"] += _batting_points(bat, is_bowler=is_pure_bowler)

        # --- Bowling ---
        for bowl in bowling_card:
            name = _resolve_player(bowl.get("PlayerName", ""), global_lookup)
            if not name:
                continue
            rec = _ensure(name, bowling_team)
            rec["bowl"] += _bowling_points(bowl, lbw_bowled_map.get(name, 0))

        # --- Fielding credits → bowling team fielders ---
        for fielder_name, fstats in fielding_stats.items():
            rec = _ensure(fielder_name, bowling_team)
            rec["catches"]          += fstats["catches"]
            rec["stumpings"]        += fstats["stumpings"]
            rec["run_out_direct"]   += fstats["run_out_direct"]
            rec["run_out_indirect"] += fstats["run_out_indirect"]

        # --- Playing XI: anyone in batting or bowling card ---
        playing_names: set[str] = set()
        for bat in batting_card:
            n = _resolve_player(bat.get("PlayerName", ""), global_lookup)
            if n:
                playing_names.add(n)
        for bowl in bowling_card:
            n = _resolve_player(bowl.get("PlayerName", ""), global_lookup)
            if n:
                playing_names.add(n)

        batting_names = {_resolve_player(b.get("PlayerName", ""), global_lookup) for b in batting_card}
        for name in playing_names:
            team = batting_team if name in batting_names else bowling_team
            _ensure(name, team)["play"] = 4   # set (not cumulate)

    # --- Compute fielding pts and total ---
    for rec in players.values():
        rec["field"] = _fielding_points(
            rec["catches"], rec["stumpings"],
            rec["run_out_direct"], rec["run_out_indirect"],
        )
        rec["total"] = rec["bat"] + rec["bowl"] + rec["field"] + rec["play"]

    # --- Sort by total descending, assign ranks ---
    sorted_players = sorted(players.values(), key=lambda x: x["total"], reverse=True)
    ranked = []
    current_rank = 1
    for i, rec in enumerate(sorted_players):
        if i > 0 and rec["total"] < sorted_players[i - 1]["total"]:
            current_rank = i + 1
        ranked.append({**rec, "rank": current_rank})

    return ranked
