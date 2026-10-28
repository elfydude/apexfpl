"""
Harvest and cache multi-season historical performance for all key FPL players.
Extracts previous seasons (2025/26, 2024/25, 2023/24, etc.) including:
- Total Points
- Minutes played
- Goals scored & Assists
- Clean Sheets
- Expected Goals (xG) & Expected Assists (xA)
"""

import os
import json
import urllib.request
import time
from typing import Dict, List, Any

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data")
HISTORY_CACHE_FILE = os.path.join(CACHE_DIR, "player_history_cache.json")
BOOTSTRAP_FILE = os.path.join(CACHE_DIR, "fpl_bootstrap_cache.json")

def load_or_fetch_player_histories(max_players: int = 650) -> Dict[str, List[Dict[str, Any]]]:
    """Fetches and caches multi-season history for players."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(HISTORY_CACHE_FILE):
        try:
            with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if len(data) > 100:
                    print(f"[HistoryEngine] Loaded {len(data)} player historical records from cache.")
                    return data
        except Exception as e:
            print(f"[HistoryEngine] Cache read error: {e}")

    print("[HistoryEngine] Building player multi-season history cache...")
    if not os.path.exists(BOOTSTRAP_FILE):
        from fpl_engine import FPLEngine
        FPLEngine().fetch_bootstrap_static()

    with open(BOOTSTRAP_FILE, "r", encoding="utf-8") as f:
        bootstrap = json.load(f)

    elements = bootstrap.get("elements", [])
    # Sort by total_points and price so key players are processed first
    elements_sorted = sorted(elements, key=lambda x: (-(x.get("total_points") or 0), -(x.get("now_cost") or 0)))

    history_map: Dict[str, List[Dict[str, Any]]] = {}
    count = 0

    for el in elements_sorted[:max_players]:
        p_id = el["id"]
        url = f"https://fantasy.premierleague.com/api/element-summary/{p_id}/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                p_summary = json.loads(resp.read().decode("utf-8"))
                past_list = []
                for s in p_summary.get("history_past", []):
                    past_list.append({
                        "season_name": s.get("season_name"),
                        "total_points": s.get("total_points", 0),
                        "minutes": s.get("minutes", 0),
                        "goals_scored": s.get("goals_scored", 0),
                        "assists": s.get("assists", 0),
                        "clean_sheets": s.get("clean_sheets", 0),
                        "bps": s.get("bps", 0),
                        "xg": float(s.get("expected_goals") or 0.0),
                        "xa": float(s.get("expected_assists") or 0.0)
                    })
                history_map[str(p_id)] = past_list
                count += 1
                if count % 50 == 0:
                    print(f"[HistoryEngine] Harvested {count} player histories...")
        except Exception as e:
            history_map[str(p_id)] = []

    with open(HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(history_map, f, indent=2)

    print(f"[HistoryEngine] Completed harvesting {len(history_map)} player histories.")
    return history_map

if __name__ == "__main__":
    # Test harvest of top players
    h = load_or_fetch_player_histories(150)
    print("History sample player count:", len(h))
