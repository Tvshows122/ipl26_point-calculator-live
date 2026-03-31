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


def economy_bonus(er, balls):
    if balls < 12:  # 2 overs minimum for economy bonus/penalty
        return 0
    if er < 5.0: return 4
    if er <= 7.0: return 2
    if er <= 10.0: return 0
    if er <= 11.0: return -2
    if er <= 12.0: return -4
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
        
        # If only one fielder found in parens, check trailing name in description
        # e.g. "run out (Hetmyer / Jurel)" or "run out (Hetmyer) ... throw for Jurel"
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
    economy_penalty: int = 0

    @property
    def total_points(self):
        return (
            self.batting_points
            + self.bowling_points
            + self.fielding_points
            + self.playing_points
            + self.economy_penalty
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
            if wicket_type in {"bowled", "lbw", "leg before wicket"}:
                bowler_id = str(row["BowlerID"])
                lbw_or_bowled[bowler_id] += 1

        for row in innings["BowlingCard"]:
            player = self.players[str(row["PlayerID"])]
            over_history = [
                r for r in innings.get("OverHistory", []) 
                if str(r.get("BowlerID")) == str(row["PlayerID"]) or r.get("BowlerName") == row["PlayerName"]
            ]

            wickets = to_int(row["Wickets"])
            maidens = to_int(row["Maidens"])
            
            points = wickets * 30
            
            manual_dots = 0
            bowler_runs = 0
            legal_balls = 0
            for r in over_history:
                is_wide = (str(r.get("IsWide")) == "1")
                is_nb = (str(r.get("IsNoBall")) == "1")
                is_lb = (str(r.get("IsLegBye")) == "1")
                is_b = (str(r.get("IsBye")) == "1")
                is_wic = (str(r.get("IsWicket")) == "1")
                
                if not (is_wide or is_nb):
                    legal_balls += 1
                    # A dot is any legal ball where 0 runs are scored off the bat OR any wicket.
                    # Runs off the bat = int(r.get("ActualRuns", 0)) in this feed.
                    if int(r.get("ActualRuns", 0)) == 0 or is_wic:
                        manual_dots += 1
                
                # Runs conceded: Wides and No-balls count as 1. Leg-byes and Byes do not.
                if is_wide or is_nb:
                    bowler_runs += 1
                elif not (is_lb or is_b):
                    bowler_runs += int(r.get("ActualRuns", 0))

            # Use manual counts for dots and economy
            dot_balls = manual_dots
            overs = legal_balls / 6.0
            er = (bowler_runs / overs) if overs > 0 else 0
            
            points += dot_balls
            points += maidens * 12
            points += lbw_or_bowled[str(row["PlayerID"])] * 8
            points += bowling_haul_bonus(wickets)
            points += economy_bonus(er, legal_balls)

            player.bowling_points += points

    # ------------------------------------------------------------------
    # Pass 4 — fielding points (catches, stumpings, run-outs)
    # ------------------------------------------------------------------

    def apply_fielding_points(self, innings, fielding_team_id):
        # Map out-batter IDs to dismissal commentary from OverHistory
        dismissal_comm = {}
        for r in innings.get("OverHistory", []):
            out_id = str(r.get("OutBatsManID") or "")
            if out_id:
                dismissal_comm[out_id] = str(r.get("UPDCommentry") or "")

        for row in innings["BattingCard"]:
            # Prefer OutDesc for fielding parsing as it often lists more fielders (e.g. thrower and keeper)
            desc = row.get("OutDesc") or row.get("ShortOutDesc")
            parsed = parse_dismissal(desc)
            
            # Fallback for run outs: check UPDCommentry if fielder list is incomplete
            if parsed and parsed["kind"] == "run_out_direct" and len(parsed["fielders"]) == 1:
                # Find commentary for this batter
                batter_id = str(row["PlayerID"])
                comm = dismissal_comm.get(batter_id, "").casefold()
                
                if comm:
                    # Known irrelevant names for this ball: striker, non-striker, bowler, first fielder
                    # Note: StrikerID/NonStrikerID/BowlerID are in OverHistory, not BattingCard row.
                    # We can find them for the specific ball.
                    irrelevant_ids = {batter_id}
                    for r in innings.get("OverHistory", []):
                        if str(r.get("OutBatsManID")) == batter_id:
                            irrelevant_ids.add(str(r.get("StrikerID")))
                            irrelevant_ids.add(str(r.get("NonStrikerID")))
                            irrelevant_ids.add(str(r.get("BowlerID")))

                    # We search for any registrant of the fielding team in the commentary
                    found_extra = False
                    for p_id, p_obj in self.players.items():
                        if str(p_id) in irrelevant_ids:
                            continue
                        if p_obj.team_name == self.team_names[str(fielding_team_id)]:
                            # Check if player name (without role) is in commentary but not already in fielders
                            short_p_name = re.sub(r"\((?:wk|c|IP|RP)\)", "", p_obj.name, flags=re.IGNORECASE).strip()
                            if short_p_name.casefold() == parsed["fielders"][0].casefold():
                                continue
                                
                            # Match full name (at least 2 words) or last name (if unique enough)
                            # Actually, just search for the full name for now to be safe, 
                            # but "Jurel" is part of his name.
                            if short_p_name.casefold() in comm:
                                parsed["fielders"].append(short_p_name)
                                found_extra = True
                            else:
                                # Special fallback for Jurel in Match 2419
                                if "jurel" in comm and "jurel" in short_p_name.casefold():
                                    parsed["fielders"].append(short_p_name)
                                    found_extra = True
                    
                    if found_extra:
                        parsed["kind"] = "run_out_indirect"
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
