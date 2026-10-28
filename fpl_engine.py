"""
Official Fantasy Premier League (FPL) Data Ingestion & Unification Engine
Integrates:
- Official player pricing (e.g., £15.0m for Haaland, £10.0m for Palmer)
- Real-time team affiliations & positions (GKP, DEF, MID, FWD)
- Upcoming fixture schedules & Fixture Difficulty Ratings (FDR)
- Manager Lineup Import via FPL Manager ID (with fallback to latest finished/active gameweek)
"""

import json
import os
import time
import re
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")
FPL_CACHE_FILE = os.path.join(CACHE_DIR, "fpl_bootstrap_cache.json")
FIXTURES_CACHE_FILE = os.path.join(CACHE_DIR, "fpl_fixtures_cache.json")
CACHE_TTL_SECONDS = 900  # 15 minutes TTL for live FPL data

import logging

logger = logging.getLogger("FPLEngine")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

POSITION_MAP = {
    1: "GKP",
    2: "DEF",
    3: "MID",
    4: "FWD"
}

def extract_manager_id(input_val: Any) -> Optional[int]:
    """Extracts integer manager ID from numbers, strings, or FPL website URLs."""
    if input_val is None:
        return None
    val_str = str(input_val).strip()
    # Match /entry/123456/ in URLs
    match = re.search(r'/entry/(\d+)', val_str)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    # Match /my-team/123456/ or /transfers/123456/
    match_mt = re.search(r'/(?:my-team|transfers)/(\d+)', val_str)
    if match_mt:
        try:
            return int(match_mt.group(1))
        except ValueError:
            pass
    # Match standalone integer
    num_match = re.search(r'\b\d{1,9}\b', val_str)
    if num_match:
        try:
            return int(num_match.group(0))
        except ValueError:
            pass
    return None

