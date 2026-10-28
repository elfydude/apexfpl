"""
Automated Gameweek Update Pipeline & Daemon Scheduler
Monitors the official Fantasy Premier League (FPL) fixture and event state.
When a Gameweek concludes (all fixtures completed & bonus points/data finalized),
this pipeline automatically:
1. Ingests fresh official FPL bootstrap static data and upcoming fixture calendars.
2. Performs a comprehensive FotMob scrape across all completed match details, shotmaps, and player metrics.
3. Updates player multi-season history and career baselines.
4. Recalculates Bayesian credibility blending, Poisson clean sheet/goals conceded deductions, and xP models.
5. Emits real-time state logs and updates the live server in-memory catalog without downtime.
"""

import os
import sys
import json
import time
import asyncio
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Setup paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATUS_FILE = os.path.join(DATA_DIR, "gameweek_pipeline_status.json")

from fpl_engine import FPLEngine
from fotmob_engine import FotMobEngine

DEFAULT_CHECK_INTERVAL_SECONDS = 300  # Check every 5 minutes

class GameweekPipeline:
    def __init__(self, recommender_engine=None, check_interval: int = DEFAULT_CHECK_INTERVAL_SECONDS):
        self.engine = recommender_engine
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self.is_running = False
        self.scheduler_active = False
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

        # Load persisted status
        self.status_data = self._load_persisted_status()

    def _load_persisted_status(self) -> Dict[str, Any]:
        """Loads or initializes pipeline audit status."""
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[GameweekPipeline] Status file read error: {e}")

        # Default initial status
        initial = {
            "enabled": True,
            "is_running": False,
            "last_run_timestamp": None,
            "last_run_status": "NEVER_RUN",
            "last_completed_gw": 3,
            "target_next_gw": 4,
            "check_interval_seconds": self.check_interval,
            "last_check_timestamp": None,
            "total_runs_count": 0,
            "recent_runs": [],
            "logs": []
        }
        self._save_persisted_status(initial)
        return initial

    def _save_persisted_status(self, data: Optional[Dict[str, Any]] = None):
        """Persists pipeline status to disk."""
        to_save = data or self.status_data
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2)
        except Exception as e:
            print(f"[GameweekPipeline] Status file write error: {e}")

    def log(self, message: str):
        """Timestamped audit logger."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        formatted = f"[{now_str}] {message}"
        print(f"[GameweekPipeline] {formatted}")
        
        logs = self.status_data.setdefault("logs", [])
        logs.append(formatted)
        if len(logs) > 100:
            self.status_data["logs"] = logs[-100:]
        self._save_persisted_status()

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time pipeline status."""
        next_check = None
        last_chk = self.status_data.get("last_check_timestamp")
        if last_chk and self.scheduler_active:
            try:
                last_dt = datetime.fromisoformat(last_chk)
                next_check = datetime.fromtimestamp(last_dt.timestamp() + self.check_interval, timezone.utc).isoformat()
            except Exception:
                pass

        return {
            "enabled": self.status_data.get("enabled", True),
            "scheduler_active": self.scheduler_active,
            "is_running": self.is_running,
            "last_run_timestamp": self.status_data.get("last_run_timestamp"),
            "last_run_status": self.status_data.get("last_run_status"),
            "last_completed_gw": self.status_data.get("last_completed_gw"),
            "target_next_gw": self.status_data.get("target_next_gw"),
            "check_interval_seconds": self.check_interval,
            "last_check_timestamp": self.status_data.get("last_check_timestamp"),
            "next_check_timestamp": next_check,
            "total_runs_count": self.status_data.get("total_runs_count", 0),
            "recent_runs": self.status_data.get("recent_runs", [])[-10:],
            "logs": self.status_data.get("logs", [])[-20:]
        }

    def check_for_completed_gameweek(self) -> Optional[int]:
        """
        Polls official FPL API to check if a new Gameweek has completed.
        Returns the new completed Gameweek ID if an update is due, else None.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self.status_data["last_check_timestamp"] = now_iso
        self._save_persisted_status()

        try:
            fpl = self.engine.fpl if self.engine else FPLEngine()
            bootstrap = fpl.fetch_bootstrap_static(force_refresh=True)
            events = bootstrap.get("events", [])
            
            # Find the highest finished and data_checked event
            finished_gws = [
                ev["id"] for ev in events
                if ev.get("finished") and ev.get("data_checked")
            ]
            
            if not finished_gws:
                # Fallback: check if finished flag is true even if data_checked is pending
                finished_gws = [ev["id"] for ev in events if ev.get("finished")]

            if not finished_gws:
                return None

            latest_finished = max(finished_gws)
            last_recorded = self.status_data.get("last_completed_gw", 0)

            if latest_finished > last_recorded:
                self.log(f"NEW GAMEWEEK COMPLETION DETECTED: Gameweek {latest_finished} has ended (Previously recorded: GW {last_recorded}).")
                return latest_finished

            return None
        except Exception as e:
            self.log(f"Error checking gameweek completion: {e}")
            return None

    def run_pipeline(self, force: bool = False, trigger_source: str = "manual") -> Dict[str, Any]:
        """
        Executes the end-to-end Gameweek Data Renewal Pipeline:
        1. FPL Bootstrap & Upcoming Fixture Ingestion
        2. FotMob Comprehensive Scrape (Stats, Match Details, Shotmaps, Injuries)
        3. Bayesian Blending & xP Model Recalculation
        4. In-Memory Catalog Live Update
        """
        if self.is_running:
            return {
                "success": False,
                "message": "Pipeline execution already in progress.",
                "status": "ALREADY_RUNNING"
            }

        with self._lock:
            self.is_running = True
            self.status_data["is_running"] = True
            self._save_persisted_status()

        t_start = time.time()
        start_time_iso = datetime.now(timezone.utc).isoformat()
        self.log(f"=== PIPELINE STARTED (Trigger: {trigger_source}) ===")

        try:
            # Lazy initialize engine if needed
            if not self.engine:
                from recommender_engine import RecommenderEngine
                self.engine = RecommenderEngine()

            # -------------------------------------------------------------
            # STEP 1: Ingest Fresh Official FPL Bootstrap & Fixtures
            # -------------------------------------------------------------
            self.log("Step 1/4: Ingesting official Premier League bootstrap-static & fixtures...")
            fpl_data = self.engine.fpl.fetch_bootstrap_static(force_refresh=True)
            fpl_fixes = self.engine.fpl.fetch_upcoming_fixtures(force_refresh=True)
            gw_info = self.engine.fpl.get_gameweek_info()
            self.log(f"Step 1 Complete: FPL data synchronized. Upcoming Target: GW {gw_info.get('target_gameweek')}, Completed Base: GW {gw_info.get('latest_completed_gameweek')}.")

            # -------------------------------------------------------------
            # STEP 2: Harvest Fresh FotMob Stats, Completed Matches & Shotmaps
            # -------------------------------------------------------------
            self.log("Step 2/4: Harvesting FotMob match details, shotmaps, and 43 stat categories...")
            matches_scanned = 0
            total_shots = 0
            total_injuries = 0
            try:
                fm_engine = FotMobEngine()
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    fm_data = loop.run_until_complete(fm_engine.scrape_all_stats(force_refresh=True))
                finally:
                    loop.run_until_complete(fm_engine.close())
                    loop.close()

                matches_scanned = len(fm_data.get("match_details", {}))
                total_shots = fm_data.get("total_shots_recorded", 0)
                total_injuries = len(fm_data.get("injuries", []))
                self.log(f"Step 2 Complete: FotMob harvested {len(fm_data.get('players', {}))} players, {matches_scanned} matches, {total_shots} shots, {total_injuries} injuries.")
            except Exception as fme:
                self.log(f"Step 2 Notice: FotMob harvest notice ({fme}). Continuing with cached FotMob stats.")

            # -------------------------------------------------------------
            # STEP 3: Unify Data & Recalculate Bayesian Holistic xP Models
            # -------------------------------------------------------------
            self.log("Step 3/4: Unifying catalog & recalculating Bayesian credibility xP models...")
            self.engine.load_and_unify_data(force_refresh=False)
            total_unified = len(self.engine.unified_players)
            self.log(f"Step 3 Complete: Unified {total_unified} players with renewed stats and xP rankings.")

            # -------------------------------------------------------------
            # STEP 4: Persist Pipeline Audit Run Record
            # -------------------------------------------------------------
            duration = round(time.time() - t_start, 1)
            target_gw = gw_info.get("target_gameweek", 4)
            completed_gw = gw_info.get("latest_completed_gameweek", 3)

            run_summary = {
                "timestamp": start_time_iso,
                "duration_seconds": duration,
                "trigger_source": trigger_source,
                "completed_gameweek": completed_gw,
                "target_gameweek": target_gw,
                "players_count": total_unified,
                "matches_scanned": matches_scanned,
                "shots_recorded": total_shots,
                "injuries_tracked": total_injuries,
                "status": "SUCCESS"
            }

            self.status_data["last_run_timestamp"] = start_time_iso
            self.status_data["last_run_status"] = "SUCCESS"
            self.status_data["last_completed_gw"] = completed_gw
            self.status_data["target_next_gw"] = target_gw
            self.status_data["total_runs_count"] = self.status_data.get("total_runs_count", 0) + 1
            self.status_data.setdefault("recent_runs", []).append(run_summary)
            if len(self.status_data["recent_runs"]) > 20:
                self.status_data["recent_runs"] = self.status_data["recent_runs"][-20:]

            self.log(f"=== PIPELINE COMPLETED SUCCESSFULLY in {duration}s ===")
            return {
                "success": True,
                "duration_seconds": duration,
                "gameweek_info": gw_info,
                "run_summary": run_summary
            }

        except Exception as e:
            duration = round(time.time() - t_start, 1)
            self.log(f"PIPELINE FAILED with error: {e}")
            self.status_data["last_run_status"] = "FAILED"
            self.status_data["last_run_error"] = str(e)
            return {
                "success": False,
                "error": str(e),
                "duration_seconds": duration
            }
        finally:
            with self._lock:
                self.is_running = False
                self.status_data["is_running"] = False
                self._save_persisted_status()

    def _scheduler_worker(self):
        """Background thread worker that checks for Gameweek completion."""
        self.log("Auto-Update Scheduler Worker started.")
        while not self._stop_event.is_set():
            try:
                if self.status_data.get("enabled", True):
                    completed_gw = self.check_for_completed_gameweek()
                    if completed_gw is not None:
                        self.log(f"Auto-triggering end-of-gameweek pipeline for Gameweek {completed_gw}...")
                        self.run_pipeline(trigger_source=f"auto_end_of_gw_{completed_gw}")
            except Exception as e:
                self.log(f"Error in scheduler worker loop: {e}")

            # Sleep in short increments to support quick shutdown
            for _ in range(self.check_interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self.log("Auto-Update Scheduler Worker stopped.")

    def start_scheduler(self):
        """Starts the background auto-update monitor thread."""
        if self.scheduler_active:
            return
        self.scheduler_active = True
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_worker, daemon=True, name="GameweekPipelineScheduler")
        self._scheduler_thread.start()
        self.log(f"Auto-Update Scheduler activated (Checking every {self.check_interval}s).")

    def stop_scheduler(self):
        """Stops the background auto-update monitor thread."""
        if not self.scheduler_active:
            return
        self.scheduler_active = False
        self._stop_event.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=3)
            self._scheduler_thread = None
        self.log("Auto-Update Scheduler deactivated.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gameweek Auto-Update Pipeline CLI")
    parser.add_argument("--run", action="store_true", help="Execute the complete pipeline immediately")
    parser.add_argument("--check", action="store_true", help="Check if a new completed Gameweek is ready to ingest")
    parser.add_argument("--status", action="store_true", help="Display current pipeline status")
    parser.add_argument("--daemon", action="store_true", help="Run the continuous monitoring scheduler daemon")
    args = parser.parse_args()

    pipeline = GameweekPipeline()

    if args.run:
        print("Starting Gameweek Pipeline execution...")
        result = pipeline.run_pipeline(force=True, trigger_source="cli_manual")
        print("Result:", json.dumps(result, indent=2))
    elif args.check:
        new_gw = pipeline.check_for_completed_gameweek()
        print("Check result - New completed GW:", new_gw)
    elif args.status:
        print(json.dumps(pipeline.get_status(), indent=2))
    elif args.daemon:
        print("Starting continuous daemon scheduler. Press Ctrl+C to stop.")
        pipeline.start_scheduler()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping scheduler...")
            pipeline.stop_scheduler()
    else:
        parser.print_help()
