"""
fantasy_engine.py — Dream11-style fantasy point calculator.

Input:  innings1 dict  (full parsed JSON from Innings1.js)
        innings2 dict  (full parsed JSON from Innings2.js)
        Both dicts are wrapped under 'Innings1'/'Innings2' key respectively.

Output: sorted list of player dicts with full breakdown + rank.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SR_MIN_BALLS = 10
SR_MIN_RUNS  = 20
ECO_MIN_OVERS = 2.0   # floating overs


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _safe_int(val, default=0) -> int:
    try:
        return int(val) if val not in (None, "", "null") else default
    except (TypeError, ValueError):
        return default


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if val not in (None, "", "null") else default
    except (TypeError, ValueError):
        return default


def _overs_to_balls(overs_val) -> int:
    """Convert '3.2' → 20 balls, '4' → 24 balls (IPL over notation)."""
    try:
        overs = float(overs_val) if overs_val else 0.0
        full    = int(overs)
        partial = round((overs - full) * 10)   # e.g. 3.2 → 2 extra balls
        return full * 6 + partial
    except (TypeError, ValueError):
        return 0


def _unwrap(innings_dict: dict | None, key: str) -> dict:
    """
    The feed wraps data under 'Innings1' or 'Innings2' key.
    E.g.  { 'Innings1': { 'BattingCard': [...], ... } }
    Returns the inner dict, or {} if missing.
    """
    if not innings_dict:
        return {}
    return innings_dict.get(key, {})


# ---------------------------------------------------------------------------
# Batting
# ---------------------------------------------------------------------------

def _batting_points(bat: dict) -> int:
    """
    BattingCard fields used:
      PlayerName, Runs, Balls (= balls faced), Fours, Sixes,
      OutDesc (e.g. 'not out', 'lbw', 'b Bumrah', 'c Kohli b ...')
    """
    runs        = _safe_int(bat.get("Runs"))
    balls_faced = _safe_int(bat.get("Balls"))
    fours       = _safe_int(bat.get("Fours"))
    sixes       = _safe_int(bat.get("Sixes"))
    out_desc    = str(bat.get("OutDesc") or bat.get("ShortOutDesc") or "").strip().lower()

    # Dismissed if OutDesc is not 'not out' / empty / 'dnb'
    is_out = out_desc not in ("", "not out", "dnb", "did not bat")

    pts = 0
    pts += runs  * 1
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

    # Duck
    if is_out and runs == 0:
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
# Bowling — from BowlingCard
# ---------------------------------------------------------------------------

def _bowling_points(bowl: dict, lbw_bowled_count: int) -> int:
    """
    BowlingCard fields used:
      PlayerName, Overs, Maidens, Runs, Wickets, DotBalls
    lbw_bowled_count: derived separately from OverHistory.
    """
    wickets    = _safe_int(bowl.get("Wickets"))
    maidens    = _safe_int(bowl.get("Maidens"))
    runs_given = _safe_int(bowl.get("Runs"))
    overs_raw  = bowl.get("Overs", 0)
    dots       = _safe_int(bowl.get("DotBalls"))

    balls_bowled  = _overs_to_balls(overs_raw)
    overs_decimal = balls_bowled / 6.0

    pts = 0
    pts += dots    * 1
    pts += wickets * 30
    pts += maidens * 12
    pts += lbw_bowled_count * 8   # LBW / Bowled bonus

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
# Fielding — derived from OverHistory
# ---------------------------------------------------------------------------

def _parse_fielding_and_lbw(over_history: list) -> tuple[dict, dict]:
    """
    Parse OverHistory balls to extract:
      - fielding: {player_name: {catches, stumpings, run_out_direct, run_out_indirect}}
      - lbw_bowled_map: {bowler_name: count_of_lbw_or_bowled_wickets}

    OverHistory field used:
      WicketType (string), IsBowlerWicket ('1'/'0'), BowlerName,
      IsWicket ('1'/'0').
    """
    fielding: dict[str, dict] = {}
    lbw_bowled_map: dict[str, int] = {}

    def _add_field(name: str, key: str):
        name = (name or "").strip()
        if not name:
            return
        rec = fielding.setdefault(name, {
            "catches": 0, "stumpings": 0,
            "run_out_direct": 0, "run_out_indirect": 0,
        })
        rec[key] += 1

    for ball in over_history:
        is_wicket = str(ball.get("IsWicket", "0")).strip() == "1"
        if not is_wicket:
            continue

        wtype        = str(ball.get("WicketType", "")).strip().lower()
        bowler_name  = str(ball.get("BowlerName", "")).strip()
        # Some feeds include a Fielder name — use BatsManName context isn't useful here
        # WicketType values seen: 'caught', 'bowled', 'lbw', 'stumped',
        #   'run out', 'caught and bowled', 'hit wicket', 'obstructing the field'

        if wtype in ("lbw", "bowled"):
            if bowler_name:
                lbw_bowled_map[bowler_name] = lbw_bowled_map.get(bowler_name, 0) + 1

        elif wtype == "caught":
            # The fielder name isn't always in OverHistory;
            # fall back to BowlerName only for "caught and bowled"
            pass   # handled via ManhattanWickets below

        elif wtype == "caught and bowled":
            _add_field(bowler_name, "catches")

        elif wtype == "stumped":
            # Stumping — no fielder name in OverHistory usually
            pass

        elif wtype == "run out":
            pass   # handled below

    return fielding, lbw_bowled_map


def _parse_fielding_from_manhattan(manhattan_wickets: list,
                                   fall_of_wickets: list) -> dict:
    """
    ManhattanWickets fields:
      OutBatsman, OutDesc, BatsmanRuns, BatsmanBalls

    OutDesc examples:
      "c Kohli b Bumrah", "st †Saha b Chahal", "run out (Sharma)",
      "lbw b Kumar", "b Bumrah", "c & b Shami"

    Returns fielding dict: {player_name: {catches, stumpings, run_out_direct, run_out_indirect}}
    """
    import re
    fielding: dict[str, dict] = {}

    def _add(name: str, key: str):
        name = (name or "").strip()
        # Strip keeper dagger
        name = name.lstrip("†").strip()
        if not name:
            return
        rec = fielding.setdefault(name, {
            "catches": 0, "stumpings": 0,
            "run_out_direct": 0, "run_out_indirect": 0,
        })
        rec[key] += 1

    for entry in manhattan_wickets:
        desc = str(entry.get("OutDesc", "")).strip().lower()
        if not desc:
            continue

        # caught and bowled
        if re.match(r"c\s*&\s*b\s+", desc):
            m = re.match(r"c\s*&\s*b\s+(.+)", desc)
            if m:
                _add(m.group(1).strip().title(), "catches")

        # caught
        elif desc.startswith("c ") and " b " in desc:
            # "c Kohli b Bumrah"
            m = re.match(r"c\s+(.+?)\s+b\s+", desc)
            if m:
                fielder = m.group(1).strip()
                if fielder.lower() not in ("", "sub"):
                    _add(fielder.title(), "catches")

        # stumped
        elif desc.startswith("st "):
            # "st †Saha b Chahal"
            m = re.match(r"st\s+[†]?(.+?)\s+b\s+", desc)
            if m:
                _add(m.group(1).strip().title(), "stumpings")

        # run out
        elif "run out" in desc:
            # "run out (Sharma/Chahal)" or "run out (Sharma)"
            m = re.search(r"\((.+?)\)", desc)
            if m:
                names = m.group(1).split("/")
                if len(names) == 1:
                    _add(names[0].strip().title(), "run_out_direct")
                else:
                    # First is direct, second is indirect
                    _add(names[0].strip().title(), "run_out_direct")
                    _add(names[1].strip().title(), "run_out_indirect")
            else:
                pass  # can't parse, skip

    return fielding


def _merge_fielding(*dicts) -> dict:
    """Merge multiple fielding dicts, summing counts."""
    merged: dict[str, dict] = {}
    for d in dicts:
        for name, stats in d.items():
            rec = merged.setdefault(name, {
                "catches": 0, "stumpings": 0,
                "run_out_direct": 0, "run_out_indirect": 0,
            })
            for k in rec:
                rec[k] += stats.get(k, 0)
    return merged


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
    Calculate Dream11 fantasy points.
    innings1_raw / innings2_raw are the full parsed dicts from the feeds,
    e.g. { "Innings1": { "BattingCard": [...], ... } }

    Returns ranked list of player dicts.
    """
    innings1 = _unwrap(innings1_raw, "Innings1")
    innings2 = _unwrap(innings2_raw, "Innings2")

    players: dict[str, dict] = {}   # player_name → record

    def _ensure(name: str, team: str) -> dict:
        if name not in players:
            players[name] = {
                "player": name,
                "team": team,
                "bat": 0, "bowl": 0, "field": 0, "play": 4,
                "total": 0,
                "catches": 0, "stumpings": 0,
                "run_out_direct": 0, "run_out_indirect": 0,
            }
        return players[name]

    # ---------- process each innings -----------
    for inn_data, inn_label in [(innings1, "Innings1"), (innings2, "Innings2")]:
        if not inn_data:
            continue

        extras        = (inn_data.get("Extras") or [{}])[0]
        batting_team  = str(extras.get("BattingTeamName", "")).strip()
        bowling_team  = str(extras.get("BowlingTeamName", "")).strip()
        batting_card  = inn_data.get("BattingCard", []) or []
        bowling_card  = inn_data.get("BowlingCard", []) or []
        over_history  = inn_data.get("OverHistory", []) or []
        manhattan_wkts= inn_data.get("ManhattanWickets", []) or []

        # --- LBW/bowled counts per bowler (from OverHistory) ---
        _, lbw_bowled_map = _parse_fielding_and_lbw(over_history)

        # --- Fielding from ManhattanWickets OutDesc ---
        fielding_stats = _parse_fielding_from_manhattan(manhattan_wkts, [])

        # --- Batting ---
        for bat in batting_card:
            name = str(bat.get("PlayerName", "")).strip()
            if not name:
                continue
            rec = _ensure(name, batting_team)
            rec["bat"] += _batting_points(bat)

        # --- Bowling ---
        for bowl in bowling_card:
            name = str(bowl.get("PlayerName", "")).strip()
            if not name:
                continue
            rec = _ensure(name, bowling_team)
            lbc = lbw_bowled_map.get(name, 0)
            rec["bowl"] += _bowling_points(bowl, lbc)

        # --- Fielding credits ---
        for fielder_name, fstats in fielding_stats.items():
            rec = _ensure(fielder_name, bowling_team)
            rec["catches"]          += fstats["catches"]
            rec["stumpings"]        += fstats["stumpings"]
            rec["run_out_direct"]   += fstats["run_out_direct"]
            rec["run_out_indirect"] += fstats["run_out_indirect"]

        # --- Playing XI (+4 for appearing in batting/bowling card) ---
        playing_names: set[str] = set()
        for bat in batting_card:
            n = str(bat.get("PlayerName", "")).strip()
            if n:
                playing_names.add(n)
        for bowl in bowling_card:
            n = str(bowl.get("PlayerName", "")).strip()
            if n:
                playing_names.add(n)

        for name in playing_names:
            _ensure(name, batting_team if name in {
                str(b.get("PlayerName", "")).strip() for b in batting_card
            } else bowling_team)["play"] = 4   # set, not cumulate

    # --- Compute fielding pts + total ---
    for rec in players.values():
        rec["field"] = _fielding_points(
            rec["catches"], rec["stumpings"],
            rec["run_out_direct"], rec["run_out_indirect"],
        )
        rec["total"] = rec["bat"] + rec["bowl"] + rec["field"] + rec["play"]

    # --- Sort and rank ---
    sorted_players = sorted(players.values(), key=lambda x: x["total"], reverse=True)
    ranked = []
    current_rank = 1
    for i, rec in enumerate(sorted_players):
        if i > 0 and rec["total"] < sorted_players[i - 1]["total"]:
            current_rank = i + 1
        ranked.append({**rec, "rank": current_rank})

    return ranked
