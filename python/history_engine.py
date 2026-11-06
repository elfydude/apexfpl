"""
Harvest and cache multi-season historical performance for all official FPL players.
Extracts previous seasons (2025/26, 2024/25, 2023/24, etc.) directly from official FPL element-summary:
- Season Name
- Total Points
- Minutes Played & Starts
- Goals Scored & Assists (Official FPL scoring)
- Clean Sheets & Goals Conceded
- Cards (Yellow & Red) & Bonus Points
- Expected Goals (xG) & Expected Assists (xA)
"""

import os
import json
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data")
HISTORY_CACHE_FILE = os.path.join(CACHE_DIR, "player_history_cache.json")
BOOTSTRAP_FILE = os.path.join(CACHE_DIR, "fpl_bootstrap_cache.json")

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

def load_or_fetch_player_histories(force_refresh: bool = False, max_workers: int = 12) -> Dict[str, List[Dict[str, Any]]]:
    """Fetches and caches multi-season history for all players."""
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

    # Persist validated historical cache
    with open(HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(history_map, f, indent=2)

    total_seasons = sum(len(v) for v in history_map.values())
    print(f"[HistoryEngine] Harvest complete in {time.time() - start_time:.1f}s! Cached {len(history_map)} players with {total_seasons} sound historical season entries.")
    return history_map

if __name__ == "__main__":
    h = load_or_fetch_player_histories(force_refresh=True)
    print("Historical cache rebuilt successfully. Total players:", len(h))
