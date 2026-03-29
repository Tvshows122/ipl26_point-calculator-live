"""
fantasy_engine.py — Dream11-style Fantasy Points Calculator

Implements the official IPL Fantasy scoring system.
Internal engine is an exact replica of Logic 2 (modular structure, identical
calculation order and edge-case handling). The public `calculate_points` API
returns output in the Logic 1 format for backward compatibility.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, asdict

PLAYING_BONUS = 4


# ---------------------------------------------------------------------------
# Safe parsing helpers
# ---------------------------------------------------------------------------

def to_int(value):
    """Parse a value to int, returning 0 for None/empty/unparseable.

    Uses re.match (anchored at start) to match Logic 2 exactly.
    """
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    match = re.match(r"-?\d+", text)
    return int(match.group()) if match else 0


def overs_to_balls(overs_value):
    """Convert an overs string like '4.3' to total balls (27)."""
    text = str(overs_value).strip()
    if not text:
        return 0
    if "." not in text:
        return int(text) * 6
    overs, balls = text.split(".", 1)
    return int(overs) * 6 + int(balls)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def canonical_name(name):
    """Strip role annotations and casefold for lookup matching."""
    text = str(name or "")
    text = re.sub(r"\((?:wk|c|IP|RP)\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def display_name(name):
    """Collapse whitespace for clean display."""
    return re.sub(r"\s+", " ", str(name or "")).strip()


# ---------------------------------------------------------------------------
# Scoring sub-functions
# ---------------------------------------------------------------------------

def batting_milestone_bonus(runs):
    if runs >= 100:
        return 16
    if runs >= 75:
        return 12
    if runs >= 50:
        return 8
    if runs >= 25:
        return 4
    return 0


def strike_rate_bonus(runs, balls):
    if balls <= 0 or (runs < 20 and balls < 10):
        return 0
    strike_rate = (runs / balls) * 100
    if strike_rate >= 170:
        return 6
    if strike_rate >= 150:
        return 4
    if strike_rate >= 130:
        return 2
    if strike_rate >= 70:
        return 0
    if strike_rate >= 60:
        return -2
    if strike_rate >= 50:
        return -4
    return -6


def economy_bonus(runs_conceded, balls_bowled):
    if balls_bowled < 12:
        return 0
    economy = (runs_conceded * 6) / balls_bowled
    if economy < 5:
        return 6
    if economy < 6:
        return 4
    if economy < 7:
        return 2
    if economy < 10:
        return 0
    if economy < 11:
        return -2
    if economy < 12:
        return -4
    return -6


def bowling_haul_bonus(wickets):
    if wickets >= 5:
        return 12
    if wickets >= 4:
        return 8
    if wickets >= 3:
        return 4
    return 0


# ---------------------------------------------------------------------------
# Dismissal parser
# ---------------------------------------------------------------------------

def parse_dismissal(description):
    """Parse a ShortOutDesc string into a structured dismissal dict.

    Returns None if the batter is not out or the string is empty.
    """
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    lowered = text.casefold()
    if not text or lowered == "not out":
        return None

    caught_bowled = re.match(r"^c\s*&\s*b\s+(.+)$", text, flags=re.IGNORECASE)
    if caught_bowled:
        return {"kind": "catch", "fielders": [caught_bowled.group(1).strip()]}

    caught = re.match(r"^c\s+(.+?)\s+b\s+(.+)$", text, flags=re.IGNORECASE)
    if caught:
        return {"kind": "catch", "fielders": [caught.group(1).strip()]}

    stumped = re.match(r"^st\s+(.+?)\s+b\s+(.+)$", text, flags=re.IGNORECASE)
    if stumped:
        return {"kind": "stumping", "fielders": [stumped.group(1).strip()]}

    run_out = re.match(r"^run out\s*\((.+)\)$", text, flags=re.IGNORECASE)
    if run_out:
        raw_fielders = run_out.group(1)
        fielders = [piece.strip() for piece in re.split(r"/|,", raw_fielders) if piece.strip()]
        if len(fielders) <= 1:
            return {"kind": "run_out_direct", "fielders": fielders}
        return {"kind": "run_out_indirect", "fielders": fielders}

    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PlayerPoints:
    player_id: str
    name: str
    team_name: str
    batting_points: int = 0
    bowling_points: int = 0
    fielding_points: int = 0
    playing_points: int = 0
    catches: int = 0
    stumpings: int = 0
    run_out_direct: int = 0
    run_out_indirect: int = 0
    played: bool = False

    @property
    def total_points(self):
        return (
            self.batting_points
            + self.bowling_points
            + self.fielding_points
            + self.playing_points
        )


# ---------------------------------------------------------------------------
# Calculator — exact Logic 2 modular structure
# ---------------------------------------------------------------------------

class FantasyCalculator:
    def __init__(self):
        self.players = {}
        self.name_lookup = defaultdict(dict)
        self.team_names = {}

    # ------------------------------------------------------------------
    # Player registry
    # ------------------------------------------------------------------

    def get_player(self, player_id, name, team_id, team_name):
        player_id = str(player_id)
        team_id = str(team_id)
        team_name = display_name(team_name)
        self.team_names[team_id] = team_name
        if player_id not in self.players:
            self.players[player_id] = PlayerPoints(
                player_id=player_id,
                name=display_name(name),
                team_name=team_name,
            )
        else:
            player = self.players[player_id]
            if not player.name:
                player.name = display_name(name)
            if not player.team_name:
                player.team_name = team_name
        self.name_lookup[team_id][canonical_name(name)] = player_id
        return self.players[player_id]

    def resolve_player_id(self, team_id, name):
        return self.name_lookup[str(team_id)].get(canonical_name(name))

    # ------------------------------------------------------------------
    # Pass 1 — register all players and mark them as played
    # ------------------------------------------------------------------

    def register_innings_players(self, innings):
        extras = innings["Extras"][0]
        batting_team_name = extras["BattingTeamName"]
        bowling_team_name = extras["BowlingTeamName"]

        batting_team_id = str(innings["BattingCard"][0]["TeamID"])
        bowling_team_id = str(innings["BowlingCard"][0]["TeamID"])

        for row in innings["BattingCard"]:
            player = self.get_player(
                row["PlayerID"],
                row["PlayerName"],
                batting_team_id,
                batting_team_name,
            )
            player.played = True

        for row in innings["BowlingCard"]:
            player = self.get_player(
                row["PlayerID"],
                row["PlayerName"],
                bowling_team_id,
                bowling_team_name,
            )
            player.played = True

        return batting_team_id, bowling_team_id

    # ------------------------------------------------------------------
    # Pass 2 — batting points
    # ------------------------------------------------------------------

    def apply_batting_points(self, innings):
        for row in innings["BattingCard"]:
            player = self.players[str(row["PlayerID"])]

            runs = to_int(row["Runs"])
            balls = to_int(row["Balls"])
            fours = to_int(row["Fours"])
            sixes = to_int(row["Sixes"])
            dismissal = str(row.get("ShortOutDesc") or "").strip()

            points = runs
            points += fours * 4
            points += sixes * 6
            points += batting_milestone_bonus(runs)
            points += strike_rate_bonus(runs, balls)

            # Duck penalty applied to any dismissed batter on zero.
            # DNB rows are skipped naturally (balls >= 0 is always true,
            # so gate is: non-empty dismissal string that is not "not out").
            if dismissal and dismissal.casefold() != "not out" and runs == 0 and balls >= 0:
                points -= 2

            player.batting_points += points

    # ------------------------------------------------------------------
    # Pass 3 — bowling points (includes LBW/bowled bonus via OverHistory)
    # ------------------------------------------------------------------

    def apply_bowling_points(self, innings):
        lbw_or_bowled = defaultdict(int)
        for row in innings["OverHistory"]:
            if to_int(row.get("IsWicket")) != 1 or to_int(row.get("IsBowlerWicket")) != 1:
                continue
            wicket_type = str(row.get("WicketType") or "").strip().casefold()
            if wicket_type in {"bowled", "lbw"}:
                bowler_id = str(row["BowlerID"])
                lbw_or_bowled[bowler_id] += 1

        for row in innings["BowlingCard"]:
            player = self.players[str(row["PlayerID"])]

            wickets = to_int(row["Wickets"])
            dot_balls = to_int(row["DotBalls"])
            maidens = to_int(row["Maidens"])
            runs_conceded = to_int(row["Runs"])
            balls_bowled = overs_to_balls(row["Overs"])

            points = wickets * 30
            points += dot_balls
            points += maidens * 12
            points += lbw_or_bowled[str(row["PlayerID"])] * 8
            points += bowling_haul_bonus(wickets)
            points += economy_bonus(runs_conceded, balls_bowled)

            player.bowling_points += points

    # ------------------------------------------------------------------
    # Pass 4 — fielding points (catches, stumpings, run-outs)
    # ------------------------------------------------------------------

    def apply_fielding_points(self, innings, fielding_team_id):
        for row in innings["BattingCard"]:
            parsed = parse_dismissal(row.get("ShortOutDesc"))
            if not parsed:
                continue
            for fielder_name in parsed["fielders"]:
                player_id = self.resolve_player_id(fielding_team_id, fielder_name)
                if not player_id:
                    continue
                player = self.players[player_id]
                if parsed["kind"] == "catch":
                    player.catches += 1
                    player.fielding_points += 8
                elif parsed["kind"] == "stumping":
                    player.stumpings += 1
                    player.fielding_points += 12
                elif parsed["kind"] == "run_out_direct":
                    player.run_out_direct += 1
                    player.fielding_points += 12
                elif parsed["kind"] == "run_out_indirect":
                    player.run_out_indirect += 1
                    player.fielding_points += 6

    # ------------------------------------------------------------------
    # Pass 5 — fielding bonuses (3+ catches → +4), applied once globally
    # ------------------------------------------------------------------

    def apply_fielding_bonuses(self):
        for player in self.players.values():
            if player.catches >= 3:
                player.fielding_points += 4

    # ------------------------------------------------------------------
    # Pass 6 — playing XI bonus, applied once globally
    # ------------------------------------------------------------------

    def apply_playing_bonus(self):
        for player in self.players.values():
            if player.played:
                player.playing_points = PLAYING_BONUS

    # ------------------------------------------------------------------
    # Orchestrator — matches Logic 2's exact two-phase loop
    # ------------------------------------------------------------------

    def score_match(self, innings_payloads):
        # Phase 1: register all players from every innings first,
        # collecting (innings, batting_team_id, bowling_team_id) tuples.
        innings_with_teams = []
        for innings in innings_payloads:
            batting_team_id, bowling_team_id = self.register_innings_players(innings)
            innings_with_teams.append((innings, batting_team_id, bowling_team_id))

        # Phase 2: apply scoring in per-innings order, then global bonuses.
        for innings, _, bowling_team_id in innings_with_teams:
            self.apply_batting_points(innings)
            self.apply_bowling_points(innings)
            self.apply_fielding_points(innings, bowling_team_id)

        self.apply_fielding_bonuses()
        self.apply_playing_bonus()

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def rows(self):
        """Return PlayerPoints sorted descending by total, then team, then name."""
        return sorted(
            self.players.values(),
            key=lambda player: (-player.total_points, player.team_name, player.name),
        )

    def results(self):
        """Return API-friendly list of dicts (Logic 1 output format)."""
        out = []
        for i, r in enumerate(self.rows()):
            d = asdict(r)
            d["rank"] = i + 1
            d["bat"] = d.pop("batting_points")
            d["bowl"] = d.pop("bowling_points")
            d["field"] = d.pop("fielding_points")
            d["play"] = d.pop("playing_points")
            d["player"] = d.pop("name")
            d["team"] = d.pop("team_name")
            d["total"] = r.total_points
            del d["played"]
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Public API — backward-compatible with Logic 1 callers
# ---------------------------------------------------------------------------

def calculate_points(innings1_raw: dict | None, innings2_raw: dict | None) -> list[dict]:
    """
    Given raw innings payloads (optionally wrapped under "Innings1"/"Innings2" keys),
    calculate Dream11-compliant fantasy points using the Logic 2 engine and return
    results in the Logic 1 API format.

    Args:
        innings1_raw: Dict containing the first innings data, optionally wrapped
                      under an "Innings1" key.
        innings2_raw: Dict containing the second innings data, optionally wrapped
                      under an "Innings2" key.

    Returns:
        List of player point dicts sorted by total points descending.
    """
    def _unwrap(raw, key):
        if not raw:
            return None
        if key in raw:
            return raw[key]
        return raw

    inn1 = _unwrap(innings1_raw, "Innings1")
    inn2 = _unwrap(innings2_raw, "Innings2")

    # Collect only valid (non-None, non-empty) innings payloads.
    innings_payloads = [i for i in (inn1, inn2) if i]

    calc = FantasyCalculator()
    calc.score_match(innings_payloads)
    return calc.results()
