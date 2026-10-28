"""
FotMob Scraper & Data Ingestion Engine
Fetches 40+ Premier League stats categories, squad rosters, injury lists, 
and match shotmaps from FotMob.
"""

import asyncio
import json
import os
import gzip
import urllib.request
from typing import Dict, List, Any, Optional
from fotmob import FotMob

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_FILE = os.path.join(CACHE_DIR, "fotmob_stats_cache.json")
PREMIER_LEAGUE_ID = 47

# Top Premier League FotMob Team IDs for squad & injury scraping
PL_TEAM_IDS = {
    "Arsenal": 9825,
    "Aston Villa": 10252,
    "Bournemouth": 8678,
    "Brentford": 9937,
    "Brighton": 10204,
    "Chelsea": 8455,
    "Crystal Palace": 9826,
    "Everton": 8668,
    "Fulham": 9879,
    "Ipswich": 9827,
    "Leicester": 8197,
    "Liverpool": 8650,
    "Man City": 8456,
    "Man United": 10260,
    "Newcastle": 10261,
    "Nottingham Forest": 10203,
    "Southampton": 8466,
    "Tottenham": 8586,
    "West Ham": 8654,
    "Wolves": 8602,
}

def fetch_gzip_json(url: str) -> Optional[Dict[str, Any]]:
    """Helper to fetch gzip or plain json from data.fotmob.com"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.fotmob.com/"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            try:
                decomp = gzip.decompress(raw)
                return json.loads(decomp.decode('utf-8'))
            except Exception:
                return json.loads(raw.decode('utf-8'))
    except Exception as e:
        print(f"[FotMob fetch_gzip_json] Error for {url}: {e}")
        return None

class FotMobEngine:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.client: Optional[FotMob] = None
        self.cached_data: Optional[Dict[str, Any]] = None

    async def init_client(self):
        if not self.client:
            self.client = FotMob()

    async def close(self):
        if self.client:
            try:
                res = self.client.close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
            self.client = None

    async def fetch_league_overview(self) -> Dict[str, Any]:
        """Fetch general league info and stat category download links"""
        await self.init_client()
        return await self.client.get_league(PREMIER_LEAGUE_ID)

    async def scrape_all_stats(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Scrapes full stats, squad lists, injuries, and creates a consolidated player index"""
        if not force_refresh and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    self.cached_data = json.load(f)
                    print(f"[FotMobEngine] Loaded {len(self.cached_data.get('players', {}))} players from cache.")
                    return self.cached_data
            except Exception as e:
                print(f"[FotMobEngine] Cache read error, refreshing: {e}")

        print("[FotMobEngine] Starting fresh data harvest from FotMob...")
        await self.init_client()
        league_info = await self.fetch_league_overview()
        
        stat_links = league_info.get("stats", {}).get("players", [])
        print(f"[FotMobEngine] Found {len(stat_links)} player stat categories to ingest.")

        # Aggregated stats indexed by player ID and lowercase full name
        players_by_id: Dict[str, Dict[str, Any]] = {}
        category_summaries: Dict[str, List[Any]] = {}

        for stat_item in stat_links:
            header = stat_item.get("header")
            fetch_url = stat_item.get("fetchAllUrl")
            if not fetch_url:
                continue

            data = fetch_gzip_json(fetch_url)
            if not data or "TopLists" not in data:
                continue

            for top_list in data.get("TopLists", []):
                stat_name = top_list.get("StatName", header)
                stat_list = top_list.get("StatList", [])
                category_summaries[header] = stat_list[:10]  # Store top 10 for quick leaderboard

                for entry in stat_list:
                    p_id = str(entry.get("ParticiantId") or entry.get("ParticipantId"))
                    p_name = entry.get("ParticipantName", "").strip()
                    if not p_name:
                        continue

                    if p_id not in players_by_id:
                        players_by_id[p_id] = {
                            "fotmob_id": p_id,
                            "name": p_name,
                            "team_id": entry.get("TeamId"),
                            "team_name": entry.get("TeamName"),
                            "country": entry.get("ParticipantCountryCode"),
                            "minutes_played": entry.get("MinutesPlayed", 0),
                            "matches_played": entry.get("MatchesPlayed", 0),
                            "stats": {},
                            "injuries": None,
                            "shotmap": [],
                        }

                    # Add specific stat
                    val = entry.get("StatValue")
                    rank = entry.get("Rank")
                    players_by_id[p_id]["stats"][header] = {
                        "value": val,
                        "rank": rank,
                        "sub_value": entry.get("SubStatValue")
                    }

        print(f"[FotMobEngine] Processed base stats for {len(players_by_id)} players.")

        # Next: Scrape squads for injury info & active positions
        injury_reports: List[Dict[str, Any]] = []
        for team_name, t_id in PL_TEAM_IDS.items():
            try:
                team_data = await self.client.get_team(t_id)
                squad_groups = team_data.get("squad", {}).get("squad", [])
                for group in squad_groups:
                    role_type = group.get("title")  # keepers, defenders, midfielders, attackers
                    for member in group.get("members", []):
                        m_id = str(member.get("id"))
                        m_name = member.get("name", "").strip()
                        injury = member.get("injury")
                        is_injured = member.get("injured", False) or (injury is not None)

                        if is_injured:
                            inj_entry = {
                                "player_id": m_id,
                                "name": m_name,
                                "team": team_name,
                                "injury": injury,
                                "status": "Injured" if not injury else injury.get("expectedReturn", "Injured")
                            }
                            injury_reports.append(inj_entry)

                        if m_id in players_by_id:
                            players_by_id[m_id]["injury"] = injury
                            players_by_id[m_id]["is_injured"] = is_injured
                            players_by_id[m_id]["role"] = role_type
                            players_by_id[m_id]["shirt_number"] = member.get("shirtNumber")
                        else:
                            # Add players who may not have topped any single stat yet
                            players_by_id[m_id] = {
                                "fotmob_id": m_id,
                                "name": m_name,
                                "team_id": t_id,
                                "team_name": team_name,
                                "country": member.get("ccode"),
                                "minutes_played": 0,
                                "matches_played": 0,
                                "role": role_type,
                                "shirt_number": member.get("shirtNumber"),
                                "stats": {
                                    "FotMob rating": {"value": member.get("rating", 6.0)},
                                    "Top scorer": {"value": member.get("goals", 0)},
                                    "Assists": {"value": member.get("assists", 0)}
                                },
                                "injury": injury,
                                "is_injured": is_injured,
                                "shotmap": [],
                            }
            except Exception as e:
                print(f"[FotMobEngine] Error loading team squad {team_name} ({t_id}): {e}")

        # Fetch match shotmaps for all players and team opponent shots (for Goalkeepers)
        team_opponent_shots: Dict[str, List[Dict[str, Any]]] = {}
        match_details_cache: Dict[str, Any] = {}
        total_shots_recorded = 0
        try:
            available_seasons = league_info.get("allAvailableSeasons", [])
            season_str = available_seasons[0] if available_seasons else "2024/2025"
            fixtures = await self.client.get_league_fixtures(PREMIER_LEAGUE_ID, season_str)
            if fixtures:
                completed = [fx for fx in fixtures if fx.get("status", {}).get("finished")]
                print(f"[FotMobEngine] Scanning {len(completed)} completed fixtures for shotmaps...")
                for idx, fix in enumerate(completed):
                    m_id = fix.get("id")
                    if not m_id:
                        continue
                    home_id = fix.get("home", {}).get("id")
                    away_id = fix.get("away", {}).get("id")
                    try:
                        m_det = await self.client.get_match_details(m_id)
                        content = m_det.get("content", {})
                        shots = content.get("shotmap", {}).get("shots", [])
                        total_shots_recorded += len(shots)

                        match_details_cache[str(m_id)] = {
                            "match_id": m_id,
                            "home_id": home_id,
                            "away_id": away_id,
                            "home_name": fix.get("home", {}).get("name"),
                            "away_name": fix.get("away", {}).get("name"),
                            "shots_count": len(shots)
                        }

                        for s in shots:
                            pid = s.get("playerId")
                            tid = s.get("teamId")
                            if pid and str(pid) in players_by_id:
                                players_by_id[str(pid)]["shotmap"].append(s)

                            opp_team_id = away_id if tid == home_id else home_id
                            if opp_team_id:
                                team_opponent_shots.setdefault(str(opp_team_id), []).append(s)
                    except Exception as me:
                        print(f"[FotMobEngine] Error parsing match {m_id}: {me}")
                print(f"[FotMobEngine] Total shots recorded: {total_shots_recorded} across {len(completed)} matches.")
        except Exception as e:
            print(f"[FotMobEngine] Warning: match shotmap sampling partial: {e}")

        payload = {
            "updated_at": "2026-09-07",
            "league_id": PREMIER_LEAGUE_ID,
            "total_players": len(players_by_id),
            "total_shots_recorded": total_shots_recorded,
            "players": players_by_id,
            "team_opponent_shots": team_opponent_shots,
            "match_details": match_details_cache,
            "injuries": injury_reports,
            "categories": list(category_summaries.keys())
        }

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        self.cached_data = payload
        print(f"[FotMobEngine] Successfully saved {len(players_by_id)} players and {len(injury_reports)} injuries to cache.")
        return payload

if __name__ == "__main__":
    async def main():
        engine = FotMobEngine()
        data = await engine.scrape_all_stats(force_refresh=True)
        print(f"Total players cached: {len(data['players'])}")
        print(f"Total injuries reported: {len(data['injuries'])}")
        await engine.close()
    asyncio.run(main())
