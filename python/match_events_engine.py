"""
Premier League Match Events Engine
Extracts, indexes, and queries granular match timeline events:
- Goalscorers, minute timestamps (e.g. 15', 45+2', 90+7'), assists, penalty/own-goal tags
- Yellow cards, red cards, second yellows with exact minute timestamps
- Substitutions with minute timestamps, players in, and players out
- Match scorelines, finished fixture results, and chronology across 2026/27 and historical seasons
"""

import os
import json
import time
import re
import unicodedata
import urllib.request
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data")
EVENTS_CACHE_FILE = os.path.join(CACHE_DIR, "pl_match_events_cache.json")

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize('NFKD', str(s)).encode('ASCII', 'ignore').decode('utf-8')
    return s.lower().replace("-", " ").replace(".", "").replace("'", "").strip()

TEAM_SYNONYMS: Dict[str, str] = {
    "arsenal": "Arsenal",
    "aston villa": "Aston Villa",
    "villa": "Aston Villa",
    "bournemouth": "Bournemouth",
    "afc bournemouth": "Bournemouth",
    "brentford": "Brentford",
    "brighton": "Brighton & Hove Albion",
    "brighton and hove albion": "Brighton & Hove Albion",
    "chelsea": "Chelsea",
    "coventry": "Coventry City",
    "coventry city": "Coventry City",
    "crystal palace": "Crystal Palace",
    "palace": "Crystal Palace",
    "everton": "Everton",
    "fulham": "Fulham",
    "hull": "Hull City",
    "hull city": "Hull City",
    "ipswich": "Ipswich Town",
    "ipswich town": "Ipswich Town",
    "leeds": "Leeds United",
    "leeds united": "Leeds United",
    "leicester": "Leicester City",
    "leicester city": "Leicester City",
    "liverpool": "Liverpool",
    "man city": "Manchester City",
    "manchester city": "Manchester City",
    "city": "Manchester City",
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "manchester united": "Manchester United",
    "united": "Manchester United",
    "mufc": "Manchester United",
    "newcastle": "Newcastle United",
    "newcastle united": "Newcastle United",
    "nottingham forest": "Nottingham Forest",
    "nottm forest": "Nottingham Forest",
    "forest": "Nottingham Forest",
    "southampton": "Southampton",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "tottenham hotspur": "Tottenham Hotspur",
    "sunderland": "Sunderland",
    "west ham": "West Ham United",
    "west ham united": "West Ham United",
    "wolves": "Wolverhampton Wanderers",
    "wolverhampton": "Wolverhampton Wanderers",
    "wolverhampton wanderers": "Wolverhampton Wanderers"
}

