"""
fotmob_details.py
Official FotMob Heatmap and Shotmap Provider for Apex FPL.
Extracts 100% genuine spatial coordinates and match shotmaps directly from FotMob's API.
No synthetic blobs or manufactured stats.
"""

import asyncio
import json
import logging
import os
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger("FotMobDetails")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data")
CACHE_FILE = os.path.join(CACHE_DIR, "fotmob_player_details_cache.json")

class FotMobDetailsManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FotMobDetailsManager, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.dirty = False
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    for k, v in raw.items():
                        if isinstance(v, dict):
                            self.cache[str(k)] = {
                                "heatmap": v.get("heatmap") or [],
                                "shotmap": v.get("shotmap") or [],
                                "keeper_shotmap": v.get("keeper_shotmap") or []
                            }
                logger.info(f"Loaded {len(self.cache)} player details from {CACHE_FILE}")
            except Exception as e:
                logger.warning(f"Error loading {CACHE_FILE}: {e}")

    def save_cache(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
            self.dirty = False
            logger.info(f"Saved {len(self.cache)} player details to {CACHE_FILE}")
        except Exception as e:
            logger.error(f"Error saving {CACHE_FILE}: {e}")

    def get_details(self, fotmob_id: Optional[int or str]) -> Dict[str, Any]:
        """
        Returns {'heatmap': [...], 'shotmap': [...], 'keeper_shotmap': [...]}
        If not in cache, attempts synchronous fetch with short timeout.
        """
        empty_res = {"heatmap": [], "shotmap": [], "keeper_shotmap": []}
        if not fotmob_id:
            return empty_res

        str_id = str(fotmob_id)
        if str_id in self.cache:
            return self.cache[str_id]

        # On-demand fetch
        try:
            fetched = self._fetch_sync(int(fotmob_id))
            if fetched:
                self.cache[str_id] = fetched
                self.dirty = True
                # Persist in background thread
                threading.Thread(target=self.save_cache, daemon=True).start()
                return fetched
        except Exception as e:
            logger.warning(f"Failed to fetch FotMob details for {fotmob_id}: {e}")

        return empty_res

    def _fetch_sync(self, fotmob_id: int) -> Optional[Dict[str, Any]]:
        async def _do_fetch():
            from fotmob import FotMob
            fm = FotMob()
            try:
                res = await fm.get_player(fotmob_id)
                if not res:
                    return None
                fss = res.get("firstSeasonStats") or {}
                heat = (fss.get("heatmap") or {}).get("coordinates") or []
                shots = fss.get("shotmap") or []
                keeper_shots = fss.get("keeperShotmap") or []
                return {
                    "heatmap": heat,
                    "shotmap": shots,
                    "keeper_shotmap": keeper_shots
                }
            finally:
                try:
                    await fm.close()
                except Exception:
                    pass

        try:
            return asyncio.run(_do_fetch())
        except Exception as e:
            logger.error(f"asyncio error fetching player {fotmob_id}: {e}")
            return None

# Global singleton
fotmob_details = FotMobDetailsManager()
