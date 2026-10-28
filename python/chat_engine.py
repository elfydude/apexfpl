"""
ChatFPL Analytical & Real-Time Strategy Reasoning Engine with Gemini API Integration.
Handles real-time transfer strategy suggestions, player replacement advice,
captaincy recommendations, xP formula transparency, and natural conversation.
"""

import os
import re
import json
import concurrent.futures
from typing import Dict, Any, List, Optional, Tuple

# Try importing Gemini libraries
GEMINI_CLIENT = None
try:
    from google import genai
    from google.genai import types
    HAS_GOOGLE_GENAI = True
except ImportError:
    HAS_GOOGLE_GENAI = False

import warnings
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import google.generativeai as legacy_genai
    HAS_LEGACY_GENAI = True
except ImportError:
    HAS_LEGACY_GENAI = False


OUT_OF_SCOPE_RESPONSE = (
    "I am ChatFPL, an AI assistant built exclusively for Fantasy Premier League and Premier League football analytics. "
    "I cannot answer general knowledge or pop-culture questions. Please ask me about FPL transfer plans, captain picks, "
    "player xP projections, injury updates, or fixture difficulty to help optimize your squad!"
)


class ChatFPLEngine:
    def __init__(self, recommender_engine):
        self.re = recommender_engine
        self._build_player_lookup()
        self.gemini_api_key = self._find_gemini_api_key()

    def _find_gemini_api_key(self) -> Optional[str]:
        """Finds Gemini API key from environment, .env file, or config."""
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if key:
            return key.strip()

        candidates = [
            os.path.join(os.getcwd(), ".env"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), ".env")),
        ]
        for env_file in candidates:
            if os.path.exists(env_file):
                try:
                    with open(env_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if val:
                                    return val
                except Exception:
                    pass
        return None

    def set_gemini_api_key(self, api_key: str) -> bool:
        """Dynamically set and persist Gemini API key."""
        clean_key = api_key.strip()
        if not clean_key:
            return False
        self.gemini_api_key = clean_key
        os.environ["GEMINI_API_KEY"] = clean_key
        target_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
        try:
            with open(target_env, "w", encoding="utf-8") as f:
                f.write(f"GEMINI_API_KEY={clean_key}\n")
        except Exception:
            pass
        return True

    def _build_player_lookup(self):
        """Constructs an exhaustive dictionary of lowercase names, web names, and aliases to player records."""
        self.player_lookup: Dict[str, Dict[str, Any]] = {}
        self.players_by_name: Dict[str, List[Dict[str, Any]]] = {}
        self.name_list: List[str] = []

        aliases = {
            "trent": "Alexander-Arnold",
            "trent alexander arnold": "Alexander-Arnold",
            "taa": "Alexander-Arnold",
            "kdb": "De Bruyne",
            "kevin de bruyne": "De Bruyne",
            "bruno": "B.Fernandes",
            "bruno fernandes": "B.Fernandes",
            "vdv": "Van de Ven",
            "micky van de ven": "Van de Ven",
            "erling haaland": "Haaland",
            "haaland": "Haaland",
            "palmer": "Palmer",
            "cole palmer": "Palmer",
            "alex palmer": "Palmer",
            "saka": "Saka",
            "bukayo saka": "Saka",
            "virgil": "Virgil",
            "van dijk": "Virgil",
            "virgil van dijk": "Virgil",
            "gvardiol": "Gvardiol",
            "josko gvardiol": "Gvardiol",
            "de cuyper": "De Cuyper",
            "maxim de cuyper": "De Cuyper",
            "rice": "Rice",
            "declan rice": "Rice",
            "martinez": "Martinez",
            "lisandro martinez": "Martinez",
            "emi martinez": "Martinez",
            "emiliano martinez": "Martinez",
            "tielemans": "Tielemans",
            "youri tielemans": "Tielemans",
            "iwobi": "Iwobi",
            "alex iwobi": "Iwobi",
            "son": "Son",
            "heung-min son": "Son",
            "salah": "M.Salah",
            "mo salah": "M.Salah",
            "mohamed salah": "M.Salah",
            "watkins": "Watkins",
            "ollie watkins": "Watkins",
            "isak": "Isak",
            "alexander isak": "Isak",
            "pedro": "Pedro",
            "joao pedro": "Pedro",
            "eze": "Eze",
            "eberechi eze": "Eze",
            "munoz": "Muñoz",
            "daniel munoz": "Muñoz",
            "foden": "Foden",
            "phil foden": "Foden",
            "raya": "Raya",
            "david raya": "Raya",
            "alisson": "Alisson",
            "ederson": "Ederson",
            "pickford": "Pickford",
            "dunk": "Dunk",
            "lewis dunk": "Dunk",
            "bassey": "Bassey",
            "calvin bassey": "Bassey",
            "guehi": "Guéhi",
            "marc guehi": "Guéhi"
        }

        for p in self.re.unified_players:
            wn = p.get("web_name", "").lower()
            fn = p.get("full_name", "").lower()
            if wn:
                self.players_by_name.setdefault(wn, []).append(p)
                self.name_list.append(wn)
            if fn and fn != wn:
                self.players_by_name.setdefault(fn, []).append(p)
                self.name_list.append(fn)

        for alias, target_web_name in aliases.items():
            alias_low = alias.lower()
            matched = [p for p in self.re.unified_players if p.get("web_name", "").lower() == target_web_name.lower()]
            if matched:
                for mp in matched:
                    if mp not in self.players_by_name.setdefault(alias_low, []):
                        self.players_by_name[alias_low].append(mp)
                self.name_list.append(alias_low)

        # Build player_lookup mapping key -> top player by selection / minutes as standard fallback
        for name, plist in self.players_by_name.items():
            self.player_lookup[name] = max(plist, key=lambda x: (x.get("selected_by_percent") or 0.0, x.get("current_minutes") or 0))

        self.name_list = list(set(self.name_list))
        self.name_list.sort(key=len, reverse=True)

        self.team_aliases = {
            "arsenal": "ARS", "gunners": "ARS", "ars": "ARS",
            "aston villa": "AVL", "villa": "AVL", "avl": "AVL",
            "bournemouth": "BOU", "cherries": "BOU", "bou": "BOU",
            "brentford": "BRE", "bees": "BRE", "bre": "BRE",
            "brighton": "BHA", "seagulls": "BHA", "bha": "BHA",
            "chelsea": "CHE", "blues": "CHE", "che": "CHE",
            "crystal palace": "CRY", "palace": "CRY", "eagles": "CRY", "cry": "CRY",
            "everton": "EVE", "toffees": "EVE", "eve": "EVE",
            "fulham": "FUL", "cottagers": "FUL", "ful": "FUL",
            "ipswich": "IPS", "ipswich town": "IPS", "ips": "IPS",
            "leicester": "LEI", "foxes": "LEI", "lei": "LEI",
            "liverpool": "LIV", "reds": "LIV", "liv": "LIV",
            "man city": "MCI", "manchester city": "MCI", "city": "MCI", "mci": "MCI",
            "man utd": "MUN", "manchester united": "MUN", "united": "MUN", "red devils": "MUN", "mun": "MUN", "manu": "MUN",
            "newcastle": "NEW", "magpies": "NEW", "toon": "NEW", "new": "NEW",
            "nottingham forest": "NFO", "forest": "NFO", "nott'm forest": "NFO", "nfo": "NFO",
            "tottenham": "TOT", "spurs": "TOT", "tot": "TOT",
            "west ham": "WHU", "hammers": "WHU", "whu": "WHU",
            "wolves": "WOL", "wolverhampton": "WOL", "wol": "WOL",
            "coventry": "COV", "coventry city": "COV", "cov": "COV",
            "hull": "HUL", "hull city": "HUL", "hul": "HUL",
            "leeds": "LEE", "leeds united": "LEE", "lee": "LEE",
            "sunderland": "SUN", "sun": "SUN"
        }

    def find_team(self, text: str) -> Optional[str]:
        """Identifies a Premier League team 3-letter abbreviation from query text."""
        low = text.lower()
        for alias in sorted(self.team_aliases.keys(), key=len, reverse=True):
            pattern = r'\b' + re.escape(alias) + r'\b'
            if re.search(pattern, low):
                return self.team_aliases[alias]
        return None

    def find_player_from_history(self, history: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        """Recovers the most recently mentioned player from recent conversational turns."""
        if not history:
            return None
        for item in reversed(history):
            txt = item.get("text", "")
            p = self.find_player(txt)
            if p:
                return p
        return None

    def find_player(self, text: str) -> Optional[Dict[str, Any]]:
        """Extracts the most relevant player mentioned in a text query, disambiguating by team, position, or first name."""
        low = text.lower()

        # Position clues in query
        is_gk = any(w in low for w in ["gk", "gkp", "goalkeeper", "goalkeepers", "keeper", "keepers", "goalie"])
        is_def = any(w in low for w in ["def", "defs", "defender", "defenders", "centre-back", "cb", "lb", "rb", "fullback"])
        is_mid = any(w in low for w in ["mid", "mids", "midfielder", "midfielders", "winger", "wingers", "cam", "cdm"])
        is_fwd = any(w in low for w in ["fwd", "fwds", "forward", "forwards", "striker", "strikers", "attacker", "cf", "st"])
        target_pos = "GKP" if is_gk else ("DEF" if is_def else ("MID" if is_mid else ("FWD" if is_fwd else None)))

        target_team = self.find_team(text)

        for name in self.name_list:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, low):
                candidates = self.players_by_name.get(name, [])
                if not candidates:
                    candidates = [self.player_lookup[name]] if name in self.player_lookup else []
                if not candidates:
                    continue
                if len(candidates) == 1:
                    return candidates[0]

                # Disambiguate multiple players sharing name
                if target_team and target_pos:
                    match = next((p for p in candidates if p.get("team_short") == target_team and p.get("position") == target_pos), None)
                    if match:
                        return match
                if target_team:
                    match = next((p for p in candidates if p.get("team_short") == target_team), None)
                    if match:
                        return match
                if target_pos:
                    match = next((p for p in candidates if p.get("position") == target_pos), None)
                    if match:
                        return match
                # First name check
                for p in candidates:
                    first_part = (p.get("full_name") or "").lower().split()[0]
                    if first_part and len(first_part) > 2 and re.search(r'\b' + re.escape(first_part) + r'\b', low):
                        return p
                # Default to highest selection / minutes
                return max(candidates, key=lambda x: (x.get("selected_by_percent") or 0.0, x.get("current_minutes") or 0))

        return None

    def find_multiple_players(self, text: str) -> List[Dict[str, Any]]:
        """Extracts up to 2 distinct players mentioned for comparison queries, disambiguating by context."""
        low = text.lower()
        found = []
        found_ids = set()

        is_gk = any(w in low for w in ["gk", "gkp", "goalkeeper", "keeper", "goalie"])
        is_def = any(w in low for w in ["def", "defender", "centre-back", "cb", "lb", "rb"])
        is_mid = any(w in low for w in ["mid", "midfielder", "winger"])
        is_fwd = any(w in low for w in ["fwd", "forward", "striker"])
        target_pos = "GKP" if is_gk else ("DEF" if is_def else ("MID" if is_mid else ("FWD" if is_fwd else None)))
        target_team = self.find_team(text)

        for name in self.name_list:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, low):
                candidates = self.players_by_name.get(name, [])
                if not candidates:
                    candidates = [self.player_lookup[name]] if name in self.player_lookup else []
                if not candidates:
                    continue

                chosen = None
                if len(candidates) == 1:
                    chosen = candidates[0]
                else:
                    if target_team and target_pos:
                        chosen = next((p for p in candidates if p.get("team_short") == target_team and p.get("position") == target_pos), None)
                    if not chosen and target_team:
                        chosen = next((p for p in candidates if p.get("team_short") == target_team), None)
                    if not chosen and target_pos:
                        chosen = next((p for p in candidates if p.get("position") == target_pos), None)
                    if not chosen:
                        for p in candidates:
                            first_part = (p.get("full_name") or "").lower().split()[0]
                            if first_part and len(first_part) > 2 and re.search(r'\b' + re.escape(first_part) + r'\b', low):
                                chosen = p
                                break
                    if not chosen:
                        chosen = max(candidates, key=lambda x: (x.get("selected_by_percent") or 0.0, x.get("current_minutes") or 0))

                if chosen and chosen["id"] not in found_ids:
                    found.append(chosen)
                    found_ids.add(chosen["id"])
                    if len(found) >= 2:
                        break
        return found

    def _get_team_roster_context(self, user_msg: str) -> str:
        """Retrieves verified live roster information for a team/position mentioned in the query."""
        t_alias = self.find_team(user_msg)
        if not t_alias:
            return ""

        low = user_msg.lower()
        is_gk = any(w in low for w in ["gk", "gkp", "goalkeeper", "goalkeepers", "keeper", "keepers", "goalie", "goalies", "between the sticks", "number one", "no. 1"])
        is_def = any(w in low for w in ["def", "defs", "defender", "defenders", "centre-back", "centre-backs", "cb", "cbs", "fullback", "fullbacks", "lb", "rb"])
        is_mid = any(w in low for w in ["mid", "mids", "midfielder", "midfielders", "winger", "wingers"])
        is_fwd = any(w in low for w in ["fwd", "fwds", "forward", "forwards", "striker", "strikers", "attacker", "attackers"])
        is_roster_query = any(w in low for w in ["squad", "roster", "lineup", "setup", "players", "who plays", "team", "options", "depth", "who are", "who is", "is "]) or is_gk or is_def or is_mid or is_fwd

        if not is_roster_query:
            return ""

        pos_filter = "GKP" if is_gk else ("DEF" if is_def else ("MID" if is_mid else ("FWD" if is_fwd else None)))
        team_players = [p for p in self.re.unified_players if p.get("team_short") == t_alias]
        if not team_players:
            return ""

        if pos_filter:
            relevant_players = [p for p in team_players if p.get("position") == pos_filter]
        else:
            relevant_players = team_players

        if not relevant_players:
            return ""

        t_name = team_players[0].get("team_name", t_alias)
        lines = []
        for p in relevant_players[:12]:
            lines.append(
                f"- {p.get('full_name')} (Web name: '{p.get('web_name')}') | {p.get('position')} | £{p.get('cost', 0):.1f}m | "
                f"Starts: {p.get('current_starts', 0)} | Mins: {p.get('current_minutes', 0)} | xP: {p.get('expected_points', 0):.2f} | "
                f"Status: {'Available' if p.get('status') == 'a' else p.get('status')}"
            )

        pos_label = f" {pos_filter}" if pos_filter else ""
        return (
            f"VERIFIED OFFICIAL 2026/27 PREMIER LEAGUE ROSTER FOR {t_name.upper()} ({t_alias}){pos_label}:\n"
            + "\n".join(lines)
            + f"\nCRITICAL DATABASE INSTRUCTION FOR {t_name.upper()}:\n"
            f"1. Rely strictly on the verified roster above for who currently plays for {t_name}.\n"
            f"2. Every player in the list above is officially registered in the active Premier League database for {t_name}.\n"
            f"3. NEVER claim a registered player above does not play for the club (e.g. Alex Palmer IS registered for Ipswich Town).\n"
            f"4. NEVER invent or claim departed players (such as players who left the Premier League or are not listed above) currently play for {t_name}."
        )

    @property
    def current_gameweek(self) -> int:
        """Dynamically retrieves current target gameweek from FPL engine."""
        try:
            return self.re.fpl.get_current_gameweek() or 5
        except Exception:
            return 5

    def _format_deadline_str(self, raw_iso: str) -> str:
        """Formats ISO deadline string into clean human-readable date and time."""
        if not raw_iso:
            return "Upcoming Deadline"
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(raw_iso.replace("Z", "+00:00"))
            return dt.strftime("%A, %d %b %Y, %H:%M UTC")
        except Exception:
            return raw_iso

    def get_gameweek_metadata(self) -> Dict[str, Any]:
        """Provides full gameweek status with target, completed, and formatted deadline."""
        try:
            info = self.re.fpl.get_gameweek_info()
            target_gw = info.get("target_gameweek") or self.current_gameweek
            completed_gw = info.get("latest_completed_gameweek") or max(1, target_gw - 1)
            raw_deadline = info.get("deadline_time", "")
            return {
                "target_gameweek": target_gw,
                "latest_completed_gameweek": completed_gw,
                "gameweek_name": info.get("gameweek_name", f"Gameweek {target_gw}"),
                "deadline_time": raw_deadline,
                "deadline_display": self._format_deadline_str(raw_deadline)
            }
        except Exception:
            return {
                "target_gameweek": 5,
                "latest_completed_gameweek": 4,
                "gameweek_name": "Gameweek 5",
                "deadline_time": "",
                "deadline_display": "Upcoming"
            }

    def _is_out_of_scope(self, text: str) -> bool:
        """Determines if a user message is strictly outside the scope of football and FPL."""
        low = text.lower().strip()
        if not low:
            return False

        # If a known player or team is mentioned, it is within scope
        if self.find_player(text) or self.find_team(text):
            return False

        # Broad football and FPL vocabulary across all eras, leagues, awards, and terms
        football_terms = [
            "fpl", "fantasy", "premier league", "gameweek", "gw", "captain", "vice captain", "vc",
            "transfer", "transfers", "bench", "squad", "lineup", "chip", "chips", "wildcard", "free hit", "bench boost",
            "triple captain", "tc", "fixture", "fixtures", "fdr", "points", "xp", "clean sheet", "clean sheets", "cs",
            "xg", "xa", "xgot", "fotmob", "penalty", "penalties", "free kick", "free-kick", "corner", "corners", "goal",
            "goals", "assist", "assists", "yellow card", "red card", "injury", "injuries", "doubtful", "price change", "bps",
            "differential", "differentials", "budget", "striker", "strikers", "midfielder", "midfielders", "defender",
            "defenders", "goalkeeper", "goalkeepers", "keeper", "gkp", "fwd", "def", "mid", "football", "soccer", "table",
            "relegation", "top four", "champions league", "golden boot", "sub", "substitute", "sell", "buy",
            "top scorer", "top scorers", "scorer", "scorers", "highest scorer", "most goals", "most assists",
            "winner", "winners", "world cup", "euro", "euros", "ballon d'or", "ballon dor", "uefa", "fifa",
            "la liga", "serie a", "bundesliga", "ligue 1", "all-time", "history", "historical", "record", "records",
            "trophy", "trophies", "champion", "champions", "manager", "managers", "coach", "coaches", "derby",
            "offside", "var", "referee", "hat trick", "hat-trick", "golden glove", "speed", "fastest", "distance",
            "sprints", "tackles", "recoveries", "passes", "chances", "ict", "threat", "creativity", "influence",
            "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "2027"
        ]
        if any(term in low for term in football_terms):
            return False

        # Out-of-scope topic markers (pop culture, celebrities, general trivia, recipes, weather, politics, etc.)
        non_football_cues = [
            "hair", "rihanna", "taylor swift", "beyonce", "drake", "kardashian", "movie", "movies", "actor", "actors",
            "actress", "singer", "singers", "song", "songs", "album", "recipe", "recipes", "cook", "bake",
            "weather", "temperature", "forecast", "president", "politics", "election", "crypto", "bitcoin",
            "stock", "stocks", "horoscope", "zodiac", "capital of", "who wrote", "height of", "net worth",
            "dating", "married to", "boyfriend", "girlfriend", "fashion", "clothes", "makeup", "car", "cars",
            "flight", "flights", "hotel", "hotels", "travel to", "homework", "math", "python", "javascript",
            "code", "programming", "relationship"
        ]
        if any(cue in low for cue in non_football_cues):
            return True

        return False

    def process_query(self, user_msg: str, user_squad: Optional[Dict[str, Any]] = None, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Processes user query either through Gemini API (if key present) or through native real-time FPL strategy engine."""
        text = user_msg.strip()
        low = text.lower()

        # Check for out-of-scope / non-football queries
        if self._is_out_of_scope(text):
            return {
                "success": True,
                "reply": OUT_OF_SCOPE_RESPONSE,
                "suggestions": [
                    "Suggest a transfer strategy for my squad",
                    "Who should I captain this week?",
                    "Best differential midfielder under 7.5m"
                ]
            }

        # Check if user is configuring Gemini API key directly in chat
        if any(kw in low for kw in ["set gemini key", "set api key", "gemini key:", "api key:"]):
            parts = re.split(r'[:\s]+', text)
            for part in parts:
                if len(part) >= 20 and ("AIza" in part or len(part) > 30):
                    self.set_gemini_api_key(part)
                    return {
                        "success": True,
                        "reply": "✅ **Gemini API Key connected successfully!** ChatFPL is now powered directly by Google Gemini with live platform squad context.",
                        "suggestions": [
                            "Suggest a transfer strategy for my squad",
                            "Who should I captain this week?",
                            "Should I sell Van de Ven?",
                            "Best differential forward under 7.5m"
                        ]
                    }

        # If Gemini API key is configured, use Gemini with real-time squad context and history
        if self.gemini_api_key:
            gemini_res = self._call_gemini(text, user_squad, history=history)
            if gemini_res:
                return gemini_res

        # Native Real-Time Strategy & Analytics Engine
        return self._process_native_query(text, user_squad, history=history)

    def _call_gemini(self, user_msg: str, user_squad: Optional[Dict[str, Any]] = None, history: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """Calls Gemini API with full local FPL dataset, live FotMob data, multi-turn chat history, and active squad context."""
        try:
            gw_meta = self.get_gameweek_metadata()
            target_gw = gw_meta["target_gameweek"]
            completed_gw = gw_meta["latest_completed_gameweek"]
            deadline_str = gw_meta["deadline_display"]

            # Build current squad context summary
            squad_info = self._format_squad_context(user_squad, target_gw=target_gw)

            # Direct FotMob database context injection
            players_mentioned = self.find_multiple_players(user_msg)
            if not players_mentioned:
                p_single = self.find_player(user_msg)
                if p_single:
                    players_mentioned = [p_single]
                elif history:
                    # Try resolving from previous turns
                    p_hist = self.find_player_from_history(history)
                    if p_hist:
                        players_mentioned = [p_hist]

            # If still no specific player but a team was mentioned in set-piece/penalty/stats context
            if not players_mentioned:
                t_alias = self.find_team(user_msg)
                if t_alias:
                    t_pens = [p for p in self.re.unified_players if p.get("team_short") == t_alias and (p.get("is_penalty_taker") or p.get("is_set_piece_taker"))]
                    if t_pens:
                        players_mentioned = t_pens[:2]

            fotmob_context = ""
            stats_cues = [
                "fotmob", "fot mob", "stat", "stats", "rating", "ratings", "speed", "fastest", "fast", "sprint", "sprints",
                "distance", "chances", "key pass", "xgot", "xg", "xa", "tackle", "tackles", "recovery", "recoveries",
                "pass", "passes", "ict", "threat", "creativity", "influence", "bps", "clean sheet", "clean sheets"
            ]
            if players_mentioned:
                fotmob_context = "\n\n".join([self._format_player_fotmob_context(p, target_gw=target_gw) for p in players_mentioned])
            elif any(k in user_msg.lower() for k in stats_cues):
                fotmob_context = self._get_fotmob_category_summary(user_msg)
            
            # Candidate Pool Retrieval (RAG Grounding for transfer, scouting, replacement, and recommendation queries)
            is_rec_query = any(k in user_msg.lower() for k in [
                "recommend", "suggest", "buy", "transfer", "scout", "best", "top", "pick", "picks", "option", "options",
                "who to get", "who should i get", "who to buy", "replacement", "replace", "upgrade", "differential",
                "budget", "under", "sub-", "cheap", "midfielder", "forward", "defender", "striker", "good", "sell"
            ])
            candidate_pool = self._retrieve_candidate_targets(user_msg, user_squad) if is_rec_query else []
            candidate_context = self._format_candidate_pool_context(candidate_pool, target_gw=target_gw)

            system_instruction = (
                "You are ChatFPL, an expert Fantasy Premier League (FPL) and world football AI analytics, tactical and conversational assistant. "
                "You have access to live Premier League player data, direct official FotMob match statistics, and the user's current squad.\n\n"
                f"📅 CURRENT SEASON & GAMEWEEK STATUS (LIVE DATABASE):\n"
                f"- Current Premier League Season: 2026/27.\n"
                f"- Active Target Gameweek: Gameweek {target_gw} (Upcoming Deadline: {deadline_str}).\n"
                f"- Latest Completed Gameweek: Gameweek {completed_gw}.\n"
                f"- Official Recent Timeline & Verified Champions (Ground Truth):\n"
                f"  * 2026/27: CURRENT ongoing season (actively heading into Gameweek {target_gw})\n"
                f"  * 2025/26: LAST SEASON (concluded in May 2026). PREMIER LEAGUE CHAMPIONS: **Arsenal** (Mikel Arteta's Arsenal won the title in May 2026, ending their 22-year title drought and dethroning Man City).\n"
                f"  * 2024/25: 2 seasons ago. Champions: Liverpool.\n"
                f"  * 2023/24: 3 seasons ago. Champions: Manchester City.\n"
                f"  * 2022/23: 4 seasons ago. Champions: Manchester City.\n"
                f"  * 2021/22: 5 seasons ago. Champions: Manchester City.\n"
                f"  * 2020/21: 6 seasons ago. Champions: Manchester City.\n"
                f"  * 2019/20: 7 seasons ago. Champions: Liverpool.\n"
                f"- CRITICAL FACT: When asked who won the Premier League last season (2025/26), the answer is unequivocally ARSENAL. Never state Manchester City won in 2025/26.\n"
                f"- Temporal Anchoring: You are operating in real-time during the 2026/27 season (October 2026). "
                f"The 2025/26 season is finished. Do not assume the year is 2024 or 2025. Always rely on the active 2026/27 database provided below.\n"
                f"- Gameweek Tone: You must accurately state that we are currently in/heading into Gameweek {target_gw}. "
                f"NEVER lecture, scold, or display compliance warnings to the user. Never refuse queries regarding Gameweek {target_gw} or future gameweeks.\n\n"
                "⚽ SCOPE & CAPABILITY GUIDELINES:\n"
                "1. FPL Active Transfer & Squad Advice (2026/27 Season):\n"
                "   - When recommending transfers, squad upgrades, captain picks, or replacements for the user's active team, you MUST ONLY recommend active players present in the VERIFIED ACTIVE CANDIDATE POOL provided below or the user's active squad.\n"
                "   - NEVER recommend players from older clubs (e.g. NEVER recommend Anthony Gordon at Newcastle) or players who departed the Premier League.\n"
                "   - Every player recommendation MUST use the exact current team, official price (£m), projected xP, and opponent from the verified database list.\n\n"
                "2. General Football Knowledge, History & Trivia (FULL ACCESS):\n"
                "   - You are a comprehensive football authority. When the user asks about historical football questions, past seasons, international tournaments, Champions League, Ballon d'Or, FotMob/FPL records, rules, or trivia (e.g. 'who was the top scorer in 2019', 'who won the Champions League in 2021', 'how many goals did Aguero score', 'all-time Premier League goal records'):\n"
                "   - YOU ARE FULLY EXPECTED TO ANSWER DIRECTLY using your deep football knowledge! Do NOT refuse, do NOT issue compliance warnings, and do NOT give out-of-scope replies for any football questions.\n\n"
                "3. Out-of-Scope (Non-Football ONLY):\n"
                f"   - If the user asks about topics completely unrelated to football or FPL (e.g. celebrities like Rihanna, movies, cooking, weather, politics, crypto, coding), reply with this exact response and nothing else:\n"
                f"   \"{OUT_OF_SCOPE_RESPONSE}\"\n\n"
                "Important modeling & tactical rules:\n"
                "- Expected Points (xP) is based on: Appearance, FotMob Form, Attacking Potential (holistic xG & xA), "
                "Defensive Potential, Disciplinary Deductions, and Multi-Season Track Record (60-65% weighting).\n"
                "- Midfielder attacking xP includes Big Chances created per 90 (Big Chances/90 × 0.35) instead of raw season totals.\n"
                "- Defensive contributions per 90 (tackles, ball recoveries, interceptions, blocks, clearances) award points: 10 actions = 2.0 pts for DEF (actions/90 × 0.20), and 12 actions = 2.0 pts for MID (actions/90 × 0.167).\n"
                "- Disciplinary risk reduces points: yellow cards (-1.0/90), red cards (-3.0/90), and fouls committed (-0.10/90).\n"
                "- There is NO line-breaking pass boost added to xP (it was removed).\n"
                "- Transfers in the Starting XI are heavily prioritized over bench depth.\n"
                "- TRANSFER DISCIPLINE (CRITICAL):\n"
                "  * Strongly DISCOURAGE making too many transfers in a single gameweek.\n"
                "  * In the 2026/27 season, FPL rules permit banking up to 5 Free Transfers. Rolling transfers accumulates critical transfer equity for upcoming fixture swings and double gameweeks.\n"
                "  * Discourage making sideways transfers for marginal (<0.5 xP) expected gains.\n"
                "  * Strictly discourage taking -4 point hits. Only recommend a hit if the user has multiple confirmed injured or suspended starters.\n"
                "  * Ground all advice directly in the user's starting XI, bench, budget in the bank, and available Free Transfers.\n"
                "- REAL-TIME WEB SEARCH GROUNDING:\n"
                "  * Google Search is enabled for your responses. Actively search the web for the latest breaking Premier League injury updates, press conference manager quotes, lineup confirmations, and live news when relevant.\n"
                "- TRANSFERRED OUT PLAYERS: Players marked as is_transferred_out (or who moved abroad like Watkins to Al-Hilal, Martinelli to Al-Hilal, Jesus to Barcelona, Digne to PSG, Bailey, etc.) are INACTIVE in FPL. You must NEVER suggest buying them. If asked, confirm they have transferred out of the Premier League.\n"
                "- INJURED & DOUBTFUL PLAYERS: NEVER recommend bringing in players marked as is_unavailable (0% chance) or doubtful (is_doubtful / chance_of_playing < 75%). Only recommend active, fully fit players.\n"
                "- Conversational Guidelines: Be sharp, direct, intelligent, and insightful. Avoid reciting raw mathematical formulas unless asked.\n\n"
                "🎯 INTELLIGENT, RELEVANT & ROBUST RESPONSE GUIDELINES:\n"
                "1. DIRECT OPENING: Start immediately with your answer or verdict in natural, conversational prose. NEVER output meta-labels, headers, or prefixes such as 'Bottom Line Up Front:', 'BLUF:', or 'Bottom Line:'—simply deliver your answer directly.\n"
                "2. SUBSTANTIVE & RELEVANT DEPTH: Do NOT artificially truncate, cut off, or over-summarize responses. Prioritize high-signal, relevant output. Explain the 'why' behind decisions: break down form, upcoming fixture difficulty, minutes security, tactical positioning, underlying metrics (xG, xA, FotMob ratings, chances created), and squad budget implications.\n"
                "3. ACCURATE STATS: Quote exact metrics from the verified FPL and FotMob database provided in context. When asked for a specific stat, highlight it prominently, but provide useful context (e.g. appearances, minutes, rank) if relevant to give a complete, helpful answer.\n"
                "4. MOBILE-FRIENDLY FORMATTING: Use structured markdown (bold key names, bullet points for comparative breakdowns, short tables if <=3 columns). Avoid wide horizontal tables with more than 3 columns.\n"
                "5. NATURAL CONVERSATIONAL TONE: Speak like an elite FPL analyst. Be engaging, nuanced, and strategic. Avoid repetitive robotic boilerplate.\n"
                "6. MULTI-TURN MEMORY: Seamlessly carry context across turns ('him', 'replace him', 'who else', 'what about Saka?').\n\n"
                f"CURRENT USER SQUAD & RECS (GAMEWEEK {target_gw}):\n{squad_info}\n"
            )

            if fotmob_context:
                system_instruction += (
                    f"\nDIRECT OFFICIAL FOTMOB DATABASE QUERY RESULTS:\n{fotmob_context}\n\n"
                    "INSTRUCTIONS FOR FOTMOB STATS QUERIES:\n"
                    "The user is asking about player stats. Provide a rich, insightful performance breakdown covering: "
                    "(1) Match Ratings & Form, (2) Attacking Threat (xG, goals, shot volume), (3) Creativity (xA, chances created), and (4) Work Rate & Physicals (speed, distance, sprints, defensive actions). "
                    "Make the data meaningful and actionable."
                )

            if candidate_context:
                system_instruction += (
                    f"\n\n{candidate_context}\n\n"
                    "INSTRUCTIONS FOR CANDIDATE RECOMMENDATIONS:\n"
                    "Base your recommendations entirely on the verified candidate pool above. "
                    "State the single best option first with clear rationale, followed by strong alternatives. "
                    "Detail price, expected points, fixture difficulty, and tactical fit. "
                    "Never recommend any player not listed in the verified candidate pool."
                )

            team_roster_context = self._get_team_roster_context(user_msg)
            if team_roster_context:
                system_instruction += f"\n\n{team_roster_context}\n"

            # Try google-genai
            if HAS_GOOGLE_GENAI:
                client = genai.Client(api_key=self.gemini_api_key)
                models_to_try = [
                    "gemini-3.5-flash",
                    "gemini-flash-lite-latest",
                    "gemini-3.8-flash",
                    "gemini-flash-latest",
                    "gemini-3.7-flash"
                ]

                # Google Search Tool initialization
                search_tool = None
                try:
                    search_tool = types.Tool(google_search=types.GoogleSearch())
                except Exception as e:
                    print(f"[ChatFPL] GoogleSearch tool init: {e}")

                # Format conversation history ensuring strict role alternation (user, model, user, ...)
                contents = []
                last_role = None
                if history:
                    for h in history[-8:]:
                        r = "user" if h.get("role") in ["user", "human"] else "model"
                        t = (h.get("text") or "").strip()
                        if not t:
                            continue
                        if len(t) > 800:
                            t = t[:800] + "..."
                        if r == last_role and contents:
                            prev_t = contents[-1].parts[0].text
                            contents[-1] = types.Content(role=r, parts=[types.Part.from_text(text=f"{prev_t}\n{t}")])
                        else:
                            contents.append(types.Content(role=r, parts=[types.Part.from_text(text=t)]))
                            last_role = r

                if contents and contents[-1].role == "user":
                    prev_t = contents[-1].parts[0].text
                    contents[-1] = types.Content(role="user", parts=[types.Part.from_text(text=f"{prev_t}\n{user_msg}")])
                else:
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))

                for model_name in models_to_try:
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    try:
                        config_search = types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            tools=[search_tool] if search_tool else None,
                            max_output_tokens=1500,
                            temperature=0.6
                        )
                        config_basic = types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            max_output_tokens=1500,
                            temperature=0.6
                        )

                        def execute_genai_call():
                            if search_tool:
                                try:
                                    return client.models.generate_content(
                                        model=model_name,
                                        contents=contents,
                                        config=config_search
                                    )
                                except Exception as st_err:
                                    print(f"[ChatFPL] Google Search tool on {model_name} failed ({st_err}), falling back to direct generation")
                            return client.models.generate_content(
                                model=model_name,
                                contents=contents,
                                config=config_basic
                            )

                        future = executor.submit(execute_genai_call)
                        response = future.result(timeout=12.0)
                        executor.shutdown(wait=False, cancel_futures=True)

                        if response and response.text:
                            # Safety Guardrail: check for hallucinated known departed/invalid players at old clubs
                            banned_hallucinations = ["gordon (newcastle", "gordon at newcastle", "gordon plays for newcastle"]
                            if any(b in response.text.lower() for b in banned_hallucinations):
                                print("[ChatFPL Gemini Guardrail] Detected hallucinated/departed player club in LLM response. Falling back to native verified engine.")
                                return None

                            # Safety Guardrail: check for hallucinated 2025/26 league champions (Arsenal won 2025/26, NOT Man City)
                            low_resp = response.text.lower()
                            city_terms = ["manchester city", "man city", "city"]
                            champion_terms = ["won the premier league", "won the pl", "premier league title", "won the title", "crowned champions", "claimed the crown", "champions in the 2025/26", "champions in 2025/26"]
                            season_terms = ["2025/26", "last season", "2025-26"]
                            if any(c in low_resp for c in city_terms) and any(s in low_resp for s in season_terms) and any(w in low_resp for w in champion_terms):
                                print("[ChatFPL Gemini Guardrail] Intercepted 2025/26 Man City champion hallucination. Falling back to verified ground truth.")
                                return self._handle_league_champions_query(user_msg)

                            # Clean unwanted meta-labels like "**Bottom Line Up Front:**", "**Bottom Line Up Front**:", "BLUF:"
                            cleaned_reply = re.sub(r'^(?:#+\s*)?(?:\*\*)?Bottom Line(?: Up Front)?(?:\*\*)?:?\s*(?:\*\*)?\s*', '', response.text.strip(), flags=re.IGNORECASE)
                            cleaned_reply = re.sub(r'^(?:#+\s*)?(?:\*\*)?BLUF(?:\*\*)?:?\s*(?:\*\*)?\s*', '', cleaned_reply, flags=re.IGNORECASE).strip()

                            return {
                                "success": True,
                                "reply": cleaned_reply,
                                "suggestions": self._generate_contextual_suggestions(user_msg, cleaned_reply)
                            }
                    except concurrent.futures.TimeoutError:
                        print(f"[Gemini model {model_name} timed out (>15s)]")
                        executor.shutdown(wait=False, cancel_futures=True)
                        continue
                    except Exception as model_err:
                        print(f"[Gemini model {model_name} failed]: {model_err}")
                        executor.shutdown(wait=False, cancel_futures=True)
                        continue
        except Exception as e:
            print(f"[ChatFPL Gemini Error] {e}")
            # Fall back to native strategy engine
            return None

        return None

    def _retrieve_candidate_targets(self, query: str, user_squad: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Retrieves top verified active player candidates from the database matching query criteria."""
        low = query.lower()
        pos_filter = None
        if any(w in low for w in ["mid", "midfielder", "winger"]):
            pos_filter = "MID"
        elif any(w in low for w in ["def", "defender", "centre-back", "fullback", "cb", "lb", "rb"]):
            pos_filter = "DEF"
        elif any(w in low for w in ["fwd", "forward", "striker", "attacker"]):
            pos_filter = "FWD"
        elif any(w in low for w in ["gk", "gkp", "keeper", "goalkeeper"]):
            pos_filter = "GKP"

        budget_match = re.search(r'(?:under|<=|<|less than|budget|price of|cost of|sub-)\s*(?:£)?(\d+(?:\.\d+)?)m?', low)
        max_cost = float(budget_match.group(1)) if budget_match else None

        is_diff = any(w in low for w in ["diff", "differential", "low ownership", "hidden gem", "unowned"])

        user_pids = set()
        if user_squad:
            user_pids = set(user_squad.get("starters", []) + user_squad.get("bench", []))

        # Base filter: strictly active, not transferred out, not injured, not doubtful
        candidates = [
            p for p in self.re.unified_players
            if not p.get("is_unavailable")
            and not p.get("is_transferred_out")
            and not p.get("is_doubtful")
            and p.get("chance_of_playing", 100) >= 75
            and p.get("id") not in user_pids
        ]

        if pos_filter:
            candidates = [p for p in candidates if p.get("position") == pos_filter]

        if max_cost:
            candidates = [p for p in candidates if p.get("cost", 99.0) <= max_cost]

        if is_diff:
            def parse_own(p):
                try:
                    return float(str(p.get("selected_by_percent", 0)).replace("%", ""))
                except Exception:
                    return 100.0
            candidates = [p for p in candidates if parse_own(p) <= 12.0]

        candidates.sort(key=lambda x: -x.get("expected_points", 0))

        # If no position filter or budget was given, provide balanced top 8 across key positions
        if not pos_filter and not max_cost:
            top_mids = [p for p in candidates if p.get("position") == "MID"][:3]
            top_fwds = [p for p in candidates if p.get("position") == "FWD"][:3]
            top_defs = [p for p in candidates if p.get("position") == "DEF"][:2]
            combined = top_mids + top_fwds + top_defs
            combined.sort(key=lambda x: -x.get("expected_points", 0))
            return combined

        return candidates[:8]

    def _format_candidate_pool_context(self, candidates: List[Dict[str, Any]], target_gw: int = 5) -> str:
        """Formats verified active candidates as an explicit prompt context block."""
        if not candidates:
            return ""
        lines = [
            f"VERIFIED ACTIVE CANDIDATE POOL (2026/27 SEASON - GAMEWEEK {target_gw} - LIVE DATABASE):",
            "When recommending or suggesting players, you MUST ONLY recommend from this verified list:"
        ]
        for i, c in enumerate(candidates, 1):
            fix = c.get("upcoming_fixtures", [{}])[0]
            opp = fix.get("opponent", "OPP")
            fdr = fix.get("difficulty", 3)
            venue = "H" if fix.get("is_home") else "A"
            lines.append(
                f"{i}. {c.get('web_name')} ({c.get('full_name')}) — Current Club: {c.get('team_name')} ({c.get('team_short')}) | "
                f"Pos: {c.get('position')} | Official Price: £{c.get('cost'):.1f}m | "
                f"Projected GW{target_gw} xP: {c.get('expected_points'):.2f} | Next Fixture: vs {opp} ({venue}) [FDR {fdr}] | "
                f"FotMob Rating: {c.get('fotmob_rating', 6.5):.2f} | Form: {c.get('goals_scored', 0)}G, {c.get('assists', 0)}A | "
                f"Underlying: {c.get('xg_90', 0):.2f} xG/90, {c.get('xa_90', 0):.2f} xA/90 | Ownership: {c.get('selected_by_percent', 0)}%"
            )
        return "\n".join(lines)

    def _format_squad_context(self, user_squad: Optional[Dict[str, Any]], target_gw: int = 5) -> str:
        """Constructs a comprehensive, rich context string of the user's active squad."""
        if not user_squad or not user_squad.get("starters"):
            user_squad = self.re.get_default_squad()

        starters = [self.re.players_by_id[pid] for pid in user_squad.get("starters", []) if pid in self.re.players_by_id]
        bench = [self.re.players_by_id[pid] for pid in user_squad.get("bench", []) if pid in self.re.players_by_id]
        bank = float(user_squad.get("bank", 0.5))
        ft = int(user_squad.get("free_transfers", 1))

        def format_player_line(p):
            fix = (p.get("upcoming_fixtures") or [{}])[0]
            opp = fix.get("opponent", "OPP")
            fdr = fix.get("difficulty", 3)
            venue = "H" if fix.get("is_home") else "A"
            flag = ""
            if p.get("is_unavailable"):
                flag = f" [INJURED/SUSPENDED: {p.get('injury_reason', 'Out')}]"
            elif p.get("is_doubtful"):
                flag = f" [DOUBTFUL: {p.get('chance_of_playing', 50)}% - {p.get('injury_reason', 'Flagged')}]"
            return (
                f"- {p['web_name']} ({p.get('team_short', 'PL')}, {p['position']}) | Price: £{p['cost']:.1f}m | "
                f"GW{target_gw} xP: {p.get('expected_points', 0):.2f} | Fixture: vs {opp} ({venue}) [FDR {fdr}]{flag}"
            )

        starters_lines = [format_player_line(p) for p in starters]
        bench_lines = [format_player_line(p) for p in bench]

        rec = self.re.recommend_transfers_and_chips(
            starters=[p["id"] for p in starters],
            bench=[p["id"] for p in bench],
            bank=bank,
            free_transfers=ft
        )

        all_players = starters + bench
        flagged = [p for p in all_players if p.get('is_doubtful') or p.get('is_unavailable')]
        flagged_strs = [f"{p.get('web_name')} ({p.get('injury_reason', 'Flagged')})" for p in flagged]
        flagged_summary = (
            f"Injuries/Flags in squad: {', '.join(flagged_strs)}"
            if flagged else "No active injury flags in squad."
        )

        top_captains = sorted(starters, key=lambda p: -p.get("expected_points", 0))[:2]
        cap_summary = ", ".join([f"{p['web_name']} ({p.get('expected_points', 0):.2f} xP)" for p in top_captains])

        return (
            f"USER SQUAD METRICS (GAMEWEEK {target_gw}):\n"
            f"- In Bank: £{bank:.1f}m | Available Free Transfers: {ft} FT\n"
            f"- Top Captaincy Contenders: {cap_summary}\n"
            f"- Recommended Action: {rec.get('action_title', rec.get('action', 'Hold/Roll'))}\n"
            f"- Squad Health: {flagged_summary}\n\n"
            f"STARTING XI (11 players):\n" + "\n".join(starters_lines) + "\n\n"
            f"BENCH (4 players):\n" + "\n".join(bench_lines)
        )

    def _process_native_query(self, text: str, user_squad: Optional[Dict[str, Any]] = None, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Deterministic real-time FPL tactical & analytical answering engine with multi-intent classification."""
        low = text.lower()

        # -1. Dynamic Gameweek Status & Deadline Query
        if any(kw in low for kw in ["gameweek", "gw", "deadline"]) and any(kw in low for kw in ["which", "what", "current", "next", "deadline", "when is", "schedule", "status", "are we in", "is it", "when"]):
            return self._handle_gameweek_query(text)

        # -1b. Premier League Champions / Title Winners Query
        champion_cues = ["who won", "won the", "won pl", "won league", "champion", "champions", "title winner", "lifted the trophy", "won the trophy", "who is the champion", "winner of", "who won", "winners"]
        league_cues = ["premier league", "pl", "league", "title", "last season", "2025/26", "2024/25", "2023/24", "trophy", "crown"]
        if any(c in low for c in champion_cues) and any(l in low for l in league_cues):
            return self._handle_league_champions_query(text)

        # -2. Historical Football & Tournament Queries (e.g. 2019 top scorer, golden boot, champions league)
        if any(kw in low for kw in ["top scorer", "top scorers", "golden boot", "highest scorer", "most goals", "champions league", "ballon d'or", "ballon dor", "world cup"]) and any(y in low for y in ["2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "all time", "all-time", "history", "records"]):
            return self._handle_historical_football_query(text)

        # -3. Stats Normalization & Bayesian Shrinkage Methodology
        if any(kw in low for kw in ["normalization", "normalize", "normalized", "sample size", "bayesian shrinkage", "minutes ramp", "how do you normalize", "how are stats normalized"]):
            return self._handle_stats_normalization_explanation()

        # -4. New Signings / New Premier League Entrants
        if any(kw in low for kw in ["new signing", "new signings", "new players", "new player", "new to premier league", "new to pl", "new entrants", "recently entered", "newcomer", "newcomers"]):
            return self._handle_new_signings_and_entrants(text)

        # -5. Live Injuries & Injury Returnees Overview (when not querying a single specific player)
        if any(kw in low for kw in ["injury update", "injured players", "who is injured", "injury list", "returning from injury", "returnees", "injury doubts"]) and not self.find_player(text):
            return self._handle_injury_and_returnees_query(text)

        # Pronoun & follow-up resolver from conversation history
        target_p = self.find_player(text)
        if not target_p and history and any(kw in low for kw in ["he", "him", "his", "replace him", "sell him", "why him", "bench him", "captain him", "who else"]):
            target_p = self.find_player_from_history(history)

        # 0. Direct FotMob Querying
        stats_keywords = [
            "fotmob", "fot mob", "speed", "fastest", "xgot", "chances created", "distance per 90",
            "tackles per 90", "fotmob rating", "ict", "threat", "creativity", "influence", "bps", "recoveries"
        ]
        if any(kw in low for kw in stats_keywords) or (
            any(kw in low for kw in ["stats for", "stats of", "look up stats", "query stats", "player stats"]) and target_p
        ):
            players = self.find_multiple_players(text)
            if len(players) >= 2 and any(k in low for k in ["compare", "vs", "versus", "or", "and"]):
                return self._handle_fotmob_comparison(players[0], players[1])

            if target_p:
                return self._handle_fotmob_player_query(target_p, text)

            if any(k in low for k in ["speed", "fastest", "rating", "sprints", "distance", "chances", "xgot", "tackles", "ict", "threat", "creativity", "bps", "leaders"]):
                return self._handle_fotmob_leaderboard(text)

            return self._handle_fotmob_overview_help()

        # 1. Bench & Lineup Optimization Dilemmas
        if any(kw in low for kw in ["bench", "who to bench", "who should i bench", "bench order", "who to start", "start or bench", "lineup dilemma", "benching", "sub order"]):
            return self._handle_bench_query(text, user_squad)

        # 2. Set-Piece & Penalty Takers
        if any(kw in low for kw in ["penalty", "penalties", "pen taker", "penalty taker", "free kick", "free-kick", "corners", "set piece", "set-piece"]):
            return self._handle_set_piece_query(text)

        # 3. Transfer Volume, Discipline & Hit Penalty Inquiries ("How many transfers", "Should I take a hit", "Too many transfers")
        if any(kw in low for kw in [
            "how many transfer", "too many transfer", "take a hit", "should i take a hit",
            "worth taking a hit", "multiple transfer", "how many transfers should i make",
            "is a hit worth it", "take minus 4", "take a -4"
        ]):
            return self._handle_transfer_count_and_hit_discipline(text, user_squad)

        # 3b. FPL Chips & Rule Inquiries (Wildcard, Free Hit, Bench Boost, TC, BPS)
        if any(kw in low for kw in ["wildcard", "free hit", "bench boost", "triple captain", "tc", "chip", "chips", "bonus point", "bps", "price change", "price rise", "price fall", "how does free hit", "when to wildcard"]):
            return self._handle_fpl_rules_and_chips(text)

        # 4. Fixtures, Difficulty & Upcoming Schedules
        if any(kw in low for kw in ["fixture", "fixtures", "fdr", "schedule", "calendar", "easiest run", "hardest run", "fixture swing", "double gameweek", "dgw", "blank gameweek", "bgw"]):
            return self._handle_fixture_query(text)

        # 5. Budget, Value & Differential Scouting
        if any(kw in low for kw in ["differential", "differentials", "budget", "cheap", "affordable", "under "]) or (
            any(p in low for p in ["mid", "def", "fwd", "gk", "keeper", "striker", "defender", "midfielder"]) and any(w in low for w in ["best", "top", "pick", "good", "recommend", "under", "cheap", "value", "cost"])
        ):
            return self._handle_budget_scouting(text)

        # 6. Real-time Transfer Strategy Suggestions
        if any(kw in low for kw in ["strategy", "what transfer", "suggest transfer", "who to transfer", "transfers to make", "fix my team", "what should i do", "transfer advice"]):
            return self._handle_transfer_strategy_suggestion(user_squad)

        # 7. Player Replacement Queries ("Replace X with who?", "Who should I buy for X?", "Sell X?")
        if any(kw in low for kw in ["replace", "substitute", "swap", "sell", "buy for", "upgrade"]):
            if target_p:
                return self._handle_player_replacement_query(target_p, user_squad)

        # 8. Captaincy Suggestions
        if any(kw in low for kw in ["captain", "armband", "who to captain", "vice captain", "vc"]):
            return self._handle_captaincy_query(user_squad, target_p=target_p)

        # 9. Player Comparison
        if ("compare" in low or " vs " in low or " versus " in low or " or " in low) and any(kw in low for kw in ["better", "who", "pick", "start", "buy", "transfer", "compare", "vs"]):
            players = self.find_multiple_players(text)
            if len(players) >= 2:
                return self._handle_comparison(players[0], players[1])

        # 10. Specific Player Inquiries (History, Specific Stat Queries, xP Explanation, Overview)
        if target_p:
            if any(term in low for term in [
                "last season", "past season", "previous season", "2025/26", "2024/25", "2023/24",
                "career", "history", "seasons", "campaign", "xg", "xa", "goals", "scored", "assists",
                "minutes", "mins", "clean sheet", "clean sheets", "points", "speed", "rating"
            ]):
                return self._handle_player_specific_stat_or_history(target_p, text)
            elif any(term in low for term in ["why", "how", "breakdown", "formula", "points", "xp", "project", "explain"]):
                return self._handle_player_xp_explanation(target_p)
            else:
                return self._handle_player_overview(target_p)

        # 11. Team Scouting / Club Asset Overview
        team_short = self.find_team(text)
        if team_short:
            return self._handle_team_query(text)

        # 12. Holistic xG / xA explanation
        if any(term in low for term in ["holistic xg", "holistic xa", "holistic", "bayesian", "what is holistic"]):
            return self._handle_holistic_explanation()

        # 13. Multi-Season Track Record explanation (60-65% weight)
        if any(term in low for term in ["multi season", "multi-season", "track record", "career points", "career history", "60-65", "60%", "65%", "weighting"]):
            return self._handle_multi_season_explanation()

        # 14. Rolling transfer logic
        if any(term in low for term in ["roll transfer", "why roll", "roll"]):
            return self._handle_roll_transfer_explanation(user_squad)

        # 14b. Season Timeline & Historical Framework
        if any(term in low for term in ["last season", "previous season", "2025/26", "timeline", "what season"]):
            return self._handle_timeline_overview()

        # 15. Leaderboards & Top Scorers
        if any(term in low for term in ["highest", "best", "top", "leader"]):
            return self._handle_leaderboards(low)

        # 16. Dynamic Context-Aware Discovery (Replaces static boilerplate help)
        return self._handle_smart_discovery(text, user_squad, history=history)

    # -------------------------------------------------------------------------
    # Gameweek & Historical Handlers
    # -------------------------------------------------------------------------

    def _handle_gameweek_query(self, text: str) -> Dict[str, Any]:
        """Provides dynamic real-time Premier League gameweek and deadline status."""
        gw_meta = self.get_gameweek_metadata()
        target_gw = gw_meta["target_gameweek"]
        completed_gw = gw_meta["latest_completed_gameweek"]
        deadline = gw_meta["deadline_display"]

        reply = (
            f"### 📅 Premier League & FPL Gameweek Status (2026/27)\n\n"
            f"- **Active Target Gameweek**: **Gameweek {target_gw}**\n"
            f"- **Next Transfer Deadline**: **{deadline}**\n"
            f"- **Latest Finished Gameweek**: **Gameweek {completed_gw}**\n\n"
            f"💡 *All squad calculations, player xP projections, and transfer recommendations are currently optimized for **Gameweek {target_gw}**.*"
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Who should I captain for Gameweek {target_gw}?",
                f"Suggest a transfer strategy for Gameweek {target_gw}",
                f"Injury doubts ahead of Gameweek {target_gw} deadline"
            ],
            "gameweek": target_gw
        }

    def _handle_league_champions_query(self, text: str) -> Dict[str, Any]:
        """Provides authoritative historical records of Premier League and competition winners."""
        low = text.lower()
        gw_meta = self.get_gameweek_metadata()
        target_gw = gw_meta["target_gameweek"]

        if "2024/25" in low or ("2025" in low and "2025/26" not in low and "last season" not in low):
            reply = (
                "### 🏆 2024/25 Premier League Champions\n\n"
                "* **Champions**: **Liverpool**\n"
                "* **Runners-up**: Arsenal \n\n"
                f"💡 *In the following season (**2025/26**, last season), **Arsenal** won the Premier League title.*"
            )
        elif "2023/24" in low or ("2024" in low and "2024/25" not in low):
            reply = (
                "### 🏆 2023/24 Premier League Champions\n\n"
                "* **Champions**: **Manchester City** (completed a historic 4-in-a-row with 91 points).\n"
                "* **Runners-up**: Arsenal (89 points)."
            )
        elif "2022/23" in low:
            reply = (
                "### 🏆 2022/23 Premier League Champions\n\n"
                "* **Champions**: **Manchester City** (Treble-winning season, 89 points).\n"
                "* **Runners-up**: Arsenal (84 points)."
            )
        elif "2019/20" in low or ("2020" in low and "2020/21" not in low):
            reply = (
                "### 🏆 2019/20 Premier League Champions\n\n"
                "* **Champions**: **Liverpool** (Jürgen Klopp's side amassed 99 points to win their first league title in 30 years).\n"
                "* **Runners-up**: Manchester City (81 points)."
            )
        else:
            # Default / last season (2025/26) or general winner query
            reply = (
                "### 🏆 Premier League Champions (2025/26 Season — Last Season)\n\n"
                "**Arsenal** won the Premier League title last season (**2025/26**)!\n\n"
                "Under Mikel Arteta, Arsenal finished top of the table in May 2026, ending a 22-year title drought "
                "and dethroning Manchester City to capture their first league championship since the iconic 2003/04 'Invincibles' season.\n\n"
                "#### 📜 Recent Premier League Winners Roll of Honour:\n"
                "* **2025/26 (Last Season)**: 🏆 **Arsenal** (Champions)\n"
                "* **2024/25 (2 seasons ago)**: **Liverpool**\n"
                "* **2023/24 (3 seasons ago)**: **Manchester City**\n"
                "* **2022/23 (4 seasons ago)**: **Manchester City**\n"
                "* **2021/22 (5 seasons ago)**: **Manchester City**\n"
                "* **2020/21 (6 seasons ago)**: **Manchester City**\n"
                "* **2019/20 (7 seasons ago)**: **Liverpool**\n\n"
                f"⚽ *The **2026/27** season is currently active, heading into **Gameweek {target_gw}**.*"
            )

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Who should I captain in Gameweek {target_gw}?",
                "Suggest a transfer strategy for my squad",
                "Who were the top scorers in 2025/26?"
            ]
        }

    def _handle_historical_football_query(self, text: str) -> Dict[str, Any]:
        """Handles historical football and tournament queries (e.g. 2019 top scorer, Golden Boot records) in native mode."""
        low = text.lower()
        target_gw = self.current_gameweek
        if "2019" in low or "2018/19" in low:
            reply = (
                "### ⚽ 2018/19 & 2019 Top Scorers\n\n"
                "* **2018/19 Premier League Golden Boot**: A historic 3-way tie with **22 goals** each:\n"
                "  * **Mohamed Salah** (Liverpool)\n"
                "  * **Sadio Mané** (Liverpool)\n"
                "  * **Pierre-Emerick Aubameyang** (Arsenal)\n\n"
                "* **2019 Calendar Year (Jan 1 – Dec 31, 2019)**:\n"
                "  * **Premier League**: **Jamie Vardy** (Leicester City) was top scorer across the calendar year with **29 goals**.\n"
                "  * **European Golden Shoe**: **Lionel Messi** (Barcelona) scored **50+ goals** across all competitions.\n\n"
                f"💡 *For our active 2026/27 campaign, we are currently heading into **Gameweek {target_gw}**.*"
            )
        elif "2020" in low or "2019/20" in low:
            reply = (
                "### ⚽ 2019/20 Premier League Top Scorer\n\n"
                "* **Golden Boot Winner**: **Jamie Vardy** (Leicester City) with **23 goals**.\n"
                "* **Runners-up**: Pierre-Emerick Aubameyang (Arsenal) and Danny Ings (Southampton) with 22 goals each."
            )
        elif "2021" in low or "2020/21" in low:
            if "champions league" in low:
                reply = (
                    "### 🏆 2020/21 UEFA Champions League\n\n"
                    "* **Winners**: **Chelsea FC** defeated Manchester City 1-0 in Porto (Kai Havertz 42')."
                )
            else:
                reply = (
                    "### ⚽ 2020/21 Premier League Top Scorer\n\n"
                    "* **Golden Boot Winner**: **Harry Kane** (Tottenham Hotspur) with **23 goals** and top playmaker (14 assists).\n"
                    "* **Runner-up**: Mohamed Salah (Liverpool) with 22 goals."
                )
        elif "2022" in low or "2021/22" in low:
            if "world cup" in low:
                reply = (
                    "### 🏆 2022 FIFA World Cup (Qatar)\n\n"
                    "* **Champions**: **Argentina** (defeated France 3-3, 4-2 on penalties).\n"
                    "* **Golden Boot**: Kylian Mbappé (France, 8 goals).\n"
                    "* **Golden Ball**: Lionel Messi (Argentina)."
                )
            else:
                reply = (
                    "### ⚽ 2021/22 Premier League Top Scorer\n\n"
                    "* **Golden Boot Winners (Tie)**: **Mohamed Salah** (Liverpool) and **Son Heung-min** (Tottenham) with **23 goals** each."
                )
        elif "2023" in low or "2022/23" in low:
            reply = (
                "### ⚽ 2022/23 Premier League Top Scorer\n\n"
                "* **Golden Boot Winner**: **Erling Haaland** (Manchester City) with an all-time record **36 goals** in 35 appearances.\n"
                "* **Runner-up**: Harry Kane (Tottenham Hotspur) with 30 goals."
            )
        elif "2024" in low or "2023/24" in low:
            reply = (
                "### ⚽ 2023/24 Premier League Top Scorer\n\n"
                "* **Golden Boot Winner**: **Erling Haaland** (Manchester City) with **27 goals**.\n"
                "* **Runner-up**: Cole Palmer (Chelsea) with 22 goals."
            )
        elif "2025" in low or "2024/25" in low:
            reply = (
                "### ⚽ 2024/25 Premier League Top Scorer\n\n"
                "* **Golden Boot Winner**: **Erling Haaland** (Manchester City) with **25 goals**."
            )
        elif "all time" in low or "all-time" in low:
            reply = (
                "### 🏆 Premier League All-Time Top Scorers\n\n"
                "1. **Alan Shearer** — 260 goals\n"
                "2. **Harry Kane** — 213 goals\n"
                "3. **Wayne Rooney** — 208 goals\n"
                "4. **Andrew Cole** — 187 goals\n"
                "5. **Sergio Agüero** — 184 goals\n"
                "6. **Frank Lampard** — 177 goals\n"
                "7. **Thierry Henry** — 175 goals\n"
                "8. **Mohamed Salah** — 160+ goals"
            )
        else:
            reply = (
                "### ⚽ Football Historical Records\n\n"
                "You can ask me about Premier League Golden Boot winners, all-time goal records, Champions League champions, or World Cup history!\n\n"
                f"For the active 2026/27 campaign, we are currently heading into **Gameweek {target_gw}**."
            )

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Who should I captain in Gameweek {target_gw}?",
                "All-time Premier League top scorers",
                "Who has the highest top speed on FotMob?"
            ]
        }

    # -------------------------------------------------------------------------
    # FotMob Direct Query Handlers
    # -------------------------------------------------------------------------

    def _format_player_fotmob_context(self, p: Dict[str, Any], target_gw: int = 5) -> str:
        """Extracts and formats official FotMob performance stats and advanced metrics for a player."""
        stats = p.get("all_fotmob_stats", {})
        def s(k):
            item = stats.get(k, {})
            val = item.get("value")
            rk = item.get("rank")
            rk_str = f" [PL Rank #{rk}]" if rk is not None else ""
            return f"{val}{rk_str}" if val is not None else "N/A"

        status_desc = "Active & Available"
        if p.get("is_transferred_out"):
            status_desc = f"TRANSFERRED OUT OF PREMIER LEAGUE ({p.get('injury_reason', 'Departed club')})"
        elif p.get("is_unavailable"):
            status_desc = f"UNAVAILABLE / INJURED ({p.get('injury_reason', 'Injured')}, 0% chance of playing)"
        elif p.get("is_doubtful"):
            status_desc = f"DOUBTFUL ({p.get('injury_reason', 'Doubtful')}, {p.get('chance_of_playing')}% chance of playing)"

        lines = [
            f"OFFICIAL FOTMOB & FPL STATS: {p.get('full_name')} ({p.get('team_name')} - {p.get('position')}):",
            f"- FPL Status: {status_desc}",
            f"- FotMob Player ID: {p.get('fotmob_id', 'N/A')}",
            f"- FotMob Match Rating: {s('FotMob rating')}",
            f"- Projected GW{target_gw} xP: {p.get('expected_points', 0.0):.2f}",
            f"- Goals: {p.get('goals', 0)} | Assists: {p.get('assists', 0)}",
            f"- Expected Goals (xG): {s('Expected goals (xG)')} | xG/90: {p.get('xg_90', 0.0):.2f}",
            f"- Expected Goals on Target (xGOT): {s('Expected goals on target (xGOT)')}",
            f"- Expected Assists (xA): {s('Expected assist (xA)')} | xA/90: {p.get('xa_90', 0.0):.2f}",
            f"- Chances Created: {s('Chances created')} | Big Chances Created: {s('Big chances created')}",
            f"- Shots / 90: {s('Shots per 90')} | Shots on Target / 90: {s('Shots on target per 90')}",
            f"- Top Speed: {s('Top speed')} km/h",
            f"- Total Sprints: {s('Total sprints')} | Sprints/90: {s('Sprints per 90')}",
            f"- Total Distance: {s('Total distance')} meters | Distance/90: {s('Distance per 90')} meters",
            f"- Accurate Passes / 90: {s('Accurate passes per 90')}",
            f"- Tackles / 90: {s('Tackles per 90')} | Recoveries / 90: {s('Recoveries per 90')}",
            f"- Possession Won Final 3rd / 90: {s('Possession won final 3rd per 90')}",
            f"- Primary Penalty Taker: {'YES (First Choice)' if p.get('is_penalty_taker') else 'No'}",
            f"- Set-Piece Taker (Corners/FKs): {'YES' if p.get('is_set_piece_taker') else 'No'}",
            f"- ICT Index: {p.get('ict_index', 'N/A')} (Influence: {p.get('influence', 'N/A')}, Creativity: {p.get('creativity', 'N/A')}, Threat: {p.get('threat', 'N/A')})",
            f"- Disciplinary & Defensive: Clean Sheets: {p.get('clean_sheets', 0)}, Yellows: {p.get('yellow_cards', 0)}, Reds: {p.get('red_cards', 0)}, Total BPS: {p.get('bps', 0)}"
        ]

        # Multi-season historical track record
        history = p.get("historical_past", [])
        if history:
            lines.append("\nOFFICIAL MULTI-SEASON FPL HISTORICAL LOGS:")
            sorted_hist = sorted(history, key=lambda x: x.get("season_name", ""), reverse=True)
            for s_entry in sorted_hist[:5]:
                s_name = s_entry.get("season_name")
                if s_name == "2025/26":
                    rel = "LAST SEASON (2025/26)"
                elif s_name == "2024/25":
                    rel = "2 seasons ago (2024/25)"
                elif s_name == "2023/24":
                    rel = "3 seasons ago (2023/24)"
                else:
                    rel = f"Season {s_name}"

                lines.append(
                    f"  * {rel}: {s_entry.get('total_points', 0)} total FPL pts | {s_entry.get('minutes', 0)} mins | "
                    f"{s_entry.get('goals_scored', 0)} goals | {s_entry.get('assists', 0)} assists | "
                    f"{s_entry.get('clean_sheets', 0)} clean sheets | {s_entry.get('bps', 0)} BPS | "
                    f"{float(s_entry.get('xg') or 0.0):.2f} xG | {float(s_entry.get('xa') or 0.0):.2f} xA"
                )

        return "\n".join(lines)

    def _get_fotmob_category_summary(self, query: str) -> str:
        """Extracts top performers for FotMob and metric leaderboard context."""
        low = query.lower()

        # ICT Metrics
        if any(k in low for k in ["ict", "influence", "creativity", "threat"]):
            key = "ict_index"
            title = "ICT Index"
            if "threat" in low:
                key = "threat"
                title = "Threat"
            elif "creativ" in low:
                key = "creativity"
                title = "Creativity"
            elif "influenc" in low:
                key = "influence"
                title = "Influence"

            ranked = []
            for p in self.re.unified_players:
                try:
                    val = float(p.get(key, 0) or 0)
                    if val > 0:
                        ranked.append((p.get("web_name", ""), p.get("team_short", "PL"), val, p.get("position", "")))
                except (ValueError, TypeError):
                    pass
            ranked.sort(key=lambda x: -x[2])
            top8 = ranked[:8]
            return f"Top Premier League {title} Leaders:\n" + "\n".join([f"- {r[0]} ({r[1]} • {r[3]}): {r[2]:.1f}" for r in top8])

        # BPS / Bonus
        if any(k in low for k in ["bps", "bonus points"]):
            ranked = []
            for p in self.re.unified_players:
                try:
                    val = int(p.get("bps", 0) or 0)
                    if val > 0:
                        ranked.append((p.get("web_name", ""), p.get("team_short", "PL"), val, p.get("position", "")))
                except (ValueError, TypeError):
                    pass
            ranked.sort(key=lambda x: -x[2])
            top8 = ranked[:8]
            return f"Top Premier League Total BPS (Bonus Points System):\n" + "\n".join([f"- {r[0]} ({r[1]} • {r[3]}): {r[2]} BPS" for r in top8])

        # FotMob metric categories
        category = "Top speed"
        unit = "km/h"
        if any(k in low for k in ["speed", "fastest", "fast", "pace"]):
            category = "Top speed"
            unit = "km/h"
        elif any(k in low for k in ["rating", "form", "best player"]):
            category = "FotMob rating"
            unit = ""
        elif any(k in low for k in ["chance", "key pass"]):
            category = "Chances created"
            unit = "chances"
        elif any(k in low for k in ["big chance", "big chances"]):
            category = "Big chances created"
            unit = "big chances"
        elif any(k in low for k in ["sprint"]):
            category = "Total sprints"
            unit = "sprints"
        elif any(k in low for k in ["distance"]):
            category = "Distance per 90"
            unit = "m/90"
        elif any(k in low for k in ["xgot"]):
            category = "Expected goals on target (xGOT)"
            unit = "xGOT"
        elif any(k in low for k in ["xg", "expected goals"]):
            category = "Expected goals (xG)"
            unit = "xG"
        elif any(k in low for k in ["xa", "expected assist"]):
            category = "Expected assist (xA)"
            unit = "xA"
        elif any(k in low for k in ["tackle", "tackles"]):
            category = "Tackles per 90"
            unit = "tackles/90"
        elif any(k in low for k in ["recover", "recoveries"]):
            category = "Recoveries per 90"
            unit = "recoveries/90"
        elif any(k in low for k in ["pass", "passing"]):
            category = "Accurate passes per 90"
            unit = "passes/90"
        elif any(k in low for k in ["possession won"]):
            category = "Possession won final 3rd per 90"
            unit = "/90"

        ranked = []
        for p in self.re.unified_players:
            st = p.get("all_fotmob_stats", {}).get(category)
            if st and st.get("value") is not None:
                try:
                    val = float(st["value"])
                    ranked.append((p.get("web_name", ""), p.get("team_short", "PL"), val, st.get("rank"), p.get("position", "")))
                except (ValueError, TypeError):
                    pass
        ranked.sort(key=lambda x: -x[2])
        top8 = ranked[:8]
        return f"Top Premier League {category} (FotMob Official):\n" + "\n".join([f"- {r[0]} ({r[1]} • {r[4]}): {r[2]} {unit} (Rank #{r[3]})" for r in top8])

    def _handle_fotmob_player_query(self, p: Dict[str, Any], text: str) -> Dict[str, Any]:
        """Provides a direct, concise FotMob statistics summary with simple language, refined to specific questions."""
        target_gw = self.current_gameweek
        stats = p.get("all_fotmob_stats", {})
        wn = p.get("web_name", "Player")
        fn = p.get("full_name", wn)
        team = p.get("team_name", p.get("team_short", "PL"))
        pos = p.get("position", "MID")
        fmid = p.get("fotmob_id", "N/A")
        rating = p.get("fotmob_rating", 6.0)
        low = (text or "").lower()

        def s(k):
            item = stats.get(k, {})
            v = item.get("value")
            return f"{v}" if v is not None else "0"

        # 1. Specific Refined Answer: Top Speed / Sprints
        if any(w in low for w in ["speed", "fastest", "pace", "sprint"]):
            spd = s("Top speed")
            sprints = s("Total sprints")
            reply = f"### ⚡ **{fn}** — Top Speed\n\n**{fn}** ({team}) has clocked a top speed of **{spd} km/h** with **{sprints} sprints** this season on FotMob."
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    "Top speeds in Premier League (FotMob)",
                    f"What are {wn}'s stats?",
                    "Who should I captain this week?"
                ],
                "player_id": p["id"],
                "fotmob_id": fmid
            }

        # 2. Specific Refined Answer: Match Rating
        if "rating" in low:
            reply = f"### ⭐ **{fn}** — Match Rating\n\n**{fn}** ({team}) currently holds an average match rating of **{rating:.2f}** on FotMob."
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"What are {wn}'s stats?",
                    f"Compare {wn} and Palmer",
                    "Who should I captain this week?"
                ],
                "player_id": p["id"],
                "fotmob_id": fmid
            }

        # 3. Specific Refined Answer: Chances Created & Creativity
        if any(w in low for w in ["chance", "chances", "creative", "creativity", "key pass", "key passes"]):
            chances = s("Chances created")
            big_chances = s("Big chances created")
            reply = f"### 🎯 **{fn}** — Chances Created\n\n**{fn}** ({team}) has created **{chances} chances** ({big_chances} big chances) in the Premier League this season."
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"What are {wn}'s stats?",
                    "Suggest a transfer strategy",
                    "Who should I captain this week?"
                ],
                "player_id": p["id"],
                "fotmob_id": fmid
            }

        # 4. Specific Refined Answer: Defensive Work (Tackles / Recoveries / Clean Sheets)
        if any(w in low for w in ["tackle", "tackles", "defend", "defensive", "recovery", "recoveries"]):
            tackles = s("Tackles per 90")
            cs = p.get("clean_sheets", 0)
            reply = f"### 🛡️ **{fn}** — Defensive Output\n\n**{fn}** ({team}) averages **{tackles} tackles per 90** with **{cs} clean sheets** this season."
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"What are {wn}'s stats?",
                    "Who should I captain this week?"
                ],
                "player_id": p["id"],
                "fotmob_id": fmid
            }

        # 5. General Snapshot (Direct 3-4 bullet breakdown, no wide tables)
        reply = f"### ⚽ **{fn}** ({team} • £{p.get('cost', 0):.1f}m • {pos})\n\n"
        reply += f"**Verdict**: Projected for **{p.get('expected_points', 0):.1f} points** in Gameweek {target_gw} with a **{rating:.2f}** FotMob rating.\n\n"
        reply += f"- **Goal Threat**: {s('Goals + Assists')} G/A ({s('Top scorer')} goals) • `{p.get('xg_90', 0.0):.2f}` xG/90\n"
        reply += f"- **Creativity**: {s('Chances created')} chances created ({s('Big chances created')} big chances)\n"
        reply += f"- **Speed & Work**: {s('Top speed')} km/h top speed • {s('Total sprints')} sprints\n"
        if pos in ["DEF", "GK"] or p.get("clean_sheets", 0) > 0:
            reply += f"- **Defense**: {p.get('clean_sheets', 0)} clean sheets • {s('Tackles per 90')} tackles/90\n"

        comp_target = "Haaland" if "palmer" in wn.lower() else "Palmer"
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Compare {wn} and {comp_target}",
                f"Who should I captain this week?",
                f"Suggest a transfer strategy for Gameweek {target_gw}"
            ],
            "player_id": p["id"],
            "fotmob_id": fmid
        }

    def _handle_fotmob_comparison(self, pA: Dict[str, Any], pB: Dict[str, Any]) -> Dict[str, Any]:
        """Compares two players directly with verdict first and simple terms."""
        target_gw = self.current_gameweek
        statsA = pA.get("all_fotmob_stats", {})
        statsB = pB.get("all_fotmob_stats", {})
        nameA = pA.get("web_name", "Player A")
        nameB = pB.get("web_name", "Player B")

        def val(st, k):
            v = st.get(k, {}).get("value")
            return f"{v}" if v is not None else "-"

        adv = nameA if pA.get("expected_points", 0) >= pB.get("expected_points", 0) else nameB
        other = nameB if adv == nameA else nameA
        diff = abs(pA.get("expected_points", 0) - pB.get("expected_points", 0))

        reply = f"### ⚖️ **{adv}** gets the nod over **{other}** (+{diff:.1f} pts)\n\n"
        reply += f"**Key Comparison (GW{target_gw})**:\n"
        reply += f"| Stat | **{nameA}** ({pA.get('team_short')}) | **{nameB}** ({pB.get('team_short')}) |\n"
        reply += f"| :--- | :--- | :--- |\n"
        reply += f"| **Projected Points** | **{pA.get('expected_points', 0):.1f} pts** | **{pB.get('expected_points', 0):.1f} pts** |\n"
        reply += f"| **Price** | £{pA.get('cost', 0):.1f}m | £{pB.get('cost', 0):.1f}m |\n"
        reply += f"| **Match Rating** | {pA.get('fotmob_rating', 0):.2f} | {pB.get('fotmob_rating', 0):.2f} |\n"
        reply += f"| **Goal Threat (xG)** | {val(statsA, 'Expected goals (xG)')} | {val(statsB, 'Expected goals (xG)')} |\n"
        reply += f"| **Chances Created** | {val(statsA, 'Chances created')} | {val(statsB, 'Chances created')} |\n\n"
        reply += f"**Recommendation**: Go with **{adv}** due to higher projected points and better current form."

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Query stats for {nameA}",
                f"Query stats for {nameB}",
                f"Who should I captain this week?"
            ]
        }

    def _handle_fotmob_leaderboard(self, query: str) -> Dict[str, Any]:
        """Returns official FotMob rankings for specific categories like top speed, ratings, etc."""
        low = query.lower()
        if any(k in low for k in ["speed", "fast", "pace"]):
            category = "Top speed"
            unit = " km/h"
            title = "Top Speed (km/h)"
        elif any(k in low for k in ["rating", "form", "best player"]):
            category = "FotMob rating"
            unit = ""
            title = "FotMob Match Rating"
        elif any(k in low for k in ["chance", "creator", "creative", "key pass"]):
            category = "Chances created"
            unit = ""
            title = "Chances Created"
        elif any(k in low for k in ["sprint"]):
            category = "Total sprints"
            unit = " sprints"
            title = "Total Sprints"
        elif any(k in low for k in ["distance"]):
            category = "Distance per 90"
            unit = " m"
            title = "Distance per 90 (meters)"
        elif any(k in low for k in ["xgot"]):
            category = "Expected goals on target (xGOT)"
            unit = ""
            title = "Expected Goals on Target (xGOT)"
        else:
            category = "FotMob rating"
            unit = ""
            title = "FotMob Match Rating"

        ranked = []
        for p in self.re.unified_players:
            st = p.get("all_fotmob_stats", {}).get(category)
            if st and st.get("value") is not None:
                try:
                    val = float(st["value"])
                    ranked.append((p["web_name"], p.get("team_short", "PL"), val, st.get("rank")))
                except (ValueError, TypeError):
                    continue

        ranked.sort(key=lambda x: -x[2])
        top_list = ranked[:7]

        reply = f"### ⚡ FotMob Leaderboard: **{title}**\n\n"
        reply += "| Rank | Player | Club | Value |\n"
        reply += "| :--- | :--- | :--- | :--- |\n"
        for i, (name, team, val, rank) in enumerate(top_list, 1):
            disp_rank = f"#{rank}" if rank else f"#{i}"
            val_str = f"{val:.1f}{unit}" if unit else f"{val:.2f}"
            reply += f"| **{disp_rank}** | {name} | {team} | `{val_str}` |\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Query FotMob for {top_list[0][0]}" if top_list else "Query FotMob for Haaland",
                "Top speeds in Premier League (FotMob)",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_fotmob_overview_help(self) -> Dict[str, Any]:
        """Provides an interactive guide for querying FotMob directly."""
        reply = (
            "### ⚡ Direct FotMob Stats Querying\n\n"
            "ChatFPL is directly connected to FotMob's Premier League database across **40+ granular performance metrics**.\n\n"
            "You can query directly for:\n"
            "- 🏃 **Pace & Physical**: Top speed (km/h), sprints/90, total distance covered.\n"
            "- 🎯 **Finishing & Threat**: xGOT (Expected Goals on Target), xG, shots on target.\n"
            "- 🎨 **Creativity**: Chances created, big chances, expected assists (xA).\n"
            "- 🛡️ **Pressing & Defending**: Tackles, recoveries, possession won in final 3rd.\n"
            "- 📊 **Match Ratings**: Official FotMob player ratings & Premier League ranks.\n\n"
            "Try any of the direct FotMob queries below:"
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Query FotMob for Erling Haaland",
                "What are Cole Palmer's stats on FotMob?",
                "Query FotMob for Bukayo Saka",
                "Top speeds in Premier League (FotMob)"
            ]
        }

    # -------------------------------------------------------------------------
    # Transfer Strategy Handlers
    # -------------------------------------------------------------------------

    def _handle_transfer_strategy_suggestion(self, user_squad: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Provides concrete, real-time transfer recommendations tailored to user's squad."""
        if not user_squad or not user_squad.get("starters"):
            user_squad = self.re.get_default_squad()

        starters_ids = user_squad.get("starters", [])
        bench_ids = user_squad.get("bench", [])
        bank = float(user_squad.get("bank", 0.5))
        ft = int(user_squad.get("free_transfers", 1))

        starters = [self.re.players_by_id[pid] for pid in starters_ids if pid in self.re.players_by_id]
        bench = [self.re.players_by_id[pid] for pid in bench_ids if pid in self.re.players_by_id]

        rec = self.re.recommend_transfers_and_chips(
            starters=starters_ids,
            bench=bench_ids,
            bank=bank,
            free_transfers=ft
        )

        action = rec.get("action", "ROLL_TRANSFER")
        transfers = rec.get("transfers", [])
        net_gain = rec.get("net_xp_gain", 0.0)
        target_gw = self.current_gameweek

        if action == "MAKE_TRANSFERS" and transfers:
            t = transfers[0]
            p_out = self.re.players_by_id[t["out"]]
            p_in = self.re.players_by_id[t["in"]]
            reply = f"### 🎯 Recommended Move: Sell {p_out['web_name']} ➔ Buy {p_in['web_name']}\n\n"
            reply += f"- **Sell**: **{p_out['web_name']}** ({p_out['team_short']} • £{p_out['cost']:.1f}m • {p_out['expected_points']:.1f} pts)\n"
            reply += f"- **Buy**: **{p_in['web_name']}** ({p_in['team_short']} • £{p_in['cost']:.1f}m • {p_in['expected_points']:.1f} pts)\n"
            reply += f"- **Why**: Easier fixture and higher goal threat. You gain **+{t['gain']:.1f} points** with £{t['new_bank']:.1f}m in the bank.\n\n"
        else:
            next_ft = min(5, ft + 1)
            reply = f"### 🛡️ Recommended Move: **Roll Your Free Transfer**\n\n"
            reply += f"- **Why**: Your starting eleven is solid and fixture ratings are favorable. No single transfer provides a substantial enough upgrade to justify spending your transfer.\n"
            reply += f"- **Transfer Discipline**: Rolling preserves your transfer equity and grows your balance to **{next_ft} Free Transfer{'s' if next_ft > 1 else ''}** for upcoming fixture swings, avoiding unnecessary point hits or squad churn.\n\n"

        # Flag any injured players
        injured = [p for p in starters if p.get("is_unavailable") or p.get("is_doubtful")]
        if injured:
            reply += "**⚠️ Injury Watch**:\n"
            for p in injured:
                reply += f"- **{p['web_name']}**: {p.get('injury_reason') or 'Doubtful'} ({p.get('chance_of_playing')}% chance)\n"
            reply += "\n"

        # Captaincy advice
        sorted_starters = sorted(starters, key=lambda x: -x.get("expected_points", 0))
        if sorted_starters:
            top_c = sorted_starters[0]
            reply += f"👑 **Captain Pick**: **{top_c['web_name']}** ({top_c['expected_points']:.1f} pts vs {top_c['upcoming_fixtures'][0].get('opponent', 'OPP')})"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Should I roll transfer?",
                "Who should I captain this week?",
                "Should I take a -4 hit?"
            ]
        }

    def _handle_transfer_count_and_hit_discipline(self, text: str, user_squad: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Provides expert tactical guidance discouraging making too many transfers in a single GW or taking low-conviction hits."""
        if not user_squad or not user_squad.get("starters"):
            user_squad = self.re.get_default_squad()

        bank = float(user_squad.get("bank", 0.5))
        ft = int(user_squad.get("free_transfers", 1))
        starters = [self.re.players_by_id[pid] for pid in user_squad.get("starters", []) if pid in self.re.players_by_id]
        flagged_starters = [p for p in starters if p.get("is_unavailable") or p.get("is_doubtful")]

        reply = "### 🛡️ Transfer Discipline & Hit Strategy Guide\n\n"
        reply += f"You currently have **{ft} Free Transfer{'s' if ft != 1 else ''}** available and **£{bank:.1f}m** in the bank.\n\n"
        reply += "**Key Principles on Transfer Volume in a Single Gameweek:**\n\n"
        reply += "1. **Avoid Making Too Many Transfers in One GW**:\n"
        reply += "   - Making 2+ or 3+ transfers in a single gameweek disrupts your starting XI continuity and burns valuable transfer equity on sideways moves that only yield fractional xP gains.\n"
        reply += "   - Under official **2026/27 rules**, you can bank up to **5 Free Transfers**. Patience is heavily rewarded.\n\n"
        reply += "2. **When to Roll Your Transfer**:\n"
        reply += f"   - If your starting 11 is fit and has decent fixtures, **roll your transfer**! Rolling this week will grow your banked FT balance to **{min(5, ft + 1)} FTs** next gameweek.\n"
        reply += "   - Multiple banked transfers allow you to target major fixture swings or double gameweeks without taking point penalties.\n\n"
        reply += "3. **Should You Take a -4 Hit?**:\n"
        if flagged_starters:
            reply += f"   - ⚠️ You currently have **{len(flagged_starters)} flagged starter{'s' if len(flagged_starters) > 1 else ''}** ({', '.join(p['web_name'] for p in flagged_starters)}).\n"
            reply += "   - A -4 point hit is **only justified** if an unavailable starter has 0 expected minutes and you have no playing bench cover, or if the incoming player has a massive immediate points advantage (>4.5 net xP).\n"
        else:
            reply += "   - ⛔ **Do not take a hit this gameweek**. Your starting XI has no confirmed injury crises. Taking a -4 point hit for speculative sideways transfers is statistically negative EV and damages your overall rank.\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Suggest a transfer strategy",
                "Who should I captain this week?",
                "Who should I bench?"
            ]
        }

    def _handle_player_replacement_query(self, p_out: Dict[str, Any], user_squad: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Finds the best replacement options for a specific player."""
        bank = 0.5
        if user_squad:
            bank = float(user_squad.get("bank", 0.5))

        pos = p_out.get("position", "MID")
        out_cost = p_out.get("cost", 5.0)
        out_xp = p_out.get("expected_points", 0.0)
        max_budget = out_cost + bank

        candidates = [
            p for p in self.re.unified_players
            if p["position"] == pos
            and p["id"] != p_out["id"]
            and p["cost"] <= max_budget
            and not p.get("is_unavailable")
            and not p.get("is_transferred_out")
            and not p.get("is_doubtful")
            and p.get("chance_of_playing", 100) >= 75
        ]
        candidates.sort(key=lambda x: -x.get("expected_points", 0))
        top_picks = candidates[:4]

        if not top_picks:
            reply = f"### 🔄 Replacements for **{p_out['web_name']}**\n\n"
            reply += f"No clear upgrades found within your budget of **£{max_budget:.1f}m**.\n"
        else:
            best = top_picks[0]
            gain = best['expected_points'] - out_xp
            reply = f"### 🔄 Top Replacement: **{best['web_name']}** (+{gain:.1f} pts)\n\n"
            reply += f"Best options to replace **{p_out['web_name']}** (Budget: £{max_budget:.1f}m):\n\n"
            for i, p in enumerate(top_picks[:3], 1):
                fix = p['upcoming_fixtures'][0]
                opp = fix.get('opponent', 'OPP')
                diff = fix.get('difficulty', 3)
                diff_label = "Easy" if diff <= 2 else ("Tough" if diff >= 4 else "Fair")
                reply += f"{i}. **{p['web_name']}** ({p['team_short']} • £{p['cost']:.1f}m) — **{p['expected_points']:.1f} pts** vs {opp} ({diff_label})\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Compare {p_out['web_name']} and {top_picks[0]['web_name']}" if top_picks else "Suggest a transfer strategy",
                "Who should I captain this week?",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_captaincy_query(self, user_squad: Optional[Dict[str, Any]] = None, target_p: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Recommends captain and vice-captain picks based on xP and FDR, directly evaluating any specific player inquired about."""
        if not user_squad or not user_squad.get("starters"):
            user_squad = self.re.get_default_squad()

        starters = [self.re.players_by_id[pid] for pid in user_squad.get("starters", []) if pid in self.re.players_by_id]
        starters.sort(key=lambda x: -x.get("expected_points", 0))

        if not starters:
            return self._handle_default_help()

        c = starters[0]
        vc = starters[1] if len(starters) > 1 else starters[0]
        target_gw = self.current_gameweek

        # If user specifically asked about captaining a named player (e.g. "Should I captain Saka?")
        if target_p:
            tp_xp = target_p.get("expected_points", 0.0)
            tp_name = target_p.get("web_name", "Player")
            tp_opp = target_p.get("upcoming_fixtures", [{}])[0].get("opponent", "OPP") if target_p.get("upcoming_fixtures") else "OPP"
            tp_diff = target_p.get("upcoming_fixtures", [{}])[0].get("difficulty", 3) if target_p.get("upcoming_fixtures") else 3
            diff_label = "Favorable" if tp_diff <= 2 else ("Tough" if tp_diff >= 4 else "Moderate")

            is_top = (c["id"] == target_p["id"])
            if is_top:
                reply = (
                    f"### 👑 Captaincy Verdict: **Yes, Captain {tp_name}!**\n\n"
                    f"**{tp_name}** ({target_p.get('team_short')} • £{target_p.get('cost', 0):.1f}m) is your **#1 projected captain** for Gameweek {target_gw}.\n\n"
                    f"- **Projected Points**: **{tp_xp:.1f} xP** vs {tp_opp} ({diff_label}, FDR {tp_diff})\n"
                    f"- **Attacking Output**: `{target_p.get('xg_90', 0.0):.2f}` xG/90 • `{target_p.get('xa_90', 0.0):.2f}` xA/90\n"
                    f"- **Vice-Captain Choice**: **{vc['web_name']}** ({vc['team_short']} • **{vc['expected_points']:.1f} pts**)"
                )
            else:
                diff_pts = c["expected_points"] - tp_xp
                reply = (
                    f"### 👑 Captaincy Verdict: **Prefer {c['web_name']} over {tp_name}** (+{diff_pts:.1f} pts)\n\n"
                    f"**{tp_name}** is a viable differential option (**{tp_xp:.1f} xP** vs {tp_opp}), but **{c['web_name']}** offers significantly higher expected returns.\n\n"
                    f"- **Optimal Captain**: **{c['web_name']}** ({c['team_short']}) — **{c['expected_points']:.1f} xP** vs {c['upcoming_fixtures'][0].get('opponent', 'OPP')}\n"
                    f"- **{tp_name} Differential Context**: Projected for **{tp_xp:.1f} xP**. If protecting overall rank, {c['web_name']} is safer; captain {tp_name} only if you are chasing a high-variance differential swing.\n"
                    f"- **Vice-Captain Option**: **{vc['web_name']}** ({vc['expected_points']:.1f} pts)"
                )
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"What are {tp_name}'s stats?",
                    f"Compare {tp_name} and {c['web_name']}",
                    "Suggest a transfer strategy"
                ],
                "player_id": target_p["id"]
            }

        reply = f"### 👑 Captain Pick: **{c['web_name']}** ({c['expected_points']:.1f} pts)\n\n"
        reply += f"**Primary Choice**: **{c['web_name']}** ({c['team_short']} • £{c['cost']:.1f}m)\n"
        reply += f"- **Fixture**: vs {c['upcoming_fixtures'][0].get('opponent', 'OPP')}\n"
        reply += f"- **Why**: Highest projected score this week with the best goal threat.\n\n"
        reply += f"**Vice-Captain**: **{vc['web_name']}** ({vc['team_short']} • {vc['expected_points']:.1f} pts vs {vc['upcoming_fixtures'][0].get('opponent', 'OPP')})"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Why captain {c['web_name']}?",
                f"Compare {c['web_name']} and {vc['web_name']}",
                "Suggest a transfer strategy"
            ]
        }

    # -------------------------------------------------------------------------
    # Mathematical Explanation Handlers (No LBP Boost)
    # -------------------------------------------------------------------------

    def _handle_player_xp_explanation(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Provides transparent step-by-step xP formula breakdown without line-breaking passes boost."""
        bk = p.get("xp_breakdown", {})
        wn = p.get("web_name", "Player")
        fn = p.get("full_name", wn)
        xp = p.get("expected_points", 0.0)
        team = p.get("team_short", "PL")
        cost = p.get("cost", 0.0)
        fix = p.get("upcoming_fixtures", [{}])[0]
        opp = fix.get("opponent", "OPP")
        venue = "H" if fix.get("is_home") else "A"
        fdr = fix.get("difficulty", 3)
        target_gw = self.current_gameweek

        reply = f"### 📊 Projection for **{fn}**: **{xp:.1f} points** (GW{target_gw})\n\n"
        reply += f"Here is how this projection is built in simple terms:\n\n"
        reply += f"1. **Expected Minutes**: Projected to start vs **{opp} ({venue})**.\n"
        reply += f"2. **Attacking Output**: Strong goal & assist threat based on recent matches.\n"
        reply += f"3. **Consistency**: Backed by proven multi-season Premier League output.\n"
        if fix.get("difficulty", 3) <= 2:
            reply += f"4. **Favorable Matchup**: Easy fixture boost vs {opp}.\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Compare {wn} with Palmer",
                "Suggest a transfer strategy",
                "Who should I captain this week?"
            ],
            "player_id": p["id"]
        }

    def _handle_player_overview(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Summarizes key stats for a player."""
        wn = p.get("web_name", "Player")
        fn = p.get("full_name", wn)
        xp = p.get("expected_points", 0.0)
        team = p.get("team_short", "PL")
        cost = p.get("cost", 0.0)
        goals = p.get("goals_scored", 0)
        assists = p.get("assists", 0)
        rating = p.get("fotmob_rating", 0.0)
        career_pts = p.get("career_total_fpl_points", 0)
        status_alert = ""
        if p.get("is_transferred_out") or p.get("status") in ["u", "n"]:
            status_alert = f"> ⚠️ **TRANSFERRED OUT OF PREMIER LEAGUE**: {wn} has transferred away from the Premier League ({p.get('news', 'Transferred abroad / Unattached')}) and will NOT score FPL points this season.\n\n"
        elif p.get("is_unavailable"):
            status_alert = f"> 🚨 **RULED OUT / INJURED**: {wn} is currently unavailable ({p.get('news', 'Ruled out for upcoming fixtures (0% chance of playing)')}).\n\n"
        elif p.get("is_doubtful") or (p.get("chance_of_playing") is not None and p.get("chance_of_playing") < 100):
            chance = p.get("chance_of_playing", 75)
            status_alert = f"> ⚠️ **INJURY CONCERN / DOUBT ({chance}% chance)**: {p.get('news', 'Player is carrying a knock / doubtful')}.\n\n"

        reply = f"### 👤 **{fn}** ({team} • £{cost:.1f}m • {p.get('position')})\n\n"
        if status_alert:
            reply += status_alert
        
        last_s = next((s for s in p.get("historical_past", []) if s.get("season_name") == "2025/26"), None)
        last_s_str = f"`{last_s.get('total_points', 0)} pts` ({last_s.get('goals_scored', 0)}G, {last_s.get('assists', 0)}A)" if last_s else "Debut campaign (no 2025/26 record)"
        target_gw = self.current_gameweek

        reply += f"- **Next Match (GW{target_gw})**: **{xp:.1f} projected points** vs {p.get('upcoming_fixtures', [{}])[0].get('opponent', 'OPP')}\n"
        reply += f"- **Current Season**: {goals} goals, {assists} assists (Rating: `{rating:.2f}`)\n"
        reply += f"- **Last Season**: {last_s_str}\n"
        reply += f"- **Career Points**: `{career_pts} pts` across {p.get('career_seasons_count', 0)} seasons\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Why does {wn} have {xp:.1f} points?",
                f"How did {wn} perform last season?",
                f"Replace {wn} with who?"
            ],
            "player_id": p["id"]
        }

    def _handle_player_specific_stat_or_history(self, p: Dict[str, Any], text: str = "") -> Dict[str, Any]:
        """Directly and concisely answers specific stat queries (xG, xA, goals, assists, minutes, speed, rating) for a player."""
        low = (text or "").lower()
        wn = p.get("web_name", "Player")
        fn = p.get("full_name", wn)
        team = p.get("team_short", "PL")
        pos = p.get("position", "")
        history = p.get("historical_past", [])

        # Detect target season: 2025/26 (last season), 2024/25, 2023/24, or current (2026/27)
        is_last_season = any(term in low for term in ["last season", "past season", "previous season", "2025/26", "25/26", "2025-26", "2025"])
        is_two_seasons_ago = any(term in low for term in ["two seasons ago", "2 seasons ago", "2024/25", "24/25", "2024-25"])
        is_three_seasons_ago = any(term in low for term in ["three seasons ago", "3 seasons ago", "2023/24", "23/24", "2023-24"])

        target_season_record = None
        season_label = "2025/26 (last season)"
        if is_last_season or ("season" in low and not any(k in low for k in ["this season", "current season", "2026/27", "2026"])):
            target_season_record = next((s for s in history if s.get("season_name") == "2025/26"), None)
            season_label = "2025/26 (last season)"
        elif is_two_seasons_ago:
            target_season_record = next((s for s in history if s.get("season_name") == "2024/25"), None)
            season_label = "2024/25 (2 seasons ago)"
        elif is_three_seasons_ago:
            target_season_record = next((s for s in history if s.get("season_name") == "2023/24"), None)
            season_label = "2023/24 (3 seasons ago)"

        # 1. SPECIFIC STAT INQUIRIES FOR PAST SEASONS
        if target_season_record:
            pts = target_season_record.get("total_points", 0)
            mins = target_season_record.get("minutes", 0)
            goals = target_season_record.get("goals_scored", 0)
            assists = target_season_record.get("assists", 0)
            xg = float(target_season_record.get("xg") or 0.0)
            xa = float(target_season_record.get("xa") or 0.0)
            cs = target_season_record.get("clean_sheets", 0)

            # Specific: xG / Expected Goals
            if any(term in low for term in ["xg", "expected goal", "expected goals"]):
                reply = (
                    f"### ⚽ **{fn}** — Expected Goals ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) recorded **{xg:.2f} xG**.\n\n"
                    f"- **Actual Goals**: {goals} goals\n"
                    f"- **Minutes**: {mins:,}' mins\n"
                    f"- **FPL Points**: {pts} pts"
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s assists from last season",
                        f"How many points did {wn} score last season?",
                        f"Who should I captain this week?"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: xA / Expected Assists
            if any(term in low for term in ["xa", "expected assist", "expected assists"]):
                reply = (
                    f"### 🎯 **{fn}** — Expected Assists ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) recorded **{xa:.2f} xA**.\n\n"
                    f"- **Actual Assists**: {assists} assists\n"
                    f"- **Minutes**: {mins:,}' mins\n"
                    f"- **FPL Points**: {pts} pts"
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s xG from last season",
                        f"{wn}'s goals from last season",
                        "Suggest a transfer strategy"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: Goals
            if any(term in low for term in ["goal", "goals", "scored", "top scorer"]):
                reply = (
                    f"### ⚽ **{fn}** — Goals ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) scored **{goals} goals** ({xg:.2f} xG) in {mins:,} minutes."
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s assists from last season",
                        f"{wn}'s xG from last season",
                        "Who should I captain this week?"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: Assists
            if any(term in low for term in ["assist", "assists"]):
                reply = (
                    f"### 👟 **{fn}** — Assists ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) provided **{assists} assists** ({xa:.2f} xA) in {mins:,} minutes."
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s goals from last season",
                        f"{wn}'s xG from last season",
                        "Suggest a transfer strategy"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: Minutes
            if any(term in low for term in ["minute", "minutes", "mins", "played"]):
                reply = (
                    f"### ⏱️ **{fn}** — Minutes Played ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) played **{mins:,} minutes** across the campaign."
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s goals from last season",
                        f"{wn}'s xG from last season",
                        "Suggest a transfer strategy"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: Clean Sheets
            if any(term in low for term in ["clean sheet", "clean sheets", "cs"]):
                reply = (
                    f"### 🛡️ **{fn}** — Clean Sheets ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) kept **{cs} clean sheets**."
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s points from last season",
                        "Who should I captain this week?"
                    ],
                    "player_id": p.get("id")
                }

            # Specific: Points
            if any(term in low for term in ["point", "points", "total points", "score"]):
                reply = (
                    f"### 🏆 **{fn}** — Total Points ({season_label})\n\n"
                    f"In **{season_label}**, **{fn}** ({team}) scored **{pts} FPL points** ({goals} goals, {assists} assists in {mins:,}' mins)."
                )
                return {
                    "success": True,
                    "reply": reply,
                    "suggestions": [
                        f"{wn}'s xG from last season",
                        "Who should I captain this week?"
                    ],
                    "player_id": p.get("id")
                }

            # General breakdown for that season (Clean bullet list, ZERO wide tables)
            reply = (
                f"### 👤 **{fn}** ({team} • {pos}) — {season_label}\n\n"
                f"- **Total Points**: **{pts} pts**\n"
                f"- **Goals & Assists**: {goals} goals • {assists} assists\n"
                f"- **Underlying Stats**: `{xg:.2f} xG` • `{xa:.2f} xA`\n"
                f"- **Minutes**: {mins:,}' mins ({cs} clean sheets)"
            )
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"{wn}'s xG from last season",
                    f"Who should I captain this week?",
                    "Suggest a transfer strategy"
                ],
                "player_id": p.get("id")
            }

        # 2. SPECIFIC STAT INQUIRIES FOR CURRENT SEASON (FotMob + FPL live)
        stats = p.get("all_fotmob_stats", {})
        def s(k):
            item = stats.get(k, {})
            v = item.get("value")
            return f"{v}" if v is not None else "0"

        if any(term in low for term in ["top speed", "speed", "fastest", "fast", "sprint", "sprints"]):
            reply = (
                f"### ⚡ **{fn}** — Speed & Sprints (2026/27)\n\n"
                f"- **Top Speed**: **{s('Top speed')} km/h**\n"
                f"- **Total Sprints**: **{s('Total sprints')}** ({s('Sprints per 90')} per 90)\n"
                f"- **Distance**: {s('Distance per 90')} m/90"
            )
            return {"success": True, "reply": reply, "suggestions": ["Top speeds in Premier League", f"Who should I captain this week?"]}

        if any(term in low for term in ["rating", "fotmob rating", "form rating"]):
            reply = (
                f"### ⭐ **{fn}** — Match Rating (2026/27)\n\n"
                f"- **FotMob Rating**: **{p.get('fotmob_rating', 6.0):.2f}** / 10\n"
                f"- **Current Form**: Projected for **{p.get('expected_points', 0):.1f} xP** next match."
            )
            return {"success": True, "reply": reply, "suggestions": [f"Compare {wn} with Palmer", "Who should I captain this week?"]}

        if any(term in low for term in ["xg", "expected goal", "expected goals"]):
            reply = (
                f"### ⚽ **{fn}** — Expected Goals (2026/27 Current Season)\n\n"
                f"- **Expected Goals (xG)**: **{s('Expected goals (xG)')}** (`{p.get('xg_90', 0.0):.2f}` per 90)\n"
                f"- **Goals Scored**: {p.get('goals_scored', 0)} goals\n"
                f"- **Shots**: {s('Shots per 90')} shots/90 ({s('Shots on target per 90')} on target)"
            )
            return {"success": True, "reply": reply, "suggestions": [f"{wn}'s xG from last season", "Who should I captain this week?"]}

        if any(term in low for term in ["xa", "expected assist", "expected assists", "chances created"]):
            reply = (
                f"### 🎯 **{fn}** — Creativity & xA (2026/27 Current Season)\n\n"
                f"- **Expected Assists (xA)**: **{s('Expected assist (xA)')}** (`{p.get('xa_90', 0.0):.2f}` per 90)\n"
                f"- **Chances Created**: {s('Chances created')} ({s('Big chances created')} big chances)\n"
                f"- **Assists**: {p.get('assists', 0)} assists"
            )
            return {"success": True, "reply": reply, "suggestions": [f"{wn}'s assists from last season", "Suggest a transfer strategy"]}

        # 3. MULTI-SEASON CAREER OVERVIEW (Clean bullet list, ZERO wide tables)
        if not history:
            reply = f"### 📜 **{fn}** ({team} • £{p.get('cost', 0):.1f}m)\n\nNo prior Premier League seasons on record (debut campaign in 2026/27)."
        else:
            sorted_hist = sorted(history, key=lambda x: x.get("season_name", ""), reverse=True)
            reply = f"### 📜 **{fn}** ({team} • £{p.get('cost', 0):.1f}m) — Premier League Record\n\n"
            for s_rec in sorted_hist[:3]:
                sn = s_rec.get("season_name")
                tag = "(Last Season)" if sn == "2025/26" else ""
                reply += (
                    f"**{sn} {tag}**:\n"
                    f"- **{s_rec.get('total_points', 0)} pts** • {s_rec.get('goals_scored', 0)}G, {s_rec.get('assists', 0)}A\n"
                    f"- `{float(s_rec.get('xg') or 0):.2f} xG` • `{float(s_rec.get('xa') or 0):.2f} xA` • {s_rec.get('minutes', 0):,}' mins\n\n"
                )

        return {
            "success": True,
            "reply": reply.strip(),
            "suggestions": [
                f"{wn}'s xG from last season",
                f"Who should I captain this week?",
                "Suggest a transfer strategy"
            ],
            "player_id": p.get("id")
        }

    def _handle_timeline_overview(self) -> Dict[str, Any]:
        """Authoritative breakdown of the current season and last season timeline."""
        gw_meta = self.get_gameweek_metadata()
        target_gw = gw_meta["target_gameweek"]
        deadline_str = gw_meta["deadline_display"]
        reply = (
            "### 🗓️ Premier League & FPL Season Timeline\n\n"
            f"- **Current Ongoing Season**: **2026/27** (currently Gameweek {target_gw}, {deadline_str})\n"
            "- **Last Season ('last season')**: **2025/26** (concluded in May 2026)\n"
            "  * 🏆 **Premier League Champions**: **Arsenal** (Mikel Arteta's side ended a 22-year title drought, dethroning Man City!)\n"
            "- **Two Seasons Ago**: **2024/25** (Champions: Liverpool)\n"
            "- **Three Seasons Ago**: **2023/24** (Champions: Manchester City)\n\n"
            "#### 🏆 2025/26 Campaign Highlights (Last Season):\n"
            "- **Arsenal**: Premier League Champions (May 2026)\n"
            "- **Bruno Fernandes**: 235 FPL points (9 goals, 24 assists for Man Utd)\n"
            "- **Cole Palmer**: 244 FPL points (15 goals, 14 assists for Chelsea)\n"
            "- **Mohamed Salah**: 211 FPL points (18 goals, 10 assists for Liverpool)\n"
            "- **Erling Haaland**: 217 FPL points (22 goals for Man City)\n\n"
            "💡 *Our engine weights historical track records (2025/26, 2024/25, 2023/24) at 60–65% to provide a resilient baseline for 2026/27 xP.*"
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Who should I captain this week?",
                "Bruno Fernandes stats last season",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_comparison(self, pA: Dict[str, Any], pB: Dict[str, Any]) -> Dict[str, Any]:
        """Compares two players side-by-side with tabular data."""
        nameA = pA.get("web_name", "Player A")
        nameB = pB.get("web_name", "Player B")

        reply = f"### ⚔️ Head-to-Head Comparison: **{nameA}** vs **{nameB}**\n\n"
        reply += f"| Metric | **{nameA}** ({pA.get('team_short')}) | **{nameB}** ({pB.get('team_short')}) | Advantage |\n"
        reply += f"| :--- | :--- | :--- | :--- |\n"
        reply += f"| **Price** | £{pA.get('cost', 0):.1f}m | £{pB.get('cost', 0):.1f}m | {'Cheaper ' + nameA if pA.get('cost',0) < pB.get('cost',0) else 'Cheaper ' + nameB} |\n"
        reply += f"| **Projected xP** | **{pA.get('expected_points', 0):.2f}** | **{pB.get('expected_points', 0):.2f}** | **{nameA if pA.get('expected_points',0) >= pB.get('expected_points',0) else nameB} (+{abs(pA.get('expected_points',0) - pB.get('expected_points',0)):.2f} xP)** |\n"
        reply += f"| **FotMob Match Rating** | {pA.get('fotmob_rating', 0):.2f} | {pB.get('fotmob_rating', 0):.2f} | {nameA if pA.get('fotmob_rating',0) >= pB.get('fotmob_rating',0) else nameB} |\n"
        reply += f"| **Holistic xG/90** | {pA.get('xg_90', 0):.2f} | {pB.get('xg_90', 0):.2f} | {nameA if pA.get('xg_90',0) >= pB.get('xg_90',0) else nameB} |\n"
        reply += f"| **Holistic xA/90** | {pA.get('xa_90', 0):.2f} | {pB.get('xa_90', 0):.2f} | {nameA if pA.get('xa_90',0) >= pB.get('xa_90',0) else nameB} |\n"
        reply += f"| **Career FPL Points** | {pA.get('career_total_fpl_points', 0)} pts | {pB.get('career_total_fpl_points', 0)} pts | {nameA if pA.get('career_total_fpl_points',0) >= pB.get('career_total_fpl_points',0) else nameB} |\n\n"

        target_gw = self.current_gameweek
        leader = nameA if pA.get('expected_points', 0) >= pB.get('expected_points', 0) else nameB
        diff = abs(pA.get('expected_points', 0) - pB.get('expected_points', 0))
        reply += f"💡 **ChatFPL Verdict**: **{leader}** holds a `+{diff:.2f} xP` advantage for Gameweek {target_gw}."

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Why does {leader} have higher xP?",
                "Suggest a transfer strategy",
                "Who should I captain?"
            ]
        }

    def _handle_holistic_explanation(self) -> Dict[str, Any]:
        """Explains why and how Holistic xG/xA uses Bayesian regression."""
        reply = (
            "### 🧠 What are Holistic xG & Holistic xA?\n\n"
            "In early gameweeks, individual match stats suffer from severe small-sample variance. "
            "To prevent distortions, our engine regresses current stats against multi-season history:\n\n"
            "$$\\text{Holistic Metric} = (\\text{Current Season Sample} \\times 35\\%) + (\\text{Multi-Season Career Baseline} \\times 65\\%)\n\n"
            "This ensures proven stars aren't penalized for one quiet match, and unproven players aren't overrated based on fluky early goals."
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Why is Multi-Season weighting 60-65%?",
                "Suggest a transfer strategy",
                "Why does Haaland have 11.82 xP?"
            ]
        }

    def _handle_multi_season_explanation(self) -> Dict[str, Any]:
        """Explains the 60-65% multi-season track record weighting rationale."""
        reply = (
            "### 📈 Multi-Season Track Record (60–65% Weighting)\n\n"
            "Our engine weights a player's **multi-year Premier League history at 62.5%** (target 60-65% range):\n"
            "- Incorporates past seasons points per 90 (2025/26, 2024/25, 2023/24).\n"
            "- Multi-year volume consistency bonus for proven titans (Virgil with 1,429 pts, Bruno with 1,263 pts, Saka with 1,077 pts).\n"
            "- Regresses unproven players to positional baselines to eliminate speculative hype."
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Suggest a transfer strategy",
                "Explain Holistic xG & xA",
                "Who should I captain?"
            ]
        }

    def _handle_stats_normalization_explanation(self) -> Dict[str, Any]:
        """Provides full mathematical breakdown of stats normalization for new entrants and injured players."""
        reply = (
            "### ⚖️ Stats Normalization & Small-Sample Calibration Engine\n\n"
            "To prevent severe distortions caused by small sample sizes, late summer transfers, or injury layoffs, "
            "our analytics engine applies **Empirical Bayes shrinkage** and **return-to-play ramp curves** across all Premier League players.\n\n"
            "#### 1. 🟢 Players Newly Entered into Premier League (New Signings / Promoted Stars)\n"
            "- **Sample-Size Shrinkage**: When a newcomer (e.g. Bradley Barcola, Daniel Muñoz, Gonzalo) has played limited minutes (e.g. <450 mins), "
            "their raw per-90 metrics (xG/90, xA/90, shots/90) are regressed toward a cost- and position-calibrated prior:\n"
            "  $$\\text{Rate}_{\\text{norm}} = C_{\\text{sample}} \\times \\text{Rate}_{\\text{raw}} + (1 - C_{\\text{sample}}) \\times \\text{Prior}_{\\text{cost, pos}}$$\n"
            "  where $C_{\\text{sample}} = \\frac{\\text{Minutes}}{\\text{Minutes} + 450}$.\n"
            "- **Playing Likelihood Recalibration**: Senior signings (£5.5m+) with 0 starts are **not** unfairly penalized as fringe reserve players; "
            "their playing probability is initialized at 65–80% as first-team integration occurs.\n\n"
            "#### 2. 🚑 Players Recently Injured or Returning to Fitness\n"
            "- **Return-to-Play Minutes Ramp**: When a player returns from injury (status changed to 'Available'), minutes load management is modeled explicitly:\n"
            "  * **Gameweek 0 (1st match back)**: Expected minutes ~45–60 mins (ramp factor: ~0.70x)\n"
            "  * **Gameweek +1 (2nd match back)**: Expected minutes ~70 mins (ramp factor: ~0.85x)\n"
            "  * **Gameweek +2+**: Full 90-minute match fitness (1.0x capacity)\n"
            "- **Form De-Dilution**: Official FPL 30-day rolling form treats missed injury matches as zeros. Our engine computes **Normalized Form** "
            "based on points per actual appearance, restoring fair evaluation for elite assets like Dominik Szoboszlai.\n"
            "- **Official Price Protection**: Under FPL rules, players unlocked from red injury flags receive an 8-day price freeze buffer, protecting them from immediate price drops."
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Who are the top new signings in the Premier League?",
                "Which players are returning from injury?",
                "Explain Holistic xG & xA"
            ]
        }

    def _handle_new_signings_and_entrants(self, text: str) -> Dict[str, Any]:
        """Scouts and ranks players who recently entered the Premier League."""
        new_entrants = [p for p in self.re.unified_players if p.get("is_new_pl_entrant") and not p.get("is_transferred_out")]
        new_entrants.sort(key=lambda x: (-x.get("cost", 0), -x.get("expected_points", 0)))

        top_marquee = [p for p in new_entrants if p.get("cost", 0) >= 6.0][:5]
        top_budget = [p for p in new_entrants if p.get("cost", 0) < 6.0 and p.get("current_minutes", 0) > 100][:5]

        lines = ["### 🟢 Top New Premier League Entrants & Signings\n"]
        lines.append("Here are the standout players who recently joined the Premier League, evaluated with **normalized per-90 metrics** and sample-size confidence:\n")

        lines.append("#### 🌟 Marquee & Key Signings (£6.0m+):")
        for p in top_marquee:
            cat = p.get("new_entrant_category", "Key Signing")
            conf = p.get("sample_size_confidence", 50.0)
            norm_xg = p.get("normalized_xg_90", 0.0)
            norm_xa = p.get("normalized_xa_90", 0.0)
            lines.append(
                f"- **{p['web_name']}** ({p['team_short']} • £{p['cost']:.1f}m {p['position']}) — **{p['expected_points']:.2f} xP**\n"
                f"  * *Category*: {cat} | *Sample*: {p['current_minutes']} mins ({conf:.0f}% confidence)\n"
                f"  * *Normalized Output*: {norm_xg:.2f} xG/90, {norm_xa:.2f} xA/90"
            )

        if top_budget:
            lines.append("\n#### 💎 Emerging Budget Entrants (<£6.0m):")
            for p in top_budget:
                conf = p.get("sample_size_confidence", 50.0)
                lines.append(
                    f"- **{p['web_name']}** ({p['team_short']} • £{p['cost']:.1f}m {p['position']}) — **{p['expected_points']:.2f} xP** "
                    f"({p['current_minutes']} mins played, {p.get('total_points', 0)} pts)"
                )

        lines.append("\n💡 *All metrics have been stabilized via Empirical Bayes shrinkage so small-sample flukes or early blanks don't distort projections.*")

        return {
            "success": True,
            "reply": "\n".join(lines),
            "suggestions": [
                "Which players are returning from injury?",
                "How does stats normalization work?",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_injury_and_returnees_query(self, text: str) -> Dict[str, Any]:
        """Provides status updates on currently injured assets and players ramping back from injury."""
        injured = [p for p in self.re.unified_players if p.get("is_recently_injured") and not p.get("is_transferred_out")]
        returnees = [p for p in self.re.unified_players if p.get("is_recent_injury_returnee") and not p.get("is_transferred_out")]

        injured.sort(key=lambda x: (-x.get("cost", 0), -x.get("total_points", 0)))
        returnees.sort(key=lambda x: (-x.get("cost", 0), -x.get("expected_points", 0)))

        lines = ["### 🚑 Premier League Injury & Returnee Monitor\n"]

        if returnees:
            lines.append("#### 🟢 Returning from Injury (Minutes Ramping Up):")
            lines.append("These assets have cleared medical flags and are progressively building back to full 90-minute match fitness:\n")
            for p in returnees[:5]:
                ramp = p.get("injury_return_ramp", 0.75) * 100
                exp_m = p.get("expected_minutes_projected", 60)
                norm_f = p.get("normalized_form", p.get("form", 0.0))
                lines.append(
                    f"- **{p['web_name']}** ({p['team_short']} • £{p['cost']:.1f}m {p['position']}) — **{p['expected_points']:.2f} xP**\n"
                    f"  * *Return Ramp*: {ramp:.0f}% capacity (~{exp_m} projected mins)\n"
                    f"  * *Normalized Form*: {norm_f:.1f} pts/match (official rolling form: {p.get('form', 0.0):.1f})\n"
                    f"  * *Price Status*: 🔒 Protected under official FPL 8-day price drop buffer"
                )

        lines.append("\n#### 🔴 High-Profile Sidelined / Carrying Doubts:")
        for p in injured[:6]:
            status_icon = "🔴" if p.get("status") in ["i", "s"] else "🟡"
            chance = p.get("chance_of_playing")
            chance_str = f" ({chance}% chance)" if chance is not None and chance < 100 else ""
            lines.append(
                f"- {status_icon} **{p['web_name']}** ({p['team_short']} • £{p['cost']:.1f}m {p['position']}): "
                f"*{p.get('injury_reason', 'Injured')}*{chance_str}"
            )

        return {
            "success": True,
            "reply": "\n".join(lines),
            "suggestions": [
                "Who are the top new signings in the Premier League?",
                "How does stats normalization work?",
                "Suggest a replacement for Isak"
            ]
        }

    def _handle_roll_transfer_explanation(self, user_squad: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Explains why the engine recommends rolling a free transfer."""
        reply = (
            "### 🛡️ Why Roll Free Transfer?\n\n"
            "In modern FPL, you can bank up to **5 Free Transfers**.\n\n"
            "- **Preserving Transfer Value**: If your starting XI is healthy and no clear upgrade provides positive net expected points or budget relief, rolling the free transfer preserves your flexibility.\n"
            "- Having **2 or more banked Free Transfers** gives you the tactical freedom to execute multi-player upgrades or restructure without taking -4 point hits."
        )
        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Suggest a transfer strategy",
                "Who should I captain?",
                "Best replacements for Van de Ven?"
            ]
        }

    def _handle_leaderboards(self, query: str) -> Dict[str, Any]:
        """Provides dynamic top player rankings."""
        target_gw = self.current_gameweek
        valid_pool = [
            p for p in self.re.unified_players
            if not p.get("is_unavailable")
            and not p.get("is_transferred_out")
            and not p.get("is_doubtful")
            and p.get("chance_of_playing", 100) >= 75
        ]
        if "defender" in query or "def" in query:
            players = [p for p in valid_pool if p.get("position") == "DEF"]
            players.sort(key=lambda x: -x.get("expected_points", 0))
            title = f"Top Projected Defenders (GW{target_gw})"
        elif "budget" in query or "cheap" in query or "differential" in query:
            players = [p for p in valid_pool if p.get("cost", 0) <= 6.0]
            players.sort(key=lambda x: -x.get("expected_points", 0))
            title = "Top Value & Budget Picks (≤ £6.0m)"
        else:
            players = valid_pool
            players.sort(key=lambda x: -x.get("expected_points", 0))
            title = f"Top Projected Players (GW{target_gw})"

        reply = f"### 🏆 {title}\n\n"
        for i, p in enumerate(players[:6], 1):
            reply += f"{i}. **{p.get('web_name')}** ({p.get('team_short')} • £{p.get('cost'):.1f}m) — `{p.get('expected_points', 0):.2f} xP` | vs {p.get('upcoming_fixtures')[0].get('opponent')}\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Why does {players[0].get('web_name')} have high xP?",
                "Suggest a transfer strategy",
                "Who should I captain?"
            ]
        }

    def _handle_bench_query(self, text: str, user_squad: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Provides analytical Starting XI vs. Bench recommendations."""
        if not user_squad or not user_squad.get("starters"):
            user_squad = self.re.get_default_squad()

        starters = [self.re.players_by_id[pid] for pid in user_squad.get("starters", []) if pid in self.re.players_by_id]
        bench = [self.re.players_by_id[pid] for pid in user_squad.get("bench", []) if pid in self.re.players_by_id]

        if not starters or not bench:
            return self._handle_transfer_strategy_suggestion(user_squad)

        bench_outfield = [p for p in bench if p.get("position") != "GKP"]
        bench_gk = [p for p in bench if p.get("position") == "GKP"]
        bench_outfield.sort(key=lambda x: -x.get("expected_points", 0))

        starters_sorted = sorted(starters, key=lambda x: x.get("expected_points", 0))
        lowest_starter = starters_sorted[0] if starters_sorted else None
        highest_bench = bench_outfield[0] if bench_outfield else None

        reply = "### 📋 Starting XI & Bench Optimization\n\n"

        if highest_bench and lowest_starter and highest_bench.get("expected_points", 0) > lowest_starter.get("expected_points", 0):
            gain = highest_bench.get("expected_points", 0) - lowest_starter.get("expected_points", 0)
            reply += (
                f"⚠️ **Benching Dilemma Detected:**\n"
                f"- Bench asset **{highest_bench.get('web_name')}** ({highest_bench.get('position')}, `{highest_bench.get('expected_points', 0):.2f} xP`) "
                f"is projected higher than starter **{lowest_starter.get('web_name')}** ({lowest_starter.get('position')}, `{lowest_starter.get('expected_points', 0):.2f} xP`).\n"
                f"- **Tactical Recommendation**: If your formation allows, start **{highest_bench.get('web_name')}** over **{lowest_starter.get('web_name')}** for an expected gain of **+{gain:.2f} xP**!\n\n"
            )
        else:
            reply += "✅ **Your Starting XI is currently optimal:** All outfield starters project for higher expected points than your bench options.\n\n"

        reply += "**Recommended Bench Priority Order:**\n"
        sub_ranks = ["1st Sub (Highest Priority)", "2nd Sub", "3rd Sub"]
        for idx, p in enumerate(bench_outfield):
            rank_label = sub_ranks[idx] if idx < len(sub_ranks) else f"{idx+1}th Sub"
            opp = p.get('upcoming_fixtures', [{}])[0].get('opponent', 'PL')
            reply += f"- **{rank_label}**: **{p.get('web_name')}** ({p.get('team_short')} • {p.get('position')}) — `{p.get('expected_points', 0):.2f} xP` (vs {opp})\n"

        if bench_gk:
            bgk = bench_gk[0]
            opp = bgk.get('upcoming_fixtures', [{}])[0].get('opponent', 'PL')
            reply += f"- **Backup Keeper**: **{bgk.get('web_name')}** ({bgk.get('team_short')}) — `{bgk.get('expected_points', 0):.2f} xP` (vs {opp})\n"

        doubtful = [p for p in starters if p.get("is_doubtful") or p.get("is_unavailable")]
        if doubtful:
            reply += "\n🚨 **Availability Alerts in Starting Lineup:**\n"
            for p in doubtful:
                reply += f"- **{p.get('web_name')}**: {p.get('injury_reason', 'Flagged as doubtful')} ({p.get('chance_of_playing', 0)}% chance of playing)\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Suggest a transfer strategy",
                "Who should I captain this week?",
                f"Explain {bench_outfield[0].get('web_name')}'s xP" if bench_outfield else "Best differential picks"
            ]
        }

    def _handle_budget_scouting(self, text: str) -> Dict[str, Any]:
        """Provides parametric scouting for budget, differential, and high-value options."""
        low = text.lower()

        pos_filter = None
        if any(w in low for w in ["mid", "midfielder", "winger"]):
            pos_filter = "MID"
        elif any(w in low for w in ["def", "defender", "centre-back", "fullback", "back"]):
            pos_filter = "DEF"
        elif any(w in low for w in ["fwd", "forward", "striker", "attacker"]):
            pos_filter = "FWD"
        elif any(w in low for w in ["gk", "gkp", "keeper", "goalkeeper"]):
            pos_filter = "GKP"

        budget_match = re.search(r'(?:under|<=|<|less than|budget|price of|cost of)\s*(?:£)?(\d+(?:\.\d+)?)m?', low)
        max_cost = float(budget_match.group(1)) if budget_match else None
        if not max_cost:
            if pos_filter in ["DEF", "GKP"]:
                max_cost = 5.0
            elif pos_filter == "MID":
                max_cost = 7.5
            elif pos_filter == "FWD":
                max_cost = 7.5
            else:
                max_cost = 6.5

        is_diff = any(w in low for w in ["diff", "differential", "low ownership", "hidden gem", "unowned"])

        candidates = [
            p for p in self.re.unified_players
            if not p.get("is_unavailable")
            and not p.get("is_transferred_out")
            and not p.get("is_doubtful")
            and p.get("chance_of_playing", 100) >= 75
        ]
        if pos_filter:
            candidates = [p for p in candidates if p.get("position") == pos_filter]
        if max_cost:
            candidates = [p for p in candidates if p.get("cost", 99.0) <= max_cost]
        if is_diff:
            def parse_own(p):
                try:
                    return float(str(p.get("selected_by_percent", 0)).replace("%", ""))
                except Exception:
                    return 100.0
            candidates = [p for p in candidates if parse_own(p) <= 10.0]

        candidates.sort(key=lambda x: -x.get("expected_points", 0))
        top_picks = candidates[:5]

        pos_str = pos_filter or "Player"
        diff_str = "Differential " if is_diff else ""
        title = f"Top Value {diff_str}{pos_str}s (≤ £{max_cost:.1f}m)"

        reply = f"### 🔍 {title}\n\n"
        reply += "| Player | Team | Pos | Cost | Own % | Projected xP | Next Fixture |\n"
        reply += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for p in top_picks:
            fix = p.get("upcoming_fixtures", [{}])[0]
            opp = f"{fix.get('opponent', 'PL')} ({'H' if fix.get('is_home') else 'A'})"
            own = p.get("selected_by_percent", "N/A")
            reply += f"| **{p.get('web_name')}** | {p.get('team_short')} | {p.get('position')} | £{p.get('cost'):.1f}m | {own}% | **{p.get('expected_points', 0):.2f}** | {opp} |\n"

        if top_picks:
            best = top_picks[0]
            reply += f"\n💡 **Scouting Verdict**: **{best.get('web_name')}** leads this category with **{best.get('expected_points', 0):.2f} xP**, offering strong underlying numbers and favorable fixture difficulty."

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Why does {top_picks[0].get('web_name')} have high xP?" if top_picks else "Suggest a transfer strategy",
                f"Compare {top_picks[0].get('web_name')} and {top_picks[1].get('web_name')}" if len(top_picks) >= 2 else "Who should I captain?",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_fpl_rules_and_chips(self, text: str) -> Dict[str, Any]:
        """Provides expert tactical advice on FPL chips, transfer hits, and scoring rules."""
        low = text.lower()
        if "wildcard" in low:
            reply = (
                "### 🃏 FPL Wildcard Strategy & Rules\n\n"
                "- **What it does**: Grants unlimited permanent transfers without point deductions. All changes remain locked into your squad for future gameweeks.\n"
                "- **Allocations**: You receive **two Wildcards** each season (1st available until GW19 deadline, 2nd from GW20 onwards).\n"
                "- **Best Deployment Windows**:\n"
                "  1. **Fixture Swings**: When 4+ top clubs experience significant fixture turns from red to green.\n"
                "  2. **International Breaks**: Extra market time to build team value through multiple price rises.\n"
                "  3. **Squad Surgery**: When carrying multiple injured or benched players where normal transfers would incur -8 or -12 hits."
            )
            chips = ["When should I use Free Hit?", "How does Bench Boost work?", "Suggest a transfer strategy"]
        elif "free hit" in low:
            reply = (
                "### 🎯 FPL Free Hit Strategy & Rules\n\n"
                "- **What it does**: Grants unlimited transfers for **ONE Gameweek only**. Your squad completely reverts to your previous gameweek team immediately after the gameweek concludes.\n"
                "- **Best Deployment Windows**:\n"
                "  1. **Major Blank Gameweeks (BGW)**: When several top teams have postponed matches due to cup ties, deploy Free Hit to field a full XI while rivals take hits.\n"
                "  2. **Double Gameweeks (DGW)**: Field a squad packed with players playing twice to maximize point upside.\n"
                "- **Crucial Rule**: Any regular transfer made before pressing Free Hit in the same gameweek is also wiped upon reversion."
            )
            chips = ["How does Wildcard work?", "How does Triple Captain work?", "Suggest a transfer strategy"]
        elif "bench boost" in low:
            reply = (
                "### 🚀 FPL Bench Boost Strategy & Rules\n\n"
                "- **What it does**: Automatically adds the scores of all **4 bench players** to your gameweek total.\n"
                "- **Best Deployment Windows**:\n"
                "  - **Double Gameweeks**: Optimal when all 15 players have two fixtures.\n"
                "  - **Wildcard Pairing**: Play Wildcard 1-2 weeks in advance to set up 15 playing starters before activating Bench Boost.\n"
                "- **Rule Note**: Only one power-up chip can be active per gameweek."
            )
            chips = ["When should I use Wildcard?", "Triple Captain Strategy", "Who should I bench this week?"]
        elif "triple captain" in low or "tc" in low:
            reply = (
                "### 👑 FPL Triple Captain Strategy & Rules\n\n"
                "- **What it does**: Multiplies your captain's score by **3x** instead of 2x (vice captain receives 3x if captain does not play).\n"
                "- **Best Deployment Windows**:\n"
                "  - **Double Gameweek Talisman**: Target an elite in-form premium (e.g., Erling Haaland or Mohamed Salah) when their team plays twice with favorable home matchups.\n"
                "  - **Historic Home Fixture**: An elite attacker facing a bottom-ranked defense at home."
            )
            chips = ["Who should I captain this week?", "When should I use Free Hit?", "Suggest a transfer strategy"]
        elif any(w in low for w in ["hit", "-4", "extra transfer"]):
            reply = (
                "### ⚡ Transfer Hits (-4 Points) Calculus\n\n"
                "- **The Math of a Hit**: A -4 hit deducts 4 points from your gameweek total. The incoming asset must outperform the outgoing player by at least **+4 points** over the next 2-3 gameweeks to break even.\n"
                "- **When Justified**:\n"
                "  1. Replacing an injured or benched player (projected ~0-1 pts) with an in-form starter (projected 6+ pts).\n"
                "  2. Enabling a high-ceiling captaincy pick for a Double Gameweek.\n"
                "- **When to Avoid**: Lateral transfers between similar players with marginal xP differences."
            )
            chips = ["Suggest a transfer strategy", "Why roll transfer?", "Who should I captain?"]
        elif any(w in low for w in ["bonus", "bps"]):
            reply = (
                "### 🏅 Bonus Points System (BPS) Explanation\n\n"
                "- **How it works**: The official FPL BPS tracks in-game statistics (goals, assists, chances created, clearances, tackles, pass completion %).\n"
                "- **Points Allocation**: The top 3 BPS earners in each match receive **3, 2, and 1 official bonus points**.\n"
                "- **Positional Edge**: Forwards receive 24 BPS for a goal, midfielders 18 BPS, and defenders 12 BPS. Defenders with clean sheets and high recovery volume frequently secure 3 bonus points."
            )
            chips = ["Explain xP formula", "Query FotMob for Erling Haaland", "Suggest a transfer strategy"]
        else:
            reply = (
                "### 📖 FPL Rules & Power-Up Chips Guide\n\n"
                "- **Free Transfers**: You receive 1 free transfer per gameweek and can bank up to **5 Free Transfers**.\n"
                "- **Power-Up Chips Available**:\n"
                "  - 🃏 **Wildcard (x2)**: Unlimited permanent squad restructuring.\n"
                "  - 🎯 **Free Hit (x1)**: One-gameweek temporary team.\n"
                "  - 🚀 **Bench Boost (x1)**: Score points from all 15 squad members.\n"
                "  - 👑 **Triple Captain (x1)**: Multiplies captain points by 3x.\n"
                "- **Deadlines**: Exactly 90 minutes before the first kickoff of each gameweek."
            )
            chips = ["When should I use Wildcard?", "When should I use Free Hit?", "Suggest a transfer strategy"]

        return {
            "success": True,
            "reply": reply,
            "suggestions": chips
        }

    def _handle_set_piece_query(self, text: str) -> Dict[str, Any]:
        """Provides primary penalty, corner, and direct free-kick takers."""
        team_short = self.find_team(text)

        if team_short:
            team_players = [p for p in self.re.unified_players if p.get("team_short") == team_short]
            team_name = team_players[0].get("team_name") if team_players else team_short

            pen_takers = [p for p in team_players if p.get("is_penalty_taker")]
            set_takers = [p for p in team_players if p.get("is_set_piece_taker")]

            pen_takers.sort(key=lambda x: -x.get("expected_points", 0))
            set_takers.sort(key=lambda x: -x.get("expected_points", 0))

            reply = f"### 🎯 Set-Piece & Penalty Takers: **{team_name}** ({team_short})\n\n"
            reply += "**Primary Penalty Takers:**\n"
            if pen_takers:
                for p in pen_takers:
                    reply += f"- ⚽ **{p.get('web_name')}** ({p.get('position')} • £{p.get('cost'):.1f}m) — `{p.get('expected_points', 0):.2f} xP`\n"
            else:
                reply += "- *Assigned based on matchday starting lineup*\n"

            reply += "\n**Direct Free-Kicks & Corners:**\n"
            if set_takers:
                for p in set_takers[:3]:
                    reply += f"- 🚩 **{p.get('web_name')}** ({p.get('position')} • £{p.get('cost'):.1f}m) — `{p.get('expected_points', 0):.2f} xP`\n"
            else:
                reply += "- *Rotated by set-piece tactical design*\n"

            reply += "\n💡 *Set-piece and penalty responsibility provides consistent goal and assist baseline floor for xP.*"

            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"Top projected players for {team_short}",
                    f"Upcoming fixtures for {team_short}",
                    "Suggest a transfer strategy"
                ]
            }
        else:
            top_pens = [p for p in self.re.unified_players if p.get("is_penalty_taker") and not p.get("is_unavailable")]
            top_pens.sort(key=lambda x: -x.get("expected_points", 0))

            reply = "### 🎯 Premier League Elite Penalty Takers\n\n"
            reply += "| Player | Team | Pos | Cost | Projected xP |\n"
            reply += "| :--- | :--- | :--- | :--- | :--- |\n"
            for p in top_pens[:6]:
                reply += f"| **{p.get('web_name')}** | {p.get('team_short')} | {p.get('position')} | £{p.get('cost'):.1f}m | **{p.get('expected_points', 0):.2f}** |\n"

            reply += "\nAsk about any specific club (e.g. *\"Who takes penalties for Arsenal?\"* or *\"Chelsea set-piece takers\"*)."
            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    "Who takes penalties for Arsenal?",
                    "Chelsea penalty taker",
                    "Man City penalty taker"
                ]
            }

    def _handle_fixture_query(self, text: str) -> Dict[str, Any]:
        """Analyzes team fixture difficulty ratings (FDR) and upcoming schedule."""
        team_short = self.find_team(text)

        if team_short:
            team_players = [p for p in self.re.unified_players if p.get("team_short") == team_short]
            sample = team_players[0] if team_players else None
            team_name = sample.get("team_name", team_short) if sample else team_short
            fixtures = sample.get("upcoming_fixtures", [])[:5] if sample else []

            reply = f"### 📅 Fixture Schedule & Difficulty: **{team_name}** ({team_short})\n\n"
            reply += "| GW | Opponent | Venue | FDR Difficulty |\n"
            reply += "| :--- | :--- | :--- | :--- |\n"

            def fdr_color(d):
                if d <= 2: return f"`{d}` 🟢 (Easy)"
                elif d == 3: return f"`{d}` ⚪ (Moderate)"
                elif d == 4: return f"`{d}` 🟠 (Tough)"
                return f"`{d}` 🔴 (Hard)"

            for f in fixtures:
                gw = f.get("event", "GW")
                opp = f.get("opponent", "PL")
                venue = "Home (H)" if f.get("is_home") else "Away (A)"
                diff = f.get("difficulty", 3)
                reply += f"| **GW{gw}** | {opp} | {venue} | {fdr_color(diff)} |\n"

            if fixtures:
                avg_diff = sum(f.get("difficulty", 3) for f in fixtures) / len(fixtures)
                rating_str = "favorable 🟢" if avg_diff <= 2.6 else ("difficult 🔴" if avg_diff >= 3.6 else "balanced ⚪")
                reply += f"\n**Upcoming 5-Match Average FDR**: `{avg_diff:.2f}` — Overall {rating_str} run of fixtures."

            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"Top projected players for {team_short}",
                    f"Who takes penalties for {team_short}?",
                    "Best fixture runs in Premier League"
                ]
            }
        else:
            team_fdr = {}
            for p in self.re.unified_players:
                t = p.get("team_short")
                if t and t not in team_fdr and p.get("upcoming_fixtures"):
                    next_4 = p.get("upcoming_fixtures")[:4]
                    if next_4:
                        avg_f = sum(f.get("difficulty", 3) for f in next_4) / len(next_4)
                        team_fdr[t] = (avg_f, p.get("team_name", t), [f.get("opponent") for f in next_4])

            sorted_teams = sorted(team_fdr.items(), key=lambda x: x[1][0])
            best_3 = sorted_teams[:3]
            hardest_3 = sorted_teams[-3:]

            reply = "### 🗓️ Premier League Upcoming Fixture Swings (Next 4 GWs)\n\n"
            reply += "🟢 **Top 3 Most Favorable Schedules (Target Assets):**\n"
            for t, (avg, name, opps) in best_3:
                reply += f"- **{name}** ({t}) — Avg FDR `{avg:.2f}` | Opponents: {', '.join(opps)}\n"

            reply += "\n🔴 **Top 3 Toughest Schedules (Caution):**\n"
            for t, (avg, name, opps) in reversed(hardest_3):
                reply += f"- **{name}** ({t}) — Avg FDR `{avg:.2f}` | Opponents: {', '.join(opps)}\n"

            return {
                "success": True,
                "reply": reply,
                "suggestions": [
                    f"Upcoming fixtures for {best_3[0][0]}",
                    f"Upcoming fixtures for {best_3[1][0]}",
                    "Suggest a transfer strategy"
                ]
            }

    def _handle_team_query(self, text: str) -> Dict[str, Any]:
        """Provides an analytical overview of a club's top fantasy assets, next match, and injuries."""
        team_short = self.find_team(text)
        if not team_short:
            return self._handle_fixture_query(text)

        team_players = [p for p in self.re.unified_players if p.get("team_short") == team_short and not p.get("is_unavailable")]
        team_name = team_players[0].get("team_name", team_short) if team_players else team_short
        team_players.sort(key=lambda x: -x.get("expected_points", 0))

        fixtures = team_players[0].get("upcoming_fixtures", []) if team_players else []
        next_fix = fixtures[0] if fixtures else {}
        opp = next_fix.get("opponent", "PL")
        venue = "Home" if next_fix.get("is_home") else "Away"
        diff = next_fix.get("difficulty", 3)

        reply = f"### 🛡️ Club Scouting: **{team_name}** ({team_short})\n\n"
        reply += f"**Next Match**: vs **{opp}** ({venue}) • Difficulty FDR: `{diff}/5`\n\n"
        reply += "**Top Projected Assets:**\n"

        for i, p in enumerate(team_players[:4], 1):
            role_tag = " (Penalty Taker 🎯)" if p.get("is_penalty_taker") else ""
            reply += f"{i}. **{p.get('web_name')}** ({p.get('position')} • £{p.get('cost'):.1f}m) — `{p.get('expected_points', 0):.2f} xP`{role_tag}\n"

        all_team = [p for p in self.re.unified_players if p.get("team_short") == team_short]
        injured = [p for p in all_team if p.get("is_doubtful") or p.get("is_unavailable")]
        if injured:
            reply += "\n⚠️ **Injury & Availability Notes:**\n"
            for p in injured[:3]:
                reply += f"- **{p.get('web_name')}**: {p.get('injury_reason', 'Flagged doubtful')} ({p.get('chance_of_playing', 0)}% chance)\n"

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                f"Upcoming fixtures for {team_short}",
                f"Who takes penalties for {team_short}?",
                "Suggest a transfer strategy"
            ]
        }

    def _handle_smart_discovery(self, text: str, user_squad: Optional[Dict[str, Any]] = None, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Conversational response for general greetings and discovery."""
        low = text.lower().strip()
        if any(w in low for w in ["hi", "hello", "hey", "sup", "greetings", "good morning", "good evening", "good afternoon"]):
            reply = "Hello! I'm ChatFPL, your FPL analytics and tactical assistant. Ask me anything about transfer strategies, player replacements, captaincy picks, bench dilemmas, or live Premier League stats."
        else:
            reply = "I'm here to help with your FPL squad! You can ask me to evaluate transfers, compare players, optimize your starting XI, recommend a captain, or analyze upcoming fixtures."

        return {
            "success": True,
            "reply": reply,
            "suggestions": [
                "Suggest a transfer strategy",
                "Who should I captain this week?",
                "Who should I bench?"
            ]
        }

    def _generate_contextual_suggestions(self, query: str, reply_text: str = "") -> List[str]:
        """Generates dynamic follow-up suggestions based on query and response context."""
        low = query.lower()
        reply_low = reply_text.lower() if reply_text else ""

        if any(k in low for k in ["bench", "starting xi"]):
            return ["Suggest a transfer strategy", "Who should I captain this week?", "Best differential picks"]
        elif any(k in low for k in ["fotmob", "speed", "xgot", "stats"]):
            return ["Query stats for Erling Haaland", "Compare Haaland and Palmer", "Suggest a transfer strategy"]
        elif "captain" in low:
            return ["Suggest a transfer strategy", "Who should I bench this week?", "Why roll transfer?"]
        elif any(k in low for k in ["transfer", "sell", "buy", "replace"]):
            return ["Who should I captain?", "Who should I bench this week?", "Upcoming fixture swings"]
        elif any(k in low for k in ["fixture", "schedule", "fdr"]):
            return ["Suggest a transfer strategy", "Who should I captain?", "Best differential midfielders"]
        elif any(k in low for k in ["penalty", "penalties", "set piece"]):
            return ["Who takes penalties for Arsenal?", "Who takes penalties for Chelsea?", "Suggest a transfer strategy"]

        return ["Suggest a transfer strategy", "Who should I captain?", "Who should I bench this week?"]
