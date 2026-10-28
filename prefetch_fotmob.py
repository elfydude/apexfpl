"""
prefetch_fotmob.py
Prefetches official FotMob heatmap coordinates and match shotmaps for key Premier League players.
Saves to data/fotmob_player_details_cache.json.
"""

import asyncio
import json
import os
import sys
import time

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fotmob import FotMob
from recommender_engine import RecommenderEngine

CACHE_PATH = os.path.join("data", "fotmob_player_details_cache.json")
cache = {}
if os.path.exists(CACHE_PATH):
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
            for k, v in raw.items():
                if isinstance(v, dict):
                    cache[str(k)] = {
                        "heatmap": v.get("heatmap") or [],
                        "shotmap": v.get("shotmap") or [],
                        "keeper_shotmap": v.get("keeper_shotmap") or []
                    }
    except Exception as e:
        print(f"Cache load error: {e}")

re = RecommenderEngine()
candidates = [p for p in re.unified_players if p.get("fotmob_id")]

# Sort by points, cost, minutes to prioritize most viewed players
candidates.sort(key=lambda x: (
    -(x.get("total_points", 0)),
    -(x.get("cost", 0)),
    -(x.get("current_minutes", 0))
))

target_players = candidates[:160]
print(f"Targeting {len(target_players)} top Premier League players for FotMob deep stats. Current cache size: {len(cache)}")

async def run():
    fm = FotMob()
    fetched_count = 0
    try:
        for idx, p in enumerate(target_players):
            fid = str(p["fotmob_id"])
            if fid in cache and (len(cache[fid].get("heatmap", [])) > 0 or len(cache[fid].get("shotmap", [])) > 0 or len(cache[fid].get("keeper_shotmap", [])) > 0):
                continue

            try:
                res = await fm.get_player(int(fid))
                if res:
                    fss = res.get("firstSeasonStats") or {}
                    heat = (fss.get("heatmap") or {}).get("coordinates") or []
                    shots = fss.get("shotmap") or []
                    keeper_shots = fss.get("keeperShotmap") or []
                    cache[fid] = {
                        "heatmap": heat,
                        "shotmap": shots,
                        "keeper_shotmap": keeper_shots
                    }
                    fetched_count += 1
                    pos = p.get("position", "")
                    fid_str = str(fid)
                    print(f"[{idx+1}/{len(target_players)}] {pos} ID {fid_str}: {len(heat)} heat pts, {len(shots)} shots, {len(keeper_shots)} gk shots")
                    
                    if fetched_count % 5 == 0:
                        with open(CACHE_PATH, "w", encoding="utf-8") as f:
                            json.dump(cache, f)
            except Exception as e:
                print(f"Error fetching ID {fid}: {e}")
            
            await asyncio.sleep(0.06)
    finally:
        await fm.close()
    
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(f"Prefetch complete. Fetched {fetched_count} new players. Total in cache: {len(cache)}")

if __name__ == "__main__":
    asyncio.run(run())