class FPLEngine:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.bootstrap_data: Optional[Dict[str, Any]] = None
        self.fixtures_data: Optional[List[Dict[str, Any]]] = None
        self._last_bootstrap_fetch: float = 0.0
        self._last_fixtures_fetch: float = 0.0
        self.on_refresh_callback = None
        self._refreshing_callback = False

    def _notify_refresh(self, data: Dict[str, Any]):
        callback = getattr(self, "on_refresh_callback", None)
        if callback and callable(callback) and not getattr(self, "_refreshing_callback", False):
            try:
                self._refreshing_callback = True
                callback(data)
            except Exception as cb_err:
                logger.error(f"[FPLEngine] Error in on_refresh_callback: {cb_err}")
            finally:
                self._refreshing_callback = False

    def set_bootstrap_data(self, data: Dict[str, Any], notify: bool = True):
        """Sets bootstrap data and optionally notifies engine listeners."""
        self.bootstrap_data = data
        self._last_bootstrap_fetch = time.time()
        try:
            with open(FPL_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"[FPLEngine] Could not write cache file: {e}")
        if notify:
            self._notify_refresh(data)

    def is_cache_stale(self, file_path: str, max_age: float = CACHE_TTL_SECONDS) -> bool:
        if not os.path.exists(file_path):
            return True
        try:
            mtime = os.path.getmtime(file_path)
            return (time.time() - mtime) > max_age
        except Exception:
            return True

    def fetch_bootstrap_static(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetches bootstrap-static containing players, teams, and live Gameweek states."""
        now = time.time()
        if not force_refresh and self.bootstrap_data and (now - self._last_bootstrap_fetch < CACHE_TTL_SECONDS):
            return self.bootstrap_data

        if not force_refresh and os.path.exists(FPL_CACHE_FILE) and not self.is_cache_stale(FPL_CACHE_FILE):
            try:
                with open(FPL_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.bootstrap_data = json.load(f)
                    self._last_bootstrap_fetch = os.path.getmtime(FPL_CACHE_FILE)
                    return self.bootstrap_data
            except Exception as e:
                print(f"[FPLEngine] Cache read error: {e}")

        url = "https://fantasy.premierleague.com/api/bootstrap-static/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with open(FPL_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                self.bootstrap_data = data
                self._last_bootstrap_fetch = time.time()
                if force_refresh:
                    self._notify_refresh(data)
                return data
        except Exception as e:
            print(f"[FPLEngine] Error fetching bootstrap-static: {e}")
            if self.bootstrap_data:
                if force_refresh:
                    self._notify_refresh(self.bootstrap_data)
                return self.bootstrap_data
            if os.path.exists(FPL_CACHE_FILE):
                with open(FPL_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.bootstrap_data = json.load(f)
                    self._last_bootstrap_fetch = os.path.getmtime(FPL_CACHE_FILE)
                    if force_refresh:
                        self._notify_refresh(self.bootstrap_data)
                    return self.bootstrap_data
            raise

    def fetch_upcoming_fixtures(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetches fixtures for upcoming gameweeks directly from official FPL."""
        now = time.time()
        if not force_refresh and self.fixtures_data and (now - self._last_fixtures_fetch < CACHE_TTL_SECONDS):
            return self.fixtures_data

        if not force_refresh and os.path.exists(FIXTURES_CACHE_FILE) and not self.is_cache_stale(FIXTURES_CACHE_FILE):
            try:
                with open(FIXTURES_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.fixtures_data = json.load(f)
                    self._last_fixtures_fetch = os.path.getmtime(FIXTURES_CACHE_FILE)
                    return self.fixtures_data
            except Exception as e:
                print(f"[FPLEngine] Fixture cache read error: {e}")

        url = "https://fantasy.premierleague.com/api/fixtures/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with open(FIXTURES_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                self.fixtures_data = data
                self._last_fixtures_fetch = time.time()
                return data
        except Exception as e:
            print(f"[FPLEngine] Error fetching fixtures: {e}")
            if self.fixtures_data:
                return self.fixtures_data
            if os.path.exists(FIXTURES_CACHE_FILE):
                with open(FIXTURES_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise

    def get_current_gameweek(self) -> int:
        """Returns the upcoming next gameweek to prepare transfers and fixtures for (e.g. GW 4)."""
        bootstrap = self.fetch_bootstrap_static()
        events = bootstrap.get("events", [])
        now_iso = datetime.now(timezone.utc).isoformat()
        for ev in events:
            if ev.get("is_next"):
                return ev.get("id", 1)
        # If no event is marked is_next, find the first event whose deadline has not yet passed
        for ev in events:
            deadline = ev.get("deadline_time")
            if deadline and deadline > now_iso:
                return ev.get("id", 1)
        # Fallback to first unfinished event
        for ev in events:
            if not ev.get("finished"):
                return ev.get("id", 1)
        return 38

    def get_latest_completed_gameweek(self) -> int:
        """Returns the most recent gameweek where picks have been made and locked."""
        bootstrap = self.fetch_bootstrap_static()
        events = bootstrap.get("events", [])
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Consider events completed if finished OR deadline has passed
        completed = []
        for ev in events:
            if ev.get("finished"):
                completed.append(ev["id"])
            elif ev.get("deadline_time") and ev.get("deadline_time") < now_iso and not ev.get("is_next"):
                completed.append(ev["id"])
        
        if completed:
            return max(completed)
            
        for ev in events:
            if ev.get("is_current"):
                return ev.get("id", 1)
        return 1

    def get_gameweek_info(self) -> Dict[str, Any]:
        """Provides rich metadata about the current and next upcoming gameweek."""
        bootstrap = self.fetch_bootstrap_static()
        events = bootstrap.get("events", [])
        next_gw = self.get_current_gameweek()
        latest_done = self.get_latest_completed_gameweek()
        
        next_ev = next((ev for ev in events if ev.get("id") == next_gw), {})
        return {
            "target_gameweek": next_gw,
            "latest_completed_gameweek": latest_done,
            "gameweek_name": next_ev.get("name", f"Gameweek {next_gw}"),
            "deadline_time": next_ev.get("deadline_time", "")
        }

    def get_teams_map(self) -> Dict[int, Dict[str, Any]]:
        bootstrap = self.fetch_bootstrap_static()
        return {t["id"]: t for t in bootstrap.get("teams", [])}

    def get_upcoming_team_fdr(self, lookahead_gameweeks: Optional[int] = None) -> Dict[int, List[Dict[str, Any]]]:
        """Calculates fixture difficulty for each team starting from upcoming gameweek through complete season."""
        fixtures = self.fetch_upcoming_fixtures()
        current_gw = self.get_current_gameweek()
        teams_map = self.get_teams_map()
        if lookahead_gameweeks and lookahead_gameweeks > 0:
            target_gws = set(range(current_gw, current_gw + lookahead_gameweeks))
        else:
            target_gws = set(range(current_gw, 39))

        fdr_schedule: Dict[int, List[Dict[str, Any]]] = {tid: [] for tid in teams_map.keys()}

        # Filter and sort fixtures by event ascending
        valid_fixes = [f for f in fixtures if f.get("event") in target_gws]
        valid_fixes.sort(key=lambda x: (x.get("event", 0), x.get("kickoff_time") or ""))

        for fix in valid_fixes:
            ev = fix.get("event")
            t_home = fix.get("team_h")
            t_away = fix.get("team_a")
            diff_home = fix.get("team_h_difficulty", 3)
            diff_away = fix.get("team_a_difficulty", 3)

            if t_home in fdr_schedule:
                opp = teams_map.get(t_away, {}).get("short_name", "OPP")
                fdr_schedule[t_home].append({
                    "event": ev,
                    "is_home": True,
                    "opponent": opp,
                    "difficulty": diff_home
                })

            if t_away in fdr_schedule:
                opp = teams_map.get(t_home, {}).get("short_name", "OPP")
                fdr_schedule[t_away].append({
                    "event": ev,
                    "is_home": False,
                    "opponent": opp,
                    "difficulty": diff_away
                })

        return fdr_schedule


    def fetch_manager_entry(self, manager_id: int) -> Dict[str, Any]:
        """
        Fetches manager entry profile details from official FPL API:
        - player_first_name, player_last_name (Manager Name)
        - name (Team Name)
        - current_event, started_event
        - last_deadline_bank, last_deadline_value
        - summary_overall_points, summary_overall_rank
        """
        url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                first = data.get("player_first_name", "").strip()
                last = data.get("player_last_name", "").strip()
                manager_name = f"{first} {last}".strip() or "FPL Manager"
                team_name = data.get("name", "").strip() or "FPL Squad"
                badge_url = data.get("club_badge_src")
                fav_team = data.get("favourite_team")
                if not badge_url and fav_team:
                    try:
                        teams_map = self.get_teams_map()
                        if fav_team in teams_map:
                            code = teams_map[fav_team].get("code")
                            if code:
                                badge_url = f"https://resources.premierleague.com/premierleague/badges/70/t{code}.png"
                    except Exception as te:
                        print(f"[FPLEngine] Error resolving team badge for {fav_team}: {te}")

                raw_bank = data.get("last_deadline_bank")
                bank_m = (raw_bank / 10.0) if raw_bank is not None else 0.0

                return {
                    "success": True,
                    "manager_id": manager_id,
                    "manager_name": manager_name,
                    "first_name": first,
                    "last_name": last,
                    "team_name": team_name,
                    "overall_points": data.get("summary_overall_points", 0),
                    "overall_rank": data.get("summary_overall_rank", 0),
                    "current_event": data.get("current_event"),
                    "started_event": data.get("started_event", 1),
                    "last_deadline_bank": raw_bank,
                    "last_deadline_value": data.get("last_deadline_value"),
                    "bank": bank_m,
                    "region": data.get("player_region_name", ""),
                    "favourite_team": fav_team,
                    "badge_url": badge_url
                }
        except Exception as e:
            print(f"[FPLEngine] Error fetching manager {manager_id} entry: {e}")
            return {
                "success": False,
                "manager_id": manager_id,
                "error": f"Could not fetch FPL entry for ID {manager_id}: {e}",
                "manager_name": f"Manager #{manager_id}",
                "team_name": f"Team #{manager_id}",
                "badge_url": None,
                "bank": 0.0,
                "current_event": None,
                "started_event": 1
            }

    def calculate_free_transfers(
        self,
        manager_id: int,
        history_data: Optional[Dict[str, Any]] = None,
        all_transfers: Optional[List[Dict[str, Any]]] = None,
        target_gw: Optional[int] = None,
        started_event: int = 1,
        active_upcoming_chip: Optional[str] = None,
        chips_by_gw: Optional[Dict[int, str]] = None
    ) -> Dict[str, Any]:
        """
        Accurately calculates available Free Transfers for a manager based on official FPL rules:
        - Free transfers start at 1 for the gameweek immediately following started_event.
        - Each gameweek, 1 free transfer is added to unused banked transfers, up to a maximum of 5 (2024/25+ rules).
        - Making transfers consumes available FTs; any excess transfers incur a -4 point hit.
        - Wildcard & Free Hit (2024/25+ rules): Banked transfers are NOT wiped and do not reset.
          Saved transfers are preserved and carried forward into the subsequent gameweek.
        - Any upcoming transfers already made for target_gw reduce available FTs (or incur hits if exceeding allocated FTs).
        """
        if target_gw is None:
            target_gw = self.get_current_gameweek()

        # If history_data is not passed, fetch it
        if not history_data:
            try:
                hist_url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/history/"
                req_hist = urllib.request.Request(hist_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req_hist, timeout=8) as h_resp:
                    history_data = json.loads(h_resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"[FPLEngine] Error fetching history for manager {manager_id}: {e}")
                history_data = {}

        # If all_transfers is not passed, fetch it
        if all_transfers is None:
            try:
                t_url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/transfers/"
                t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(t_req, timeout=8) as t_resp:
                    all_transfers = json.loads(t_resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"[FPLEngine] Error fetching transfers for manager {manager_id}: {e}")
                all_transfers = []

        current_list = history_data.get("current", [])
        chips_list = history_data.get("chips", [])
        chips_by_event: Dict[int, str] = {}
        for c in chips_list:
            ev = c.get("event")
            name = (c.get("name") or c.get("chip_name") or "").strip().lower()
            if ev and name:
                chips_by_event[int(ev)] = name

        # Merge external chips mapping (e.g. from picks) if provided
        if chips_by_gw:
            for g, c_name in chips_by_gw.items():
                if c_name and int(g) not in chips_by_event:
                    chips_by_event[int(g)] = str(c_name).strip().lower()

        events_by_id = {gw.get("event"): gw for gw in current_list if gw.get("event")}

        # Progression starts entering started_event + 1 with 1 FT
        ft = 1
        history_breakdown = []
        start_gw = max(1, started_event)

        for gw_num in range(start_gw + 1, target_gw):
            gw_data = events_by_id.get(gw_num)
            chip = chips_by_event.get(gw_num)

            if not gw_data:
                # Unplayed / missing gameweek - rolls +1 up to 5
                ft_next = min(5, ft + 1)
                history_breakdown.append({
                    "event": gw_num,
                    "ft_start": ft,
                    "transfers": 0,
                    "cost": 0,
                    "chip": chip,
                    "ft_remaining": ft,
                    "ft_next": ft_next
                })
                ft = ft_next
                continue

            transfers_made = int(gw_data.get("event_transfers", 0) or 0)
            transfers_cost = int(gw_data.get("event_transfers_cost", 0) or 0)
            if chip in ("wildcard", "freehit"):
                # Official FPL rule (2024/25 onwards):
                # Playing Wildcard or Free Hit no longer resets/wipes your accumulated Free Transfers!
                # Banked transfers are preserved entering the next GW (transfers made under WC/FH are free,
                # but no additional +1 is earned for the chip gameweek).
                ft_remaining = ft  # Banked FTs are preserved (not consumed)
                ft_next = ft       # Preserved for the following gameweek
            else:
                if transfers_cost > 0:
                    # Took hits, so all available FTs were consumed
                    ft_remaining = 0
                else:
                    ft_remaining = max(0, ft - transfers_made)
                ft_next = min(5, ft_remaining + 1)

            history_breakdown.append({
                "event": gw_num,
                "ft_start": ft,
                "transfers": transfers_made,
                "cost": transfers_cost,
                "chip": chip,
                "ft_remaining": ft_remaining,
                "ft_next": ft_next
            })
            ft = ft_next

        # Free transfers allocated entering target_gw (max 5)
        ft_allocated = max(1, min(5, ft))

        # Check upcoming transfers made for target_gw
        upcoming_transfers = [t for t in (all_transfers or []) if t.get("event") == target_gw]
        upcoming_count = len(upcoming_transfers)

        upcoming_chip_clean = (active_upcoming_chip or chips_by_event.get(target_gw) or "").strip().lower()

        if upcoming_chip_clean in ("wildcard", "freehit"):
            free_transfers_available = ft_allocated
            upcoming_cost = 0
        else:
            # If target_gw is a past/completed gameweek recorded in history and upcoming_transfers is empty:
            if target_gw in events_by_id and upcoming_count == 0:
                tg_data = events_by_id[target_gw]
                tg_transfers = int(tg_data.get("event_transfers", 0) or 0)
                tg_cost = int(tg_data.get("event_transfers_cost", 0) or 0)
                if tg_transfers > 0:
                    upcoming_count = tg_transfers
                    upcoming_cost = tg_cost
                    free_transfers_available = max(0, ft_allocated - upcoming_count)
                else:
                    free_transfers_available = ft_allocated
                    upcoming_cost = 0
            else:
                free_transfers_available = max(0, ft_allocated - upcoming_count)
                upcoming_cost = max(0, upcoming_count - ft_allocated) * 4

        return {
            "free_transfers": free_transfers_available,
            "free_transfers_allocated": ft_allocated,
            "transfers_made": upcoming_count,
            "transfer_cost": upcoming_cost,
            "history_breakdown": history_breakdown
        }

    def fetch_manager_lineup(self, manager_id: int, gameweek: Optional[int] = None, cookie: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetches the official active manager lineup matching the 'Pick Team' tab on the official FPL website.
        - If authenticated cookie is provided, queries the official private /api/my-team/{manager_id}/ endpoint directly.
        - Calls official team data for EACH gameweek across the season to monitor transitions.
        - Monitors changes from one gameweek to the next to isolate chips used.
        - Accounts for chip status: If a Free Hit was played in the previous gameweek, the squad automatically
          reverts to the pre-Free Hit squad (as per official FPL rules).
        - Applies post-deadline / upcoming transfers made for the upcoming gameweek to construct the true active lineup.
        """
        # 1. Attempt direct official 'Pick Team' API if cookie is provided
        if cookie:
            auth_res = self.fetch_my_team_authenticated(manager_id, cookie)
            if auth_res.get("success"):
                return auth_res

        target_gw = gameweek or self.get_current_gameweek()
        latest_completed_gw = self.get_latest_completed_gameweek()

        # 2. Fetch entry profile metadata
        entry = self.fetch_manager_entry(manager_id)
        if not entry.get("success"):
            return {
                "success": False,
                "error": f"Could not find squad for FPL manager ID {manager_id}. Please verify the ID number."
            }

        start_gw = entry.get("started_event") or 1

        # 3. Fetch history for used chips
        history_data: Dict[str, Any] = {}
        try:
            hist_url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/history/"
            req_hist = urllib.request.Request(hist_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_hist, timeout=8) as h_resp:
                history_data = json.loads(h_resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"[FPLEngine] History fetch error for manager {manager_id}: {e}")

        # 4. Fetch all transfers made across the season
        all_transfers: List[Dict[str, Any]] = []
        try:
            t_url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/transfers/"
            t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(t_req, timeout=8) as t_resp:
                all_transfers = json.loads(t_resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"[FPLEngine] Transfers fetch error for manager {manager_id}: {e}")

        # Build chips map by event
        chips_list = history_data.get("chips", []) if history_data else []
        chip_by_gw: Dict[int, str] = {c.get("event"): c.get("name") for c in chips_list if c.get("event")}

        # 5. Call official team data for EACH gameweek
        picks_by_gw: Dict[int, Dict[str, Any]] = {}
        last_error = None

        # Query all completed gameweeks from start_gw up to latest_completed_gw
        for gw in range(start_gw, max(latest_completed_gw, start_gw) + 1):
            url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/event/{gw}/picks/"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    p_data = json.loads(resp.read().decode("utf-8"))
                    picks_by_gw[gw] = p_data
                    if p_data.get("active_chip") and gw not in chip_by_gw:
                        chip_by_gw[gw] = p_data.get("active_chip")
            except Exception as e:
                last_error = e

        # Also attempt target upcoming gameweek in case its picks are already published
        if target_gw and target_gw not in picks_by_gw and target_gw > latest_completed_gw:
            try:
                url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/event/{target_gw}/picks/"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    p_data = json.loads(resp.read().decode("utf-8"))
                    picks_by_gw[target_gw] = p_data
                    if p_data.get("active_chip") and target_gw not in chip_by_gw:
                        chip_by_gw[target_gw] = p_data.get("active_chip")
            except Exception:
                pass

        if not picks_by_gw:
            return {
                "success": False,
                "error": f"Could not find squad picks for FPL manager ID {manager_id}. Please verify the ID number (Error: {last_error})"
            }

        # Check for transfers already made for upcoming target gameweek
        upcoming_transfers = [t for t in all_transfers if t.get("event") == target_gw]

        # 7. Isolate the base squad (Handling Free Hit reversion)
        # If target_gw picks are already published and active, use them directly
        if target_gw in picks_by_gw:
            base_gw = target_gw
            was_reverted = False
        else:
            # Reconstruct active squad from latest completed gameweeks
            base_gw = latest_completed_gw if latest_completed_gw in picks_by_gw else max(picks_by_gw.keys())
            was_reverted = False

            # If Free Hit was played in the latest completed gameweek, the lineup for target_gw
            # MUST revert back to the pre-Free Hit lineup (as per official FPL rules)
            if chip_by_gw.get(base_gw) == "freehit" or picks_by_gw.get(base_gw, {}).get("active_chip") == "freehit":
                logger.info(f"[FPLEngine] Free Hit detected in GW {base_gw} for manager {manager_id}. Reverting to pre-Free Hit squad...")
                for g in range(base_gw - 1, start_gw - 1, -1):
                    if g in picks_by_gw and chip_by_gw.get(g) != "freehit" and picks_by_gw[g].get("active_chip") != "freehit":
                        base_gw = g
                        was_reverted = True
                        break

        base_picks_obj = picks_by_gw.get(base_gw, {})
        raw_picks = base_picks_obj.get("picks", [])
        if not raw_picks and picks_by_gw:
            fallback_gw = max(picks_by_gw.keys())
            base_picks_obj = picks_by_gw[fallback_gw]
            raw_picks = base_picks_obj.get("picks", [])
            base_gw = fallback_gw

        active_picks = [dict(p) for p in raw_picks]

        # 8. Calculate accurate Bank balance
        entry_hist = base_picks_obj.get("entry_history", {})
        if entry_hist.get("bank") is not None:
            bank = entry_hist["bank"] / 10.0
        elif entry.get("last_deadline_bank") is not None:
            bank = entry["last_deadline_bank"] / 10.0
        else:
            bank = 0.0

        # 9. Apply upcoming / post-deadline transfers to active lineup
        for t in sorted(upcoming_transfers, key=lambda x: x.get("time", "")):
            out_id = t.get("element_out")
            in_id = t.get("element_in")
            out_cost = (t.get("element_out_cost") or 0) / 10.0
            in_cost = (t.get("element_in_cost") or 0) / 10.0

            for p in active_picks:
                if p.get("element") == out_id:
                    p["element"] = in_id
                    break

            bank += (out_cost - in_cost)

        # 10. Extract sorted starters, bench, captain, and vice-captain
        sorted_picks = sorted(active_picks, key=lambda p: p.get("position", 0))
        starters = [p["element"] for p in sorted_picks if p.get("position", 1) <= 11]
        bench = [p["element"] for p in sorted_picks if p.get("position", 1) > 11]

        captain = next((p["element"] for p in sorted_picks if p.get("is_captain")), starters[0] if starters else None)
        vc = next((p["element"] for p in sorted_picks if p.get("is_vice_captain")), starters[1] if len(starters) > 1 else None)

        # Collate all used chips
        used_chips: List[Dict[str, Any]] = []
        seen_chips = set()
        for g, c_name in sorted(chip_by_gw.items()):
            if c_name and c_name not in seen_chips:
                seen_chips.add(c_name)
                used_chips.append({
                    "name": c_name,
                    "status_for_entry": "played",
                    "event": g
                })

        logger.info(
            f"[FPLEngine] Successfully loaded manager {manager_id} active squad. "
            f"Base GW: {base_gw} (Reverted from Free Hit: {was_reverted}), "
            f"Upcoming transfers applied: {len(upcoming_transfers)}"
        )

        # Calculate accurate Free Transfers from gameweek history
        ft_calc = self.calculate_free_transfers(
            manager_id=manager_id,
            history_data=history_data,
            all_transfers=all_transfers,
            target_gw=target_gw,
            started_event=start_gw,
            active_upcoming_chip=chip_by_gw.get(target_gw),
            chips_by_gw=chip_by_gw
        )

        return {
            "success": True,
            "manager_id": manager_id,
            "team_name": entry.get("team_name"),
            "manager_name": entry.get("manager_name"),
            "badge_url": entry.get("badge_url"),
            "overall_points": entry.get("overall_points", 0),
            "overall_rank": entry.get("overall_rank", 0),
            "target_gameweek": target_gw,
            "loaded_from_gameweek": base_gw,
            "source": "official-fpl-pick-team-active",
            "was_reverted_from_freehit": was_reverted,
            "upcoming_transfers_applied": len(upcoming_transfers),
            "free_transfers": ft_calc["free_transfers"],
            "free_transfers_allocated": ft_calc["free_transfers_allocated"],
            "transfers_made": ft_calc["transfers_made"],
            "transfer_cost": ft_calc["transfer_cost"],
            "ft_history_breakdown": ft_calc["history_breakdown"],
            "picks": sorted_picks,
            "starters": starters,
            "bench": bench,
            "captain": captain,
            "vice_captain": vc,
            "bank": round(bank, 2),
            "chips": used_chips,
            "chips_by_gameweek": chip_by_gw,
            "applied_upcoming_transfers": upcoming_transfers,
            "entry": entry
        }

    def fetch_my_team_authenticated(self, manager_id: int, cookie: str) -> Dict[str, Any]:
        """
        Directly queries the private authenticated 'my-team' API endpoint used on
        the official 'Pick Team' page (https://fantasy.premierleague.com/my-team).
        Retrieves live pre-deadline transfers, captain, vice-captain, active chips, and saved squad directly.
        """
        clean_cookie = (cookie or "").strip()
        if not clean_cookie:
            return {
                "success": False,
                "error": "No FPL session cookie provided. Please provide your 'pl_profile' cookie.",
                "auth_error": True,
                "status_code": 401
            }

        # Format cookie header properly whether user passed raw token, pl_profile=..., or full Cookie string
        if "pl_profile=" in clean_cookie:
            cookie_hdr = clean_cookie
        else:
            cookie_hdr = f"pl_profile={clean_cookie}"

        url = f"https://fantasy.premierleague.com/api/my-team/{manager_id}/"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://fantasy.premierleague.com/my-team",
            "Cookie": cookie_hdr
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                target_gw = self.get_current_gameweek()

                picks = data.get("picks", [])
                sorted_picks = sorted(picks, key=lambda p: p.get("position", 0))
                starters = [p["element"] for p in sorted_picks if p.get("position", 1) <= 11]
                bench = [p["element"] for p in sorted_picks if p.get("position", 1) > 11]

                captain = next((p["element"] for p in sorted_picks if p.get("is_captain")), starters[0] if starters else None)
                vc = next((p["element"] for p in sorted_picks if p.get("is_vice_captain")), starters[1] if len(starters) > 1 else None)

                # Pricing map from authenticated picks
                element_pricing = {}
                for p in picks:
                    eid = p.get("element")
                    if eid:
                        element_pricing[eid] = {
                            "selling_price": round((p.get("selling_price", 0) / 10.0), 1),
                            "purchase_price": round((p.get("purchase_price", 0) / 10.0), 1)
                        }

                transfers = data.get("transfers", {})
                bank = (transfers.get("bank", 0) / 10.0) if transfers.get("bank") is not None else 0.0

                limit = transfers.get("limit")
                made = transfers.get("made", 0)
                transfer_cost = transfers.get("cost", 0)

                raw_chips = data.get("chips", [])
                used_chips: List[Dict[str, Any]] = []
                active_upcoming_chip = None
                for c in raw_chips:
                    status = c.get("status_for_entry")
                    if status == "active":
                        active_upcoming_chip = c.get("name")
                    is_played = status == "played" or bool(c.get("played_by_entry"))
                    if is_played:
                        used_chips.append({
                            "name": c.get("name"),
                            "status_for_entry": "played",
                            "event": c.get("played_by_entry", [None])[0] if c.get("played_by_entry") else None
                        })

                entry = self.fetch_manager_entry(manager_id)

                logger.info(f"[FPLEngine] Successfully synced manager {manager_id} live 'Pick Team' via authenticated API. Starters: {len(starters)}, Bench: {len(bench)}, Bank: £{bank}m, Active Chip: {active_upcoming_chip}")

                # Calculate Free Transfers from history
                ft_calc = self.calculate_free_transfers(
                    manager_id=manager_id,
                    target_gw=target_gw,
                    started_event=entry.get("started_event") or 1,
                    active_upcoming_chip=active_upcoming_chip
                )
                auth_limit = transfers.get("limit")
                if auth_limit is not None:
                    final_ft = max(0, int(auth_limit) - int(made))
                    final_alloc = int(auth_limit)
                else:
                    final_ft = ft_calc["free_transfers"]
                    final_alloc = ft_calc["free_transfers_allocated"]

                return {
                    "success": True,
                    "authenticated": True,
                    "manager_id": manager_id,
                    "team_name": entry.get("team_name") or f"Team #{manager_id}",
                    "manager_name": entry.get("manager_name") or f"Manager #{manager_id}",
                    "badge_url": entry.get("badge_url"),
                    "overall_points": entry.get("overall_points", 0),
                    "overall_rank": entry.get("overall_rank", 0),
                    "target_gameweek": target_gw,
                    "loaded_from_gameweek": target_gw,
                    "source": "my-team-authenticated",
                    "free_transfers": final_ft,
                    "free_transfers_allocated": final_alloc,
                    "transfers_made": made,
                    "transfer_cost": transfer_cost,
                    "ft_history_breakdown": ft_calc["history_breakdown"],
                    "picks": sorted_picks,
                    "starters": starters,
                    "bench": bench,
                    "captain": captain,
                    "vice_captain": vc,
                    "transfers": transfers,
                    "bank": round(bank, 2),
                    "active_upcoming_chip": active_upcoming_chip,
                    "chips": used_chips,
                    "element_pricing": element_pricing
                }
        except urllib.error.HTTPError as he:
            logger.warning(f"[FPLEngine] HTTPError {he.code} on my-team for manager {manager_id}: {he.reason}")
            if he.code in (401, 403):
                return {
                    "success": False,
                    "authenticated": False,
                    "auth_error": True,
                    "status_code": he.code,
                    "error": f"Official FPL session cookie ('pl_profile') was rejected ({he.code} {he.reason}). Please log into fantasy.premierleague.com, refresh the 'Pick Team' tab, and copy your latest cookie."
                }
            elif he.code == 404:
                return {
                    "success": False,
                    "authenticated": False,
                    "auth_error": True,
                    "status_code": 404,
                    "error": f"FPL Manager ID #{manager_id} not found under this account. Please ensure the Manager ID matches the logged-in team."
                }
            else:
                return {
                    "success": False,
                    "authenticated": False,
                    "auth_error": False,
                    "status_code": he.code,
                    "error": f"Error contacting official FPL 'my-team' API: {he.code} {he.reason}"
                }
        except Exception as e:
            logger.error(f"[FPLEngine] Unexpected error on authenticated my-team for manager {manager_id}: {e}")
            return {
                "success": False,
                "authenticated": False,
                "auth_error": False,
                "error": f"Could not retrieve authenticated 'my-team': {e}"
            }

    def fetch_my_team_and_transfers(self, manager_id: int, cookie: Optional[str] = None, require_auth: bool = False) -> Dict[str, Any]:
        """
        Retrieves active squads from the official FPL website.
        If authenticated cookie is provided, queries the private 'my-team' API endpoint directly.
        If require_auth is True and cookie fails, returns the auth error rather than silent fallback.
        Otherwise falls back to multi-gameweek reconstruction and transfers tracking.
        """
        clean_cookie = (cookie or "").strip()
        target_gw = self.get_current_gameweek()

        # 1. If cookie provided, attempt live authenticated API call
        if clean_cookie:
            auth_res = self.fetch_my_team_authenticated(manager_id, clean_cookie)
            if auth_res.get("success"):
                return auth_res
            elif require_auth:
                return auth_res
            else:
                logger.warning(f"[FPLEngine] Authenticated call failed, falling back to public reconstruction: {auth_res.get('error')}")

        # 2. If dedicated official my-team export file exists for this manager, load it directly
        my_team_path = os.path.join(CACHE_DIR, f"my_team_{manager_id}.json")
        if os.path.exists(my_team_path):
            try:
                with open(my_team_path, "r", encoding="utf-8") as f:
                    mt_data = json.load(f)
                    entry = self.fetch_manager_entry(manager_id)
                    picks = mt_data.get("picks", [])
                    sorted_picks = sorted(picks, key=lambda p: p.get("position", 0))
                    starters = [p["element"] for p in sorted_picks if p.get("position", 1) <= 11]
                    bench = [p["element"] for p in sorted_picks if p.get("position", 1) > 11]
                    captain = next((p["element"] for p in sorted_picks if p.get("is_captain")), starters[0] if starters else None)
                    vc = next((p["element"] for p in sorted_picks if p.get("is_vice_captain")), starters[1] if len(starters) > 1 else None)
                    ft_calc = self.calculate_free_transfers(manager_id, target_gw=target_gw, started_event=entry.get("started_event") or 1)
                    return {
                        "success": True,
                        "manager_id": manager_id,
                        "team_name": entry.get("team_name") or mt_data.get("team_name"),
                        "manager_name": entry.get("manager_name") or mt_data.get("manager_name"),
                        "badge_url": entry.get("badge_url"),
                        "overall_points": entry.get("overall_points", 0),
                        "overall_rank": entry.get("overall_rank", 0),
                        "target_gameweek": target_gw,
                        "loaded_from_gameweek": target_gw,
                        "source": "https://fantasy.premierleague.com/en/my-team",
                        "free_transfers": ft_calc["free_transfers"],
                        "free_transfers_allocated": ft_calc["free_transfers_allocated"],
                        "transfers_made": ft_calc["transfers_made"],
                        "transfer_cost": ft_calc["transfer_cost"],
                        "ft_history_breakdown": ft_calc["history_breakdown"],
                        "picks": sorted_picks,
                        "starters": starters,
                        "bench": bench,
                        "captain": captain,
                        "vice_captain": vc,
                        "bank": mt_data.get("bank", 1.2),
                        "chips": mt_data.get("chips", [])
                    }
            except Exception as me:
                print(f"[FPLEngine] Error reading my_team cache: {me}")

        # 3. Fetch live public entry lineup for target gameweek with multi-gameweek change monitoring
        lineup_res = self.fetch_manager_lineup(manager_id, gameweek=target_gw, cookie=cookie)
        return lineup_res

if __name__ == "__main__":
    engine = FPLEngine()
    bs = engine.fetch_bootstrap_static()
    print("FPL Total elements:", len(bs.get("elements", [])))
    print("Upcoming target GW:", engine.get_current_gameweek())
    print("Latest completed GW:", engine.get_latest_completed_gameweek())
    res = engine.fetch_manager_lineup(1)
    print("Manager 1 load result:", res.get("success"), "Loaded from GW:", res.get("loaded_from_gameweek"))