def fetch_json(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[MatchEventsEngine] Error fetching {url}: {e}")
        return None

class MatchEventsEngine:
    def __init__(self):
        self.cached_data: Dict[str, Any] = {}
        self.matches: Dict[str, Dict[str, Any]] = {}
        self.player_events: Dict[str, Dict[str, Any]] = {}
        self.load_cache()

    def load_cache(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Loads cached match details and event timelines from disk."""
        if not force_refresh and os.path.exists(EVENTS_CACHE_FILE):
            try:
                with open(EVENTS_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.cached_data = json.load(f)
                    self.matches = self.cached_data.get("matches", {})
                    self.player_events = self.cached_data.get("player_events", {})
                    return self.cached_data
            except Exception as e:
                print(f"[MatchEventsEngine] Cache read error: {e}")

        # If cache missing or empty, refresh
        return self.refresh_cache()

    def refresh_cache(self) -> Dict[str, Any]:
        """Harvests full match timeline details for all finished Premier League matches."""
        league_url = "https://www.fotmob.com/api/data/leagues?id=47&ccode3=GBR"
        data = fetch_json(league_url)
        if not data:
            return {"success": False, "matches": self.matches}

        fixtures = data.get("fixtures", {})
        all_matches = fixtures.get("allMatches", [])
        finished_matches = [m for m in all_matches if m.get("status", {}).get("finished")]

        matches_data = dict(self.matches)
        missing_summaries = [m for m in finished_matches if str(m["id"]) not in matches_data]

        if missing_summaries:
            print(f"[MatchEventsEngine] Fetching detailed timeline events for {len(missing_summaries)} matches...")
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(self._fetch_single_match_details, missing_summaries))
            for r in results:
                if r:
                    matches_data[str(r["match_id"])] = r

        # Re-index player events
        player_events = self._build_player_index(matches_data)

        payload = {
            "_cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_finished_matches": len(matches_data),
            "matches": matches_data,
            "player_events": player_events
        }

        try:
            with open(EVENTS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[MatchEventsEngine] Cache write error: {e}")

        self.cached_data = payload
        self.matches = matches_data
        self.player_events = player_events
        return payload

    def _fetch_single_match_details(self, match_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mid = str(match_summary["id"])
        url = f"https://www.fotmob.com/api/data/matchDetails?matchId={mid}"
        det = fetch_json(url)
        if not det:
            return None

        content = det.get("content", {})
        facts = content.get("matchFacts", {})
        raw_events = facts.get("events", {}).get("events", [])

        events = []
        goals = []
        cards = []
        substitutions = []

        home_team = match_summary.get("home", {}).get("name", "Home")
        away_team = match_summary.get("away", {}).get("name", "Away")
        round_num = match_summary.get("roundName") or match_summary.get("round")

        for ev in raw_events:
            etype = ev.get("type")
            t = ev.get("time")
            ot = ev.get("overloadTime")
            time_str = f"{t}+{ot}'" if ot else f"{t}'"
            minute = int(t) if t is not None else 0
            is_home = ev.get("isHome", True)
            team_name = home_team if is_home else away_team
            pname = ev.get("player", {}).get("name") or ev.get("nameStr") or "Unknown"
            pid = ev.get("playerId") or ev.get("player", {}).get("id")

            clean_ev = {
                "type": etype,
                "minute": minute,
                "overload": ot,
                "time_str": time_str,
                "is_home": is_home,
                "team": team_name,
                "player_name": pname,
                "player_id": pid
            }

            if etype == "Goal":
                assist_data = ev.get("assistInput")
                assist_name = None
                if isinstance(assist_data, dict):
                    assist_name = assist_data.get("name")
                elif isinstance(assist_data, str):
                    assist_name = assist_data
                elif ev.get("assistStr"):
                    assist_name = ev.get("assistStr")

                clean_ev["assist"] = assist_name
                clean_ev["home_score"] = ev.get("homeScore")
                clean_ev["away_score"] = ev.get("awayScore")
                clean_ev["scoreline"] = f"{ev.get('homeScore')} - {ev.get('awayScore')}"
                clean_ev["is_own_goal"] = bool(ev.get("ownGoal"))
                clean_ev["is_penalty"] = bool(ev.get("penalty"))
                goals.append(clean_ev)

            elif etype == "Card":
                clean_ev["card"] = ev.get("card", "Yellow")
                clean_ev["card_description"] = ev.get("cardDescription")
                cards.append(clean_ev)

            elif etype == "Substitution":
                swap = ev.get("swap", [])
                p_in = swap[0].get("name") if len(swap) > 0 else "Unknown"
                p_out = swap[1].get("name") if len(swap) > 1 else "Unknown"
                clean_ev["player_in"] = p_in
                clean_ev["player_out"] = p_out
                clean_ev["player_in_id"] = swap[0].get("id") if len(swap) > 0 else None
                clean_ev["player_out_id"] = swap[1].get("id") if len(swap) > 1 else None
                substitutions.append(clean_ev)

            events.append(clean_ev)

        return {
            "match_id": mid,
            "round": round_num,
            "gameweek": int(round_num) if str(round_num).isdigit() else round_num,
            "home_team": home_team,
            "away_team": away_team,
            "score": match_summary.get("status", {}).get("scoreStr", "0 - 0"),
            "kickoff_time": match_summary.get("status", {}).get("utcTime"),
            "events_count": len(events),
            "events": events,
            "goals": goals,
            "cards": cards,
            "substitutions": substitutions
        }

    def _build_player_index(self, matches_data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        player_events: Dict[str, Dict[str, Any]] = {}
        for mid, m in matches_data.items():
            for g in m.get("goals", []):
                pn = g.get("player_name")
                if pn:
                    player_events.setdefault(pn, {"goals": [], "cards": [], "substitutions_on": [], "substitutions_off": []})
                    player_events[pn]["goals"].append({
                        "match_id": mid,
                        "gameweek": m.get("gameweek"),
                        "match": f"{m.get('home_team')} vs {m.get('away_team')}",
                        "minute": g.get("minute"),
                        "time_str": g.get("time_str"),
                        "assist": g.get("assist"),
                        "team": g.get("team"),
                        "is_penalty": g.get("is_penalty"),
                        "is_own_goal": g.get("is_own_goal"),
                        "is_home": g.get("is_home", True),
                        "score_after": g.get("scoreline")
                    })
            for c in m.get("cards", []):
                pn = c.get("player_name")
                if pn:
                    player_events.setdefault(pn, {"goals": [], "cards": [], "substitutions_on": [], "substitutions_off": []})
                    player_events[pn]["cards"].append({
                        "match_id": mid,
                        "gameweek": m.get("gameweek"),
                        "match": f"{m.get('home_team')} vs {m.get('away_team')}",
                        "minute": c.get("minute"),
                        "time_str": c.get("time_str"),
                        "card": c.get("card"),
                        "team": c.get("team")
                    })
            for s in m.get("substitutions", []):
                p_in = s.get("player_in")
                p_out = s.get("player_out")
                if p_in:
                    player_events.setdefault(p_in, {"goals": [], "cards": [], "substitutions_on": [], "substitutions_off": []})
                    player_events[p_in]["substitutions_on"].append({
                        "match_id": mid,
                        "gameweek": m.get("gameweek"),
                        "match": f"{m.get('home_team')} vs {m.get('away_team')}",
                        "minute": s.get("minute"),
                        "time_str": s.get("time_str"),
                        "replaced": p_out,
                        "team": s.get("team")
                    })
                if p_out:
                    player_events.setdefault(p_out, {"goals": [], "cards": [], "substitutions_on": [], "substitutions_off": []})
                    player_events[p_out]["substitutions_off"].append({
                        "match_id": mid,
                        "gameweek": m.get("gameweek"),
                        "match": f"{m.get('home_team')} vs {m.get('away_team')}",
                        "minute": s.get("minute"),
                        "time_str": s.get("time_str"),
                        "replaced_by": p_in,
                        "team": s.get("team")
                    })
        return player_events

    def find_player_events(self, query: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Resolves fuzzy player name to player events dict."""
        q_norm = normalize_text(query)
        if not q_norm:
            return None, None

        # 1. Exact or normalized match
        for pname, pdata in self.player_events.items():
            if normalize_text(pname) == q_norm:
                return pname, pdata

        # 2. Substring match on surname or full name
        for pname, pdata in self.player_events.items():
            pn_norm = normalize_text(pname)
            if q_norm in pn_norm or any(part == q_norm for part in pn_norm.split()):
                return pname, pdata

        return None, None

    def find_match(self, query: str, gameweek: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Finds match by team names (e.g. 'Arsenal vs Chelsea') or gameweek."""
        q_norm = normalize_text(query)
        detected_teams = []
        for syn, canon in TEAM_SYNONYMS.items():
            if re.search(r'\b' + re.escape(syn) + r'\b', q_norm):
                if canon not in detected_teams:
                    detected_teams.append(canon)

        for mid, m in self.matches.items():
            if gameweek and m.get("gameweek") != gameweek:
                continue

            h = m.get("home_team", "")
            a = m.get("away_team", "")

            if len(detected_teams) >= 2:
                if (detected_teams[0] in h and detected_teams[1] in a) or (detected_teams[1] in h and detected_teams[0] in a):
                    return m
            elif len(detected_teams) == 1:
                if detected_teams[0] in h or detected_teams[0] in a:
                    return m

        return None

    def get_gameweek_matches(self, gameweek: int) -> List[Dict[str, Any]]:
        """Returns all finished matches for a specific gameweek."""
        res = [m for m in self.matches.values() if m.get("gameweek") == gameweek]
        res.sort(key=lambda x: x.get("kickoff_time") or "")
        return res

    def format_match_timeline(self, m: Dict[str, Any]) -> str:
        """Formats complete chronological timeline of goals, cards, and substitutions for a fixture."""
        ht = m.get("home_team", "Home")
        at = m.get("away_team", "Away")
        score = m.get("score", "0 - 0")
        gw = m.get("gameweek", "")
        events = sorted(m.get("events", []), key=lambda x: (x.get("minute", 0), x.get("type") != "Goal"))

        reply = f"### ⚽ Match Timeline: **{ht} {score} {at}** (Gameweek {gw})\n\n"
        reply += "| Minute | Event | Player | Details / Assist | Score After |\n"
        reply += "| :---: | :---: | :--- | :--- | :---: |\n"

        for ev in events:
            etype = ev.get("type")
            t_str = ev.get("time_str", "")
            pname = ev.get("player_name", "Unknown")
            team = ev.get("team", "")

            if etype == "Goal":
                ast = f"Assist: **{ev.get('assist')}**" if ev.get("assist") else "Unassisted"
                tag = " (Penalty 🎯)" if ev.get("is_penalty") else (" (Own Goal 🛑)" if ev.get("is_own_goal") else "")
                reply += f"| **{t_str}** | ⚽ **Goal** | **{pname}** ({team}) | {ast}{tag} | **{ev.get('scoreline', score)}** |\n"
            elif etype == "Card":
                card_type = ev.get("card", "Yellow")
                icon = "🟨 Yellow Card" if card_type == "Yellow" else "🟥 Red Card"
                reply += f"| {t_str} | {icon} | {pname} ({team}) | Disciplinary | — |\n"
            elif etype == "Substitution":
                p_in = ev.get("player_in", "?")
                p_out = ev.get("player_out", "?")
                reply += f"| {t_str} | 🔄 Sub | {team} | **IN**: {p_in} ➔ **OUT**: {p_out} | — |\n"

        return reply

    def format_player_events(self, pname: str, pdata: Dict[str, Any]) -> str:
        """Formats detailed goal timestamps, assists, discipline, and substitutions for a player."""
        goals = sorted(pdata.get("goals", []), key=lambda x: (x.get("gameweek", 0), x.get("minute", 0)))
        cards = sorted(pdata.get("cards", []), key=lambda x: (x.get("gameweek", 0), x.get("minute", 0)))
        subs_on = sorted(pdata.get("substitutions_on", []), key=lambda x: (x.get("gameweek", 0), x.get("minute", 0)))
        subs_off = sorted(pdata.get("substitutions_off", []), key=lambda x: (x.get("gameweek", 0), x.get("minute", 0)))

        reply = f"### 🎯 Goal Log & Minute Timestamps: **{pname}**\n\n"
        reply += f"**Total Premier League Goals (2026/27)**: **`{len(goals)} Goals`**\n\n"

        if goals:
            reply += "| GW | Match | Minute | Goal Details | Assist Provider |\n"
            reply += "| :---: | :--- | :---: | :--- | :--- |\n"
            for g in goals:
                ast = f"**{g.get('assist')}**" if g.get("assist") else "*Unassisted*"
                tag = " (Penalty 🎯)" if g.get("is_penalty") else (" (Own Goal 🛑)" if g.get("is_own_goal") else "")
                reply += f"| **GW{g.get('gameweek')}** | {g.get('match')} | **{g.get('time_str')}** | Goal{tag} | {ast} |\n"
        else:
            reply += "*No Premier League goals recorded yet this season.*\n"

        if cards:
            reply += "\n#### 🟨 Disciplinary Cards Log:\n"
            for c in cards:
                icon = "🟨" if c.get("card") == "Yellow" else "🟥"
                reply += f"- **GW{c.get('gameweek')}** vs {c.get('match')}: {icon} **{c.get('card')} Card** at **`{c.get('time_str')}`**\n"

        if subs_on or subs_off:
            reply += "\n#### 🔄 Substitutions Timeline:\n"
            for s in subs_on:
                reply += f"- **GW{s.get('gameweek')}** vs {s.get('match')}: 🟢 Subbed **IN** at **`{s.get('time_str')}`** (Replaced: {s.get('replaced')})\n"
            for s in subs_off:
                reply += f"- **GW{s.get('gameweek')}** vs {s.get('match')}: 🔴 Subbed **OFF** at **`{s.get('time_str')}`** (Replaced by: {s.get('replaced_by')})\n"

        return reply

    def format_gameweek_roundup(self, gameweek: int) -> str:
        """Formats all match results, goalscorers, timestamps, and key events for a gameweek."""
        matches = self.get_gameweek_matches(gameweek)
        if not matches:
            return f"No finished fixture results recorded yet for **Gameweek {gameweek}**."

        reply = f"### ⚽ Premier League Gameweek {gameweek} — Fixture Results & Goal Timestamps\n\n"

        for m in matches:
            ht = m.get("home_team", "")
            at = m.get("away_team", "")
            sc = m.get("score", "0 - 0")
            goals = m.get("goals", [])
            cards = m.get("cards", [])
            subs = m.get("substitutions", [])

            reply += f"#### **{ht} {sc} {at}**\n"
            if goals:
                goal_strs = []
                for g in goals:
                    ast = f" ({g.get('assist')})" if g.get("assist") else ""
                    tag = " (pen)" if g.get("is_penalty") else (" (OG)" if g.get("is_own_goal") else "")
                    goal_strs.append(f"⚽ {g.get('player_name')} **`{g.get('time_str')}`**{ast}{tag}")
                reply += "- **Goals**: " + ", ".join(goal_strs) + "\n"
            else:
                reply += "- **Goals**: *Goalless draw (0 - 0)*\n"

            if cards:
                card_strs = [f"{'🟨' if c.get('card')=='Yellow' else '🟥'} {c.get('player_name')} `{c.get('time_str')}`" for c in cards]
                reply += "- **Cards**: " + ", ".join(card_strs) + "\n"

            if subs:
                reply += f"- **Substitutions**: `{len(subs)} tactical changes`\n"

            reply += "\n"

        return reply

match_events_engine = MatchEventsEngine()
