"""
Harvest and cache multi-season historical performance for all official FPL players.
Extracts previous seasons (2025/26, 2024/25, 2023/24, etc.) directly from official FPL element-summary
and overlays authentic Opta Premier League assist records from FotMob:
- Season Name (sorted reverse-chronologically)
- Total Points
- Minutes Played
- Goals Scored & Assists (Official Premier League Opta data)
- Clean Sheets (tracked across all positions including forwards)
- Expected Goals (xG) & Expected Assists (xA)
"""

import os
import json
import urllib.request
import urllib.parse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data")
HISTORY_CACHE_FILE = os.path.join(CACHE_DIR, "player_history_cache.json")
BOOTSTRAP_FILE = os.path.join(CACHE_DIR, "fpl_bootstrap_cache.json")
FOTMOB_STATS_FILE = os.path.join(CACHE_DIR, "fotmob_stats_cache.json")

def fetch_single_player_history(p_id: int, max_retries: int = 2) -> tuple:
    """Fetches and parses element-summary for a single player with retry logic."""
    url = f"https://fantasy.premierleague.com/api/element-summary/{p_id}/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                p_summary = json.loads(resp.read().decode("utf-8"))
                past_list = []
                for s in p_summary.get("history_past", []):
                    mins = int(s.get("minutes") or 0)
                    # Filter out unplayed placeholder seasons (0 minutes played)
                    if mins == 0 and int(s.get("starts") or 0) == 0 and int(s.get("total_points") or 0) == 0:
                        continue
                    
                    past_list.append({
                        "season_name": s.get("season_name"),
                        "total_points": int(s.get("total_points") or 0),
                        "minutes": mins,
                        "starts": int(s.get("starts") or 0),
                        "goals_scored": int(s.get("goals_scored") or 0),
                        "assists": int(s.get("assists") or 0),
                        "clean_sheets": int(s.get("clean_sheets") or 0),
                        "goals_conceded": int(s.get("goals_conceded") or 0),
                        "yellow_cards": int(s.get("yellow_cards") or 0),
                        "red_cards": int(s.get("red_cards") or 0),
                        "bonus": int(s.get("bonus") or 0),
                        "bps": int(s.get("bps") or 0),
                        "xg": round(float(s.get("expected_goals") or 0.0), 2),
                        "xa": round(float(s.get("expected_assists") or 0.0), 2)
                    })
                # Sort descending by season_name so most recent seasons come first
                past_list.sort(key=lambda x: x.get("season_name", ""), reverse=True)
                return str(p_id), past_list
        except Exception:
            if attempt == max_retries:
                return str(p_id), []
            time.sleep(0.5)
    return str(p_id), []

def fetch_fotmob_career(fmid: str) -> tuple:
    """Fetches Opta Premier League career history for a player from FotMob."""
    url = f"https://www.fotmob.com/api/data/playerData?id={fmid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            senior = data.get("careerHistory", {}).get("careerItems", {}).get("senior", {})
            pl_stats = {}
            for s in senior.get("seasonEntries", []):
                s_name = s.get("seasonName", "")
                if len(s_name) == 9 and s_name[4] == "/":
                    std_sname = s_name[:5] + s_name[7:]
                else:
                    std_sname = s_name
                
                for t in s.get("tournamentStats", []):
                    if t.get("leagueName") == "Premier League":
                        try:
                            g = int(t.get("goals") or 0)
                            a = int(t.get("assists") or 0)
                            if std_sname not in pl_stats:
                                pl_stats[std_sname] = {"goals": 0, "assists": 0}
                            pl_stats[std_sname]["goals"] += g
                            pl_stats[std_sname]["assists"] += a
                        except Exception:
                            pass
            return fmid, pl_stats
    except Exception:
        return fmid, {}

