"""
fantasy_engine.py — Dream11-style Fantasy Points Calculator

Implements the official IPL Fantasy scoring system.
Adapted directly from the robust PlayerID-based FantasyCalculator to inherently
resolve naming mismatches safely across Batting/Bowling cards and properly apply
out descriptions.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, asdict

PLAYING_BONUS = 4


def to_int(value):
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else 0


def overs_to_balls(overs_value):
    text = str(overs_value).strip()
    if not text:
        return 0
    if "." not in text:
        return int(text) * 6
    overs, balls = text.split(".", 1)
    return int(overs) * 6 + int(balls)


def canonical_name(name):
    text = str(name or "")
    text = re.sub(r"\((?:wk|c|IP|RP)\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def display_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


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
    if strike_rate >= 170: return 6
    if strike_rate >= 150: return 4
    if strike_rate >= 130: return 2
    if strike_rate >= 70: return 0
    if strike_rate >= 60: return -2
    if strike_rate >= 50: return -4
    return -6


def economy_bonus(runs_conceded, balls_bowled):
    if balls_bowled < 12:
        return 0
    economy = (runs_conceded * 6) / balls_bowled
    if economy < 5: return 6
    if economy < 6: return 4
    if economy < 7: return 2
    if economy < 10: return 0
    if economy < 11: return -2
    if economy < 12: return -4
    return -6


def bowling_haul_bonus(wickets):
    if wickets >= 5: return 12
    if wickets >= 4: return 8
    if wickets >= 3: return 4
    return 0


def parse_dismissal(description):
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    lowered = text.casefold()
    if not text or lowered == "not out": return None
    
    caught_bowled = re.match(r"^c\s*&\s*b\s+(.+)$", text, flags=re.IGNORECASE)
    if caught_bowled: return {"kind": "catch", "fielders": [caught_bowled.group(1).strip()]}
    
    caught = re.match(r"^c\s+(.+?)\s+b\s+(.+)$", text, flags=re.IGNORECASE)
    if caught: return {"kind": "catch", "fielders": [caught.group(1).strip()]}
    
    stumped = re.match(r"^st\s+(.+?)\s+b\s+(.+)$", text, flags=re.IGNORECASE)
    if stumped: return {"kind": "stumping", "fielders": [stumped.group(1).strip()]}
    
    run_out = re.match(r"^run out\s*\((.+)\)$", text, flags=re.IGNORECASE)
    if run_out:
        f = run_out.group(1)
        fielders = [p.strip() for p in re.split(r"/|,", f) if p.strip()]
        if len(fielders) <= 1: return {"kind": "run_out_direct", "fielders": fielders}
        return {"kind": "run_out_indirect", "fielders": fielders}
        
    return None


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
        return self.batting_points + self.bowling_points + self.fielding_points + self.playing_points


class FantasyCalculator:
    def __init__(self):
        self.players = {}
        self.name_lookup = defaultdict(dict)
        self.team_names = {}

    def get_player(self, player_id, name, team_id, team_name):
        player_id = str(player_id)
        team_id = str(team_id)
        self.team_names[team_id] = display_name(team_name)
        if player_id not in self.players:
            self.players[player_id] = PlayerPoints(player_id, display_name(name), self.team_names[team_id])
        else:
            p = self.players[player_id]
            if not p.name: p.name = display_name(name)
            if not p.team_name: p.team_name = self.team_names[team_id]
        self.name_lookup[team_id][canonical_name(name)] = player_id
        return self.players[player_id]

    def resolve_player_id(self, team_id, name):
        return self.name_lookup[str(team_id)].get(canonical_name(name))

    def score_match(self, innings_list):
        for inn in innings_list:
            if not inn or not inn.get("BattingCard"):
                continue
            extras = inn.get("Extras", [{}])[0]
            bat_team = extras.get("BattingTeamName", "Unknown")
            bowl_team = extras.get("BowlingTeamName", "Unknown")
            bat_team_id = str(inn["BattingCard"][0]["TeamID"])
            try:
                bowl_team_id = str(inn["BowlingCard"][0]["TeamID"])
            except:
                bowl_team_id = None
            
            for r in inn["BattingCard"]:
                self.get_player(r["PlayerID"], r["PlayerName"], bat_team_id, bat_team).played = True
            for r in inn.get("BowlingCard", []):
                self.get_player(r["PlayerID"], r["PlayerName"], bowl_team_id, bowl_team).played = True

            for r in inn["BattingCard"]:
                p = self.players[str(r["PlayerID"])]
                runs = to_int(r["Runs"])
                balls = to_int(r["Balls"])
                pts = runs + to_int(r["Fours"])*4 + to_int(r["Sixes"])*6
                pts += batting_milestone_bonus(runs) + strike_rate_bonus(runs, balls)
                d = str(r.get("ShortOutDesc", "")).strip()
                if d and d.casefold() != "not out" and runs == 0 and balls >= 0:
                    pts -= 2
                p.batting_points += pts
            
            lbw_map = defaultdict(int)
            for r in inn.get("OverHistory", []):
                if to_int(r.get("IsWicket")) == 1 and to_int(r.get("IsBowlerWicket")) == 1:
                    if str(r.get("WicketType", "")).strip().casefold() in {"bowled", "lbw"}:
                        lbw_map[str(r["BowlerID"])] += 1
            
            for r in inn.get("BowlingCard", []):
                p = self.players[str(r["PlayerID"])]
                w = to_int(r["Wickets"])
                balls = overs_to_balls(r.get("Overs"))
                pts = w*30 + to_int(r["DotBalls"]) + to_int(r["Maidens"])*12
                pts += lbw_map[str(r["PlayerID"])]*8
                pts += bowling_haul_bonus(w) + economy_bonus(to_int(r["Runs"]), balls)
                p.bowling_points += pts

            if bowl_team_id:
                for r in inn["BattingCard"]:
                    parsed = parse_dismissal(r.get("ShortOutDesc"))
                    if parsed:
                        for fn in parsed["fielders"]:
                            f_id = self.resolve_player_id(bowl_team_id, fn)
                            if f_id:
                                fp = self.players[f_id]
                                if parsed["kind"] == "catch":
                                    fp.catches += 1
                                    fp.fielding_points += 8
                                elif parsed["kind"] == "stumping":
                                    fp.stumpings += 1
                                    fp.fielding_points += 12
                                elif parsed["kind"] == "run_out_direct":
                                    fp.run_out_direct += 1
                                    fp.fielding_points += 12
                                elif parsed["kind"] == "run_out_indirect":
                                    fp.run_out_indirect += 1
                                    fp.fielding_points += 6
        
        for p in self.players.values():
            if p.catches >= 3:
                p.fielding_points += 4
            if p.played:
                p.playing_points = PLAYING_BONUS

    def results(self):
        # Sort descending by total score, then by team name, then by player name
        rows = sorted(
            self.players.values(), 
            key=lambda x: (-x.total_points, x.team_name, x.name)
        )
        out = []
        for i, r in enumerate(rows):
            d = asdict(r)
            d["rank"] = i + 1
            # Rename internal keys back to the expected API spec strings
            d["bat"] = d.pop("batting_points")
            d["bowl"] = d.pop("bowling_points")
            d["field"] = d.pop("fielding_points")
            d["play"] = d.pop("playing_points")
            d["player"] = d.pop("name")
            d["team"] = d.pop("team_name")
            d["total"] = r.total_points
            del d["player_id"]
            del d["played"]
            out.append(d)
        return out


def calculate_points(innings1_raw: dict | None, innings2_raw: dict | None) -> list[dict]:
    """
    Given raw innings payload wrapped directly from S3 (e.g. `{"Innings1": {...}}`),
    combines and parses them natively calculating Dream11 compliant statistics via ID refs.
    """
    def _unwrap(i, key):
        if not i:
            return {}
        # In case the payload has the "InningsX" metadata wrapped wrapper:
        if key in i:
            return i[key]
        return i

    inn1 = _unwrap(innings1_raw, "Innings1")
    inn2 = _unwrap(innings2_raw, "Innings2")

    calc = FantasyCalculator()
    calc.score_match([inn1, inn2])
    return calc.results()
