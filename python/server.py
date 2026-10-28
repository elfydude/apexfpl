"""
REST API Backend Server for FPL & FotMob Transfers Recommender.
Serves static frontend assets and provides endpoints for squad management,
player statistics deep-dives, live recommendations, substitutions validation,
and FPL Manager ID imports with automatic Gameweek fallback.
"""

import os
import sys
import json
import mimetypes
import logging
from typing import Any, Dict, List, Optional
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from recommender_engine import RecommenderEngine
from chat_engine import ChatFPLEngine
from auth_engine import AuthEngine
from fpl_engine import extract_manager_id
from gameweek_pipeline import GameweekPipeline
from fotmob_details import fotmob_details

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ApexServer")

# Ensure line-buffering for standard output logging
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# Ensure python module directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = int(os.environ.get("PORT", 5050))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))) else os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "static")

# Load environment variables from root .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, ".env"))
except ImportError:
    pass

engine = RecommenderEngine()
chat_engine = ChatFPLEngine(engine)
auth_engine = AuthEngine(engine.fpl)
pipeline = GameweekPipeline(engine, check_interval=300)
pipeline.start_scheduler()

class FPLRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_GET(self, head_only: bool = False):
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            # API: Current Gameweek status (Auto-updated from live FPL)
            if path == "/api/gameweek":
                gw_info = engine.fpl.get_gameweek_info()
                self.send_json_response(gw_info)
                return

            # API: Complete Data Update Pipeline After Every Gameweek
            elif path in ["/api/gameweek/update", "/api/gameweek/refresh"]:
                res = pipeline.run_pipeline(force=True, trigger_source="api_get_request")
                self.send_json_response(res)
                return

            # API: Automated Gameweek Pipeline Status & Audit Log
            elif path == "/api/pipeline/status":
                self.send_json_response(pipeline.get_status())
                return

            # API: Automated Gameweek Auto-Sync (Page Load & Squad Import)
            elif path == "/api/pipeline/auto-sync":
                new_gw = pipeline.check_for_completed_gameweek()
                if new_gw:
                    pipeline.log(f"Auto-Sync: Detected completed Gameweek {new_gw}. Triggering automated pipeline...")
                    res = pipeline.run_pipeline(force=True, trigger_source="client_auto_sync")
                    self.send_json_response({
                        "synced": True,
                        "updated": True,
                        "new_gameweek": new_gw,
                        "gameweek_info": engine.fpl.get_gameweek_info(),
                        "result": res
                    })
                else:
                    self.send_json_response({
                        "synced": True,
                        "updated": False,
                        "is_running": pipeline.is_running,
                        "last_completed_gw": pipeline.status_data.get("last_completed_gw", 3),
                        "target_next_gw": pipeline.status_data.get("target_next_gw", 4),
                        "gameweek_info": engine.fpl.get_gameweek_info()
                    })
                return

            # API: Force Refresh Live Fixtures & Gameweek
            elif path == "/api/fixtures/refresh":
                res = engine.update_gameweek_data(force_refresh=True)
                gw_info = engine.fpl.get_gameweek_info()
                self.send_json_response({
                    "success": True,
                    "message": f"Complete data updated for {gw_info.get('gameweek_name', 'Gameweek')}",
                    "gameweek_info": gw_info
                })
                return

            # API: All Unified Players
            elif path == "/api/players":
                query_params = parse_qs(parsed.query)
                full = query_params.get("full", ["false"])[0].lower() == "true"
                if full:
                    players = [p for p in engine.unified_players if not p.get("is_transferred_out") and p.get("status") not in ['u', 'n']]
                else:
                    players = []
                    for p in engine.unified_players:
                        if p.get("is_transferred_out") or p.get("status") in ['u', 'n']:
                            continue
                        d = {k: v for k, v in p.items() if k not in ("shotmap", "all_fotmob_stats", "historical_past")}
                        d["upcoming_fixtures"] = (p.get("upcoming_fixtures") or [])[:5]
                        players.append(d)
                self.send_json_response({
                    "total": len(players),
                    "players": players
                })
                return

            # API: Default Template Squad
            elif path == "/api/squad/default":
                squad = engine.get_default_squad()
                p_ids = squad.get("starters", []) + squad.get("bench", [])
                squad_players = []
                for pid in p_ids:
                    p = engine.players_by_id.get(pid)
                    if p:
                        p_copy = {k: v for k, v in p.items() if k not in ("shotmap", "all_fotmob_stats", "historical_past")}
                        p_copy["upcoming_fixtures"] = (p.get("upcoming_fixtures") or [])[:5]
                        squad_players.append(p_copy)
                squad["players"] = squad_players
                squad["chips"] = []
                squad["free_transfers"] = squad.get("free_transfers", 1)
                squad["free_transfers_allocated"] = squad.get("free_transfers_allocated", 1)
                cap_player = engine.players_by_id.get(squad.get("captain"))
                vc_player = engine.players_by_id.get(squad.get("vice_captain"))
                squad["captain_name"] = cap_player.get("web_name", "") if cap_player else ""
                squad["vice_captain_name"] = vc_player.get("web_name", "") if vc_player else ""
                self.send_json_response(squad)
                return

            # API: Player Details (Shotmap, genuine FotMob heatmap coordinates, FDR, Multi-season history)
            elif path.startswith("/api/player/"):
                try:
                    p_id = int(path.split("/")[-1])
                    player = engine.players_by_id.get(p_id)
                    if player:
                        player_copy = dict(player)
                        fotmob_id = player.get("fotmob_id")
                        if fotmob_id:
                            details = fotmob_details.get_details(fotmob_id)
                            player_copy["heatmap"] = details.get("heatmap") or []
                            if player.get("position") == "GKP":
                                player_copy["shotmap"] = details.get("keeper_shotmap") or details.get("shotmap") or player.get("shotmap", [])
                            else:
                                player_copy["shotmap"] = details.get("shotmap") or player.get("shotmap", [])
                        else:
                            player_copy["heatmap"] = []
                            player_copy["shotmap"] = player.get("shotmap", [])
                        self.send_json_response({"success": True, "player": player_copy})
                    else:
                        self.send_json_response({"success": False, "error": "Player not found"}, 404)
                except Exception as e:
                    self.send_json_response({"success": False, "error": str(e)}, 400)
                return

            # API: Injury and Doubts Board (with Recent Injury Returnees)
            elif path == "/api/injuries":
                inj_fields = (
                    "id", "web_name", "full_name", "team_name", "team_short", "cost", "position",
                    "injury_reason", "is_unavailable", "is_doubtful", "is_unlikely_to_play",
                    "is_recent_injury_returnee", "injury_return_ramp", "expected_minutes_projected",
                    "normalized_form", "chance_of_playing", "status_tags", "normalization_notes"
                )
                injuries = []
                for p in engine.unified_players:
                    # STRICT USER REQUIREMENT: Transferred out players must NOT appear in doubt/injury monitor
                    if p.get("is_transferred_out") or p.get("status") in ['u', 'n']:
                        continue
                    if p.get("is_unavailable") or p.get("is_doubtful") or p.get("is_unlikely_to_play") or p.get("is_recent_injury_returnee"):
                        injuries.append({k: p.get(k) for k in inj_fields})
                injuries.sort(key=lambda x: (
                    not x.get("is_unavailable"),
                    not x.get("is_doubtful"),
                    x.get("is_recent_injury_returnee", False),
                    -x.get("cost", 0)
                ))
                self.send_json_response({
                    "count": len(injuries),
                    "injuries": injuries
                })
                return

            # API: Stats Normalization & Data Pull for New Entrants and Recently Injured
            elif path == "/api/stats-normalization":
                from stats_normalization import stats_normalizer
                new_entrants = [p for p in engine.unified_players if p.get("is_new_pl_entrant") and not p.get("is_transferred_out")]
                recently_injured = [p for p in engine.unified_players if (p.get("is_recently_injured") or p.get("is_recent_injury_returnee")) and not p.get("is_transferred_out")]
                returnees = [p for p in engine.unified_players if p.get("is_recent_injury_returnee") and not p.get("is_transferred_out")]

                new_entrants.sort(key=lambda x: (-x.get("cost", 0), -x.get("expected_points", 0)))
                recently_injured.sort(key=lambda x: (-x.get("cost", 0), -x.get("expected_points", 0)))
                returnees.sort(key=lambda x: (-x.get("cost", 0), -x.get("expected_points", 0)))

                summary_fields = (
                    "id", "web_name", "full_name", "team_short", "position", "cost", "current_minutes",
                    "total_points", "expected_points", "status", "injury_reason", "is_new_pl_entrant",
                    "new_entrant_category", "is_recently_injured", "is_recent_injury_returnee",
                    "injury_return_ramp", "expected_minutes_projected", "sample_size_confidence",
                    "normalized_xg_90", "normalized_xa_90", "raw_current_xg90", "raw_current_xa90",
                    "normalized_form", "form", "status_tags", "normalization_notes"
                )

                self.send_json_response({
                    "success": True,
                    "summary": {
                        "total_unified_players": len(engine.unified_players),
                        "new_pl_entrants_count": len(new_entrants),
                        "recently_injured_count": len(recently_injured),
                        "recent_returnees_count": len(returnees),
                        "cached_match_histories": len(stats_normalizer.match_history_cache)
                    },
                    "new_entrants": [{k: p.get(k) for k in summary_fields} for p in new_entrants],
                    "recently_injured": [{k: p.get(k) for k in summary_fields} for p in recently_injured],
                    "recent_returnees": [{k: p.get(k) for k in summary_fields} for p in returnees]
                })
                return

            # API: On-demand Data Pull for New Entrants and Injured Players
            elif path == "/api/stats-normalization/pull-data":
                from stats_normalization import stats_normalizer
                target_ids = []
                for p in engine.unified_players:
                    if p.get("is_new_pl_entrant") or p.get("is_recently_injured") or p.get("is_recent_injury_returnee"):
                        target_ids.append(p["id"])
                stats_normalizer.batch_prefetch_target_players(target_ids[:100], max_requests=100)
                # Re-unify engine to apply fresh match logs
                engine.load_and_unify_data(force_refresh=True)
                self.send_json_response({
                    "success": True,
                    "message": f"Successfully pulled latest match and injury data for {len(target_ids[:100])} targeted players.",
                    "cached_count": len(stats_normalizer.match_history_cache)
                })
                return

            # API: Multi-Gameweek Strategy Player Rankings
            elif path == "/api/strategy/player-rankings":
                qs = parse_qs(parsed.query)
                try:
                    horizon = int(qs.get("horizon", [5])[0])
                except (ValueError, TypeError):
                    horizon = 5
                target_gws = engine.strategy.get_target_gameweeks(horizon)
                player_map = {}
                for p in engine.unified_players:
                    if p.get("is_transferred_out") or p.get("status") in ['u', 'n']:
                        continue
                    player_map[p["id"]] = engine.strategy.calculate_player_multi_gw_xp(p, target_gws)
                rankings = list(player_map.values())
                rankings.sort(key=lambda x: x["total_horizon_xp"], reverse=True)
                self.send_json_response({
                    "horizon": len(target_gws),
                    "target_gameweeks": target_gws,
                    "rankings": rankings
                })
                return

            # API: Authenticated User Profile
            elif path == "/api/auth/me":
                auth_header = self.headers.get("Authorization", "")
                token = auth_header.replace("Bearer ", "").strip()
                user = auth_engine.get_user_by_token(token)
                if user:
                    self.send_json_response({"success": True, "user": user})
                else:
                    self.send_json_response({"success": False, "error": "Not authenticated"}, 401)
                return

            # Serve football vector favicon
            elif path in ["/favicon.ico", "/favicon.svg"]:
                fav_path = os.path.join(STATIC_DIR, "favicon.svg")
                if os.path.exists(fav_path):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml")
                    with open(fav_path, "rb") as f:
                        content = f.read()
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(content)
                    return

            # Serve static frontend files
            if path == "/" or path == "":
                file_path = os.path.join(STATIC_DIR, "index.html")
            else:
                rel_path = path.lstrip("/")
                file_path = os.path.join(STATIC_DIR, rel_path)

            if os.path.exists(file_path) and os.path.isfile(file_path):
                mime_type, _ = mimetypes.guess_type(file_path)
                self.send_response(200)
                self.send_header("Content-Type", mime_type or "application/octet-stream")
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
                if not head_only:
                    self.wfile.write(b"404 Not Found")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"Error handling GET {self.path}: {e}", exc_info=True)
            try:
                self.send_json_response({"error": str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                body = json.loads(post_body) if post_body else {}
            except Exception:
                body = {}

            # API: Complete Gameweek Data Update Pipeline
            if path in ["/api/gameweek/update", "/api/gameweek/refresh", "/api/pipeline/trigger"]:
                res = pipeline.run_pipeline(force=True, trigger_source="api_post_request")
                self.send_json_response(res)
                return

            # API: Automated Gameweek Auto-Sync (POST)
            elif path == "/api/pipeline/auto-sync":
                new_gw = pipeline.check_for_completed_gameweek()
                if new_gw:
                    pipeline.log(f"Auto-Sync (POST): Detected completed Gameweek {new_gw}. Triggering automated pipeline...")
                    res = pipeline.run_pipeline(force=True, trigger_source="client_auto_sync_post")
                    self.send_json_response({
                        "synced": True,
                        "updated": True,
                        "new_gameweek": new_gw,
                        "gameweek_info": engine.fpl.get_gameweek_info(),
                        "result": res
                    })
                else:
                    self.send_json_response({
                        "synced": True,
                        "updated": False,
                        "is_running": pipeline.is_running,
                        "last_completed_gw": pipeline.status_data.get("last_completed_gw", 3),
                        "target_next_gw": pipeline.status_data.get("target_next_gw", 4),
                        "gameweek_info": engine.fpl.get_gameweek_info()
                    })
                return

            # API: Toggle Automated Scheduler
            elif path == "/api/pipeline/toggle":
                enabled = body.get("enabled", not pipeline.status_data.get("enabled", True))
                pipeline.status_data["enabled"] = enabled
                pipeline._save_persisted_status()
                if enabled and not pipeline.scheduler_active:
                    pipeline.start_scheduler()
                elif not enabled and pipeline.scheduler_active:
                    pipeline.stop_scheduler()
                self.send_json_response({"success": True, "enabled": enabled, "scheduler_active": pipeline.scheduler_active})
                return

            # API: Validate Lineup Formation & Substitutions
            elif path == "/api/validate-formation":
                starters = body.get("starters", [])
                valid, msg = engine.validate_formation(starters)
                self.send_json_response({"valid": valid, "message": msg})
                return

            # API: Run Recommendation on submitted custom squad
            elif path == "/api/recommend":
                starters = body.get("starters", [])
                bench = body.get("bench", [])
                
                # Robust extraction of bank and free_transfers
                raw_bank = body.get("bank")
                raw_ft = body.get("free_transfers")

                if not starters:
                    def_sq = engine.get_default_squad()
                    starters = def_sq["starters"]
                    bench = def_sq["bench"]
                    try:
                        bank = float(raw_bank) if raw_bank is not None else float(def_sq.get("bank", 0.5))
                    except (ValueError, TypeError):
                        bank = 0.5
                    try:
                        free_transfers = int(raw_ft) if raw_ft is not None else int(def_sq.get("free_transfers", 1))
                    except (ValueError, TypeError):
                        free_transfers = 1
                else:
                    try:
                        bank = float(raw_bank) if raw_bank is not None else 0.5
                    except (ValueError, TypeError):
                        bank = 0.5
                    try:
                        free_transfers = int(raw_ft) if raw_ft is not None else 1
                    except (ValueError, TypeError):
                        free_transfers = 1

                chips_used = body.get("chips_used", {})

                recommendation = engine.recommend_transfers_and_chips(
                    starters=starters,
                    bench=bench,
                    bank=bank,
                    free_transfers=free_transfers,
                    chips_used=chips_used
                )
                self.send_json_response(recommendation)
                return

            # API: Multi-Gameweek Strategy & Transfer Planner Simulation
            elif path == "/api/strategy/multi-gameweek":
                starters = body.get("starters", [])
                bench = body.get("bench", [])
                raw_bank = body.get("bank")
                raw_ft = body.get("free_transfers")
                horizon = int(body.get("horizon", 5))
                chips_used = body.get("chips_used", {})

                if not starters:
                    def_sq = engine.get_default_squad()
                    starters = def_sq["starters"]
                    bench = def_sq["bench"]
                    try:
                        bank = float(raw_bank) if raw_bank is not None else float(def_sq.get("bank", 0.5))
                    except (ValueError, TypeError):
                        bank = 0.5
                    try:
                        free_transfers = int(raw_ft) if raw_ft is not None else int(def_sq.get("free_transfers", 1))
                    except (ValueError, TypeError):
                        free_transfers = 1
                else:
                    try:
                        bank = float(raw_bank) if raw_bank is not None else 0.5
                    except (ValueError, TypeError):
                        bank = 0.5
                    try:
                        free_transfers = int(raw_ft) if raw_ft is not None else 1
                    except (ValueError, TypeError):
                        free_transfers = 1

                plan = engine.strategy.simulate_multi_gw_strategy(
                    starters=starters,
                    bench=bench,
                    bank=bank,
                    free_transfers=free_transfers,
                    chips_used=chips_used,
                    horizon=horizon
                )
                self.send_json_response(plan)
                return

            # API: Import Squad via FPL Manager ID or URL (e.g. /my-team or /transfers)
            elif path == "/api/import-manager":
                manager_input = body.get("manager_id") or body.get("url")
                manager_id_int = extract_manager_id(manager_input)
                if not manager_id_int:
                    self.send_json_response({"success": False, "error": f"Please enter a valid FPL Manager ID or link (got '{manager_input}')."}, 400)
                    return

                res = engine.fpl.fetch_my_team_and_transfers(manager_id_int)

                if not res.get("success"):
                    status_code = res.get("status_code", 400)
                    self.send_json_response(res, status_code)
                    return

                picks = res.get("picks", [])
                sorted_picks = sorted(picks, key=lambda p: p.get("position", 0)) if picks else []
                starters = res.get("starters") or [p["element"] for p in sorted_picks if p.get("position", 1) <= 11]
                bench = res.get("bench") or [p["element"] for p in sorted_picks if p.get("position", 1) > 11]
                captain = res.get("captain") or next((p["element"] for p in sorted_picks if p.get("is_captain")), starters[0] if starters else None)
                vc = res.get("vice_captain") or next((p["element"] for p in sorted_picks if p.get("is_vice_captain")), starters[1] if len(starters) > 1 else None)
                bank = res.get("bank", 0.0)
                chips = res.get("chips", [])
                element_pricing = res.get("element_pricing", {})

                all_squad_pids = starters + bench
                missing_pids = [pid for pid in all_squad_pids if pid not in engine.players_by_id]
                if missing_pids:
                    logger.info(f"Missing player IDs in catalog {missing_pids}; refreshing bootstrap catalog...")
                    engine.update_gameweek_data(force_refresh=True)

                squad_players = []
                for pid in all_squad_pids:
                    p = engine.players_by_id.get(pid)
                    if p:
                        p_copy = {k: v for k, v in p.items() if k not in ("shotmap", "all_fotmob_stats", "historical_past")}
                        p_copy["upcoming_fixtures"] = (p.get("upcoming_fixtures") or [])[:5]
                        if pid in element_pricing:
                            p_copy["purchase_price"] = element_pricing[pid].get("purchase_price")
                            p_copy["selling_price"] = element_pricing[pid].get("selling_price")
                        squad_players.append(p_copy)

                manager_name = res.get("manager_name") or f"Manager #{manager_id_int}"
                team_name = res.get("team_name") or f"Team #{manager_id_int}"
                badge_url = res.get("badge_url")
                overall_points = res.get("overall_points", 0)
                overall_rank = res.get("overall_rank", 0)

                cap_player = engine.players_by_id.get(captain)
                vc_player = engine.players_by_id.get(vc)
                captain_name = cap_player.get("web_name", "") if cap_player else ""
                vc_name = vc_player.get("web_name", "") if vc_player else ""

                self.send_json_response({
                    "success": True,
                    "authenticated": False,
                    "manager_id": manager_id_int,
                    "manager_name": manager_name,
                    "team_name": team_name,
                    "badge_url": badge_url,
                    "overall_points": overall_points,
                    "overall_rank": overall_rank,
                    "source": res.get("source", "official-fpl-entry-and-picks"),
                    "loaded_from_gameweek": res.get("loaded_from_gameweek"),
                    "target_gameweek": res.get("target_gameweek"),
                    "was_reverted_from_freehit": res.get("was_reverted_from_freehit", False),
                    "chips_by_gameweek": res.get("chips_by_gameweek", {}),
                    "applied_upcoming_transfers": res.get("applied_upcoming_transfers", []),
                    "active_upcoming_chip": res.get("active_upcoming_chip"),
                    "transfers_made": res.get("transfers_made", 0),
                    "transfer_cost": res.get("transfer_cost", 0),
                    "free_transfers": res.get("free_transfers", 1),
                    "free_transfers_allocated": res.get("free_transfers_allocated", res.get("free_transfers", 1)),
                    "ft_history_breakdown": res.get("ft_history_breakdown", []),
                    "starters": starters,
                    "bench": bench,
                    "captain": captain,
                    "vice_captain": vc,
                    "captain_name": captain_name,
                    "vice_captain_name": vc_name,
                    "bank": bank,
                    "chips": chips,
                    "element_pricing": element_pricing,
                    "players": squad_players
                })
                return

            # API: Ingest 1-Click Bookmarklet Live Squad Payload
            elif path == "/api/sync-payload":
                payload = body.get("payload") or body
                manager_id = payload.get("manager_id")
                picks = payload.get("picks", [])
                chips = payload.get("chips", [])
                transfers_info = payload.get("transfers", {}) or {}
                manager_name = payload.get("manager_name") or (f"Manager #{manager_id}" if manager_id else "Official Manager")
                team_name = payload.get("team_name") or (f"Team #{manager_id}" if manager_id else "My Live Squad")
                badge_url = payload.get("badge_url")
                overall_points = payload.get("overall_points", 0)
                overall_rank = payload.get("overall_rank", 0)

                if not picks:
                    self.send_json_response({"success": False, "error": "No picks found in sync payload."}, 400)
                    return

                sorted_picks = sorted(picks, key=lambda p: p.get("position", 0))
                starters = [p["element"] for p in sorted_picks if p.get("position", 1) <= 11]
                bench = [p["element"] for p in sorted_picks if p.get("position", 1) > 11]
                captain = next((p["element"] for p in sorted_picks if p.get("is_captain")), starters[0] if starters else None)
                vc = next((p["element"] for p in sorted_picks if p.get("is_vice_captain")), starters[1] if len(starters) > 1 else None)

                # Parse bank (FPL /api/my-team/ returns bank in tenths, e.g., 15 = £1.5m)
                raw_bank = transfers_info.get("bank")
                if raw_bank is None:
                    raw_bank = payload.get("bank", 0.5)
                try:
                    b_val = float(raw_bank)
                    bank = (b_val / 10.0) if b_val >= 10.0 else b_val
                except Exception:
                    bank = 0.5

                # Parse pricing
                element_pricing = {}
                for p in sorted_picks:
                    pid = p.get("element")
                    if pid:
                        element_pricing[pid] = {
                            "purchase_price": (p.get("purchase_price", 0) / 10.0) if p.get("purchase_price") else None,
                            "selling_price": (p.get("selling_price", 0) / 10.0) if p.get("selling_price") else None
                        }

                # Missing players check
                all_squad_pids = starters + bench
                missing_pids = [pid for pid in all_squad_pids if pid not in engine.players_by_id]
                if missing_pids:
                    logger.info(f"Missing player IDs {missing_pids}; refreshing catalog...")
                    engine.update_gameweek_data(force_refresh=True)

                squad_players = []
                for pid in all_squad_pids:
                    p = engine.players_by_id.get(pid)
                    if p:
                        p_copy = {k: v for k, v in p.items() if k not in ("shotmap", "all_fotmob_stats", "historical_past")}
                        p_copy["upcoming_fixtures"] = (p.get("upcoming_fixtures") or [])[:5]
                        if pid in element_pricing:
                            p_copy["purchase_price"] = element_pricing[pid].get("purchase_price")
                            p_copy["selling_price"] = element_pricing[pid].get("selling_price")
                        squad_players.append(p_copy)

                cap_player = engine.players_by_id.get(captain)
                vc_player = engine.players_by_id.get(vc)
                captain_name = cap_player.get("web_name", "") if cap_player else ""
                vc_name = vc_player.get("web_name", "") if vc_player else ""

                ft_limit = transfers_info.get("limit")
                ft_made = transfers_info.get("made", 0)
                if ft_limit is not None:
                    try:
                        ft_avail = max(0, int(ft_limit) - int(ft_made))
                        ft_alloc = int(ft_limit)
                    except Exception:
                        ft_avail = 1
                        ft_alloc = 1
                elif manager_id:
                    ft_calc = engine.fpl.calculate_free_transfers(manager_id=int(manager_id))
                    ft_avail = ft_calc.get("free_transfers", 1)
                    ft_alloc = ft_calc.get("free_transfers_allocated", 1)
                else:
                    ft_avail = 1
                    ft_alloc = 1

                self.send_json_response({
                    "success": True,
                    "authenticated": True,
                    "manager_id": manager_id,
                    "manager_name": manager_name,
                    "team_name": team_name,
                    "badge_url": badge_url,
                    "overall_points": overall_points,
                    "overall_rank": overall_rank,
                    "source": "1-click-fpl-bookmarklet",
                    "free_transfers": ft_avail,
                    "free_transfers_allocated": ft_alloc,
                    "starters": starters,
                    "bench": bench,
                    "captain": captain,
                    "vice_captain": vc,
                    "captain_name": captain_name,
                    "vice_captain_name": vc_name,
                    "bank": bank,
                    "chips": chips,
                    "element_pricing": element_pricing,
                    "players": squad_players
                })
                return

            # API: ChatFPL Conversational AI Query
            elif path == "/api/chat":
                msg = body.get("message", "").strip()
                squad = body.get("squad")
                history = body.get("history", [])
                if not msg:
                    self.send_json_response({"success": False, "error": "Message is required."}, 400)
                    return

                res = chat_engine.process_query(msg, squad, history=history)
                self.send_json_response(res)
                return

            # API: User Registration
            elif path == "/api/auth/register":
                email = body.get("email", "")
                password = body.get("password", "")
                manager_id = body.get("manager_id")
                res = auth_engine.register(email, password, manager_id)
                status_code = 200 if res.get("success") else 400
                self.send_json_response(res, status_code)
                return

            # API: User Login
            elif path == "/api/auth/login":
                email = body.get("email", "")
                password = body.get("password", "")
                res = auth_engine.login(email, password)
                status_code = 200 if res.get("success") else 401
                self.send_json_response(res, status_code)
                return

            # API: User Logout
            elif path == "/api/auth/logout":
                auth_header = self.headers.get("Authorization", "")
                token = body.get("token") or auth_header.replace("Bearer ", "").strip()
                res = auth_engine.logout(token)
                self.send_json_response({"success": res})
                return

            # API: Update Linked FPL Manager ID
            elif path == "/api/auth/update-manager":
                auth_header = self.headers.get("Authorization", "")
                token = auth_header.replace("Bearer ", "").strip()
                manager_id = body.get("manager_id")
                if not manager_id:
                    self.send_json_response({"success": False, "error": "Manager ID is required"}, 400)
                    return
                try:
                    manager_id_int = int(str(manager_id).strip())
                except (ValueError, TypeError):
                    self.send_json_response({"success": False, "error": "Invalid Manager ID format"}, 400)
                    return
                res = auth_engine.update_manager_id(token, manager_id_int)
                status_code = 200 if res.get("success") else 400
                self.send_json_response(res, status_code)
                return

            self.send_response(404)
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"Error handling POST {self.path}: {e}", exc_info=True)
            try:
                self.send_json_response({"error": str(e)}, 500)
            except Exception:
                pass

    def send_json_response(self, data: Any, status: int = 200):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass

class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def start_server(port: int = PORT):
    server_address = ("", port)
    httpd = ReusableThreadingHTTPServer(server_address, FPLRequestHandler)
    logger.info(f"Live at http://localhost:{port} (Threading enabled, socket reuse active)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server.")
        httpd.server_close()

if __name__ == "__main__":
    start_server()