def overlay_opta_stats(history_map: Dict[str, List[Dict[str, Any]]], elements: List[Dict[str, Any]], max_workers: int = 14):
    """Enriches FPL multi-season history with authentic Opta Premier League assist numbers from FotMob."""
    print("[HistoryEngine] Matching FotMob IDs to overlay official Opta Premier League assists...")
    
    # Load FotMob players cache if available
    fm_by_name = {}
    if os.path.exists(FOTMOB_STATS_FILE):
        try:
            with open(FOTMOB_STATS_FILE, "r", encoding="utf-8") as f:
                fm_data = json.load(f)
                for fid, fmp in fm_data.get("players", {}).items():
                    n = fmp.get("name", "").strip().lower()
                    if n:
                        fm_by_name[n] = str(fid)
        except Exception:
            pass

    fpl_to_fm = {}
    missing_elements = []

    for el in elements:
        pid = str(el["id"])
        full_n = f"{el.get('first_name', '')} {el.get('second_name', '')}".strip().lower()
        web_n = el.get("web_name", "").strip().lower()
        if full_n in fm_by_name:
            fpl_to_fm[pid] = fm_by_name[full_n]
        elif web_n in fm_by_name:
            fpl_to_fm[pid] = fm_by_name[web_n]
        else:
            missing_elements.append(el)

    # Search remaining players
    def search_fm_id(el):
        full_n = f"{el.get('first_name', '')} {el.get('second_name', '')}".strip()
        term = full_n if len(full_n) > 3 else el.get("web_name", "")
        url = f"https://www.fotmob.com/api/data/search/suggest?hits=8&lang=en&term={urllib.parse.quote(term)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data and isinstance(data, list):
                    sugg = data[0].get("suggestions", [])
                    players = [s for s in sugg if s.get("type") == "player"]
                    if players:
                        return str(el["id"]), str(players[0].get("id"))
        except Exception:
            pass
        return str(el["id"]), None

    if missing_elements:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(search_fm_id, el) for el in missing_elements]
            for f in as_completed(futs):
                pid, fmid = f.result()
                if fmid:
                    fpl_to_fm[pid] = fmid

    print(f"[HistoryEngine] Mapped {len(fpl_to_fm)}/{len(elements)} players to FotMob career records.")
    
    unique_fmids = list(set(fpl_to_fm.values()))
    fm_careers = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_fotmob_career, fmid): fmid for fmid in unique_fmids}
        for f in as_completed(futs):
            fmid, stats = f.result()
            if stats:
                fm_careers[fmid] = stats

    adjusted_count = 0
    for pid, seasons in history_map.items():
        fmid = fpl_to_fm.get(pid)
        if not fmid or fmid not in fm_careers:
            continue
        career = fm_careers[fmid]
        for s in seasons:
            s_name = s.get("season_name")
            if s_name in career:
                opta_a = career[s_name]["assists"]
                if s.get("assists") != opta_a:
                    s["assists"] = opta_a
                    adjusted_count += 1
                opta_g = career[s_name]["goals"]
                if opta_g > 0 and s.get("goals_scored") != opta_g:
                    s["goals_scored"] = opta_g

    print(f"[HistoryEngine] Overlay complete: adjusted {adjusted_count} seasons with Opta Premier League assist tallies.")

def load_or_fetch_player_histories(force_refresh: bool = False, max_workers: int = 12) -> Dict[str, List[Dict[str, Any]]]:
    """Fetches, cleans, and caches multi-season history for all players."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not force_refresh and os.path.exists(HISTORY_CACHE_FILE):
        try:
            with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if len(data) >= 500:
                    print(f"[HistoryEngine] Loaded {len(data)} player historical records from cache.")
                    return data
        except Exception as e:
            print(f"[HistoryEngine] Cache read error: {e}")

    print("[HistoryEngine] Building player multi-season history cache across all active players...")
    if not os.path.exists(BOOTSTRAP_FILE):
        from fpl_engine import FPLEngine
        FPLEngine().fetch_bootstrap_static()

    with open(BOOTSTRAP_FILE, "r", encoding="utf-8") as f:
        bootstrap = json.load(f)

    elements = bootstrap.get("elements", [])
    history_map: Dict[str, List[Dict[str, Any]]] = {}

    print(f"[HistoryEngine] Harvesting history for {len(elements)} players using {max_workers} concurrent threads...")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_single_player_history, el["id"]): el["id"] for el in elements}
        completed = 0
        for future in as_completed(futures):
            pid, past_list = future.result()
            history_map[pid] = past_list
            completed += 1
            if completed % 100 == 0 or completed == len(elements):
                print(f"[HistoryEngine] Harvested {completed}/{len(elements)} player histories ({time.time() - start_time:.1f}s)...")

    # Overlay verified Opta stats
    overlay_opta_stats(history_map, elements, max_workers=max_workers)

    # Persist validated historical cache
    with open(HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(history_map, f, indent=2)

    total_seasons = sum(len(v) for v in history_map.values())
    print(f"[HistoryEngine] Harvest complete in {time.time() - start_time:.1f}s! Cached {len(history_map)} players with {total_seasons} sound historical season entries.")
    return history_map

if __name__ == "__main__":
    h = load_or_fetch_player_histories(force_refresh=True)
    print("Historical cache rebuilt successfully. Total players:", len(h))
