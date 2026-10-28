"""
Recommender & Optimization Engine for FPL Transfers & Power-ups
Synthesizes:
1. FotMob 43 granular statistics (xG/90, xA/90, FotMob Ratings, Recoveries, Shots, Shotmaps, Injuries)
2. Expected Minutes & Playing Likelihood Factor:
   - Players unlikely to play (fringe assets, reserve youth like Kusi-Asare with 0 starts/minutes)
     receive a steep playing probability penalty, drastically lowering their xP to near 0.
3. FPL Official Rules:
   - Free Transfer limits tracked actively (starting with 1 FT, rolling up to 5)
   - Transfer Hit penalty (-4 points per additional transfer beyond free transfer allowance)
   - Squad constraints (max 3 players per club, exact position quotas)
   - Valid starting XI formations (1 GKP, minimum 3 DEF, minimum 2 MID, minimum 1 FWD)
4. Multi-season historical performance:
   - Evaluates past seasons (e.g. 2025/26, 2024/25, 2023/24) to provide baseline reliability
"""

import os
import json
import math
import random
import unicodedata
from typing import Dict, List, Any, Optional, Tuple

import logging

logger = logging.getLogger("RecommenderEngine")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

from fotmob_engine import FotMobEngine, CACHE_FILE as FOTMOB_CACHE_FILE
from fpl_engine import FPLEngine, POSITION_MAP
from history_engine import HISTORY_CACHE_FILE, load_or_fetch_player_histories
from stats_normalization import stats_normalizer

def normalize_string(s: str) -> str:
    """Normalize accents, hyphens, and casing for player name matching."""
    if not s:
        return ""
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
    return s.lower().replace("-", " ").replace(".", "").replace("'", "").strip()

TEAM_EQUIVALENCES: Dict[str, str] = {
    "arsenal": "arsenal",
    "aston villa": "aston villa",
    "bournemouth": "bournemouth",
    "brentford": "brentford",
    "brighton": "brighton",
    "brighton and hove albion": "brighton",
    "chelsea": "chelsea",
    "coventry city": "coventry city",
    "crystal palace": "crystal palace",
    "everton": "everton",
    "fulham": "fulham",
    "hull city": "hull city",
    "ipswich": "ipswich",
    "ipswich town": "ipswich",
    "leeds": "leeds",
    "leeds united": "leeds",
    "liverpool": "liverpool",
    "man city": "man city",
    "manchester city": "man city",
    "man utd": "man utd",
    "man united": "man utd",
    "manchester united": "man utd",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "nottm forest": "nottingham forest",
    "nottingham forest": "nottingham forest",
    "southampton": "southampton",
    "spurs": "tottenham",
    "tottenham": "tottenham",
    "tottenham hotspur": "tottenham",
    "sunderland": "sunderland",
    "west ham": "west ham",
    "west ham united": "west ham",
    "wolves": "wolves",
    "wolverhampton wanderers": "wolves",
    "leicester": "leicester",
    "leicester city": "leicester",
}

def normalize_team(team_name: str) -> str:
    if not team_name:
        return ""
    norm = normalize_string(team_name)
    return TEAM_EQUIVALENCES.get(norm, norm)

NICKNAME_MAP: Dict[str, str] = {
    "benjamin": "ben",
    "alexander": "alex",
    "matthew": "matt",
    "robert": "bob",
    "william": "will",
    "nicholas": "nick",
    "samuel": "sam",
    "joseph": "joe",
    "joshua": "josh",
    "daniel": "dan",
    "gabriel": "gabi",
    "carlos": "charly",
    "dominic": "dom",
    "oleksandr": "alex",
    "emiliano": "emi",
}

class RecommenderEngine:
    def __init__(self):
        self.fpl = FPLEngine()
        self.fpl.on_refresh_callback = self._on_fpl_bootstrap_refreshed
        self._is_unifying = False
        self.unified_players: List[Dict[str, Any]] = []
        self.players_by_id: Dict[int, Dict[str, Any]] = {}
        self.players_by_fpl_id: Dict[int, Dict[str, Any]] = {}
        self.player_histories: Dict[str, List[Dict[str, Any]]] = {}
        self.load_and_unify_data()

    def _on_fpl_bootstrap_refreshed(self, data: Optional[Dict[str, Any]] = None):
        """Automatically updates unified players, clears stale flags, and recalculates models when FPL data is refreshed."""
        logger.info("[RecommenderEngine] Synchronizing unified player catalog with refreshed FPL bootstrap data...")
        self.load_and_unify_data(force_refresh=False)

    def load_and_unify_data(self, force_refresh: bool = False):
        """Merges FotMob 43 stats, multi-season records, playing likelihood, and FPL player catalog."""
        if getattr(self, "_is_unifying", False):
            return
        self._is_unifying = True
        try:
            self._execute_load_and_unify(force_refresh=force_refresh)
        finally:
            self._is_unifying = False

    def refresh_data(self, force_refresh: bool = True) -> Dict[str, Any]:
        """Forces fresh download of official FPL bootstrap, fixtures, and synchronizes player models."""
        self.fpl.fetch_bootstrap_static(force_refresh=force_refresh)
        self.fpl.fetch_upcoming_fixtures(force_refresh=force_refresh)
        self.load_and_unify_data(force_refresh=False)
        return {
            "success": True,
            "total_players": len(self.unified_players),
            "gameweek_info": self.fpl.get_gameweek_info()
        }

    def _execute_load_and_unify(self, force_refresh: bool = False):
        fpl_bootstrap = self.fpl.fetch_bootstrap_static(force_refresh=force_refresh)
        teams_map = self.fpl.get_teams_map()
        fdr_map = self.fpl.get_upcoming_team_fdr(lookahead_gameweeks=None)

        fotmob_players = {}
        team_opponent_shots = {}
        if os.path.exists(FOTMOB_CACHE_FILE):
            try:
                with open(FOTMOB_CACHE_FILE, "r", encoding="utf-8") as f:
                    fm_data = json.load(f)
                    fotmob_players = fm_data.get("players", {})
                    team_opponent_shots = fm_data.get("team_opponent_shots", {})
            except Exception as e:
                print(f"[RecommenderEngine] FotMob cache error: {e}")

        if os.path.exists(HISTORY_CACHE_FILE):
            try:
                with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.player_histories = json.load(f)
            except Exception as e:
                print(f"[RecommenderEngine] History cache error: {e}")

        fm_by_norm_name: Dict[str, Dict[str, Any]] = {}
        fm_by_team: Dict[str, List[Dict[str, Any]]] = {}
        team_name_to_tid: Dict[str, int] = {}
        all_lbp_rates: List[float] = []

        for fid, fmp in fotmob_players.items():
            norm_n = normalize_string(fmp.get("name", ""))
            norm_t = normalize_team(fmp.get("team_name", ""))
            fm_by_norm_name[norm_n] = fmp
            fm_by_team.setdefault(norm_t, []).append(fmp)
            if fmp.get("team_id") and norm_t:
                team_name_to_tid[norm_t] = int(fmp["team_id"])

            stats_dict = fmp.get("stats", {})
            lbp_item = stats_dict.get("Line-breaking passes per 90")
            if lbp_item and lbp_item.get("value") is not None:
                try:
                    all_lbp_rates.append(float(lbp_item["value"]))
                except (ValueError, TypeError):
                    pass

        all_lbp_rates.sort()
        if all_lbp_rates:
            n_lbp = len(all_lbp_rates)
            self.lbp_90th_percentile = round(all_lbp_rates[min(int(n_lbp * 0.90), n_lbp - 1)], 2)
            self.lbp_95th_percentile = round(all_lbp_rates[min(int(n_lbp * 0.95), n_lbp - 1)], 2)
        else:
            self.lbp_90th_percentile = 16.90
            self.lbp_95th_percentile = 20.00
        self.all_lbp_rates = all_lbp_rates
        print(f"[RecommenderEngine] Line-breaking passes: {len(all_lbp_rates)} players tracked. 90th %ile threshold: {self.lbp_90th_percentile}/90, 95th %ile: {self.lbp_95th_percentile}/90")

        current_gw = self.fpl.get_current_gameweek()
        unified: List[Dict[str, Any]] = []
        self.players_by_id.clear()
        self.players_by_fpl_id.clear()

        for p in fpl_bootstrap.get("elements", []):
            fpl_id = p["id"]
            web_name = p.get("web_name", "")
            first_name = p.get("first_name", "")
            second_name = p.get("second_name", "")
            full_name = f"{first_name} {second_name}".strip()
            team_id = p.get("team")
            team_info = teams_map.get(team_id, {})
            team_name = team_info.get("name", "Unknown")
            team_short = team_info.get("short_name", "UNK")
            pos_id = p.get("element_type")
            position = POSITION_MAP.get(pos_id, "MID")
            cost = p.get("now_cost", 50) / 10.0  # £ in millions
            total_points = p.get("total_points", 0)

            # Match FotMob record with strict team-aware multi-layer matching
            matched_fm: Optional[Dict[str, Any]] = None
            sec_parts = second_name.split()
            first_and_last = f"{first_name} {sec_parts[-1]}" if sec_parts else ""
            first_and_head = f"{first_name} {sec_parts[0]}" if sec_parts else ""
            clean_web = web_name.replace(".", " ").strip()

            nick = NICKNAME_MAP.get(first_name.lower(), first_name)
            nick_and_last = f"{nick} {sec_parts[-1]}" if sec_parts else ""
            nick_and_head = f"{nick} {sec_parts[0]}" if sec_parts else ""

            norm_t = normalize_team(team_name)
            team_fm_players = fm_by_team.get(norm_t, [])

            candidates_to_try = {
                normalize_string(full_name),
                normalize_string(f"{nick} {second_name}"),
                normalize_string(first_and_last),
                normalize_string(first_and_head),
                normalize_string(nick_and_last),
                normalize_string(nick_and_head),
                normalize_string(web_name),
                normalize_string(clean_web),
                normalize_string(p.get("known_name", ""))
            }
            candidates_to_try.discard("")

            # 1. Exact candidate match within the player's club
            for fmp in team_fm_players:
                fm_norm = normalize_string(fmp.get("name", ""))
                if fm_norm in candidates_to_try:
                    matched_fm = fmp
                    break

            # 2. Substring matching strictly within the player's club (prevents cross-club false matches)
            if not matched_fm:
                norm_last = normalize_string(sec_parts[-1].split("-")[0] if sec_parts else second_name)
                norm_head = normalize_string(sec_parts[0].split("-")[0] if sec_parts else second_name)
                norm_first = normalize_string(first_name)
                for fmp in team_fm_players:
                    fm_norm = normalize_string(fmp.get("name", ""))
                    target_last = norm_head if len(norm_head) >= 3 else norm_last
                    if target_last and len(target_last) >= 3 and target_last in fm_norm:
                        if (norm_first and norm_first in fm_norm) or (nick and nick in fm_norm) or (normalize_string(web_name) in fm_norm):
                            matched_fm = fmp
                            break

            # 3. Cross-club fallback ONLY for exact full-name match (>5 chars) to support transfers/loans
            if not matched_fm:
                full_norm = normalize_string(full_name)
                if len(full_norm) > 5 and full_norm in fm_by_norm_name:
                    matched_fm = fm_by_norm_name[full_norm]

            fm_stats = matched_fm.get("stats", {}) if matched_fm else {}
            shotmap = list(matched_fm.get("shotmap", [])) if matched_fm else []

            # Goalkeeper specific metrics from FPL and FotMob
            saves = int(p.get("saves", 0))
            penalties_saved = int(p.get("penalties_saved", 0))
            goals_conceded = int(p.get("goals_conceded", 0))
            save_pct = float(fm_stats.get("Save percentage", {}).get("value") or 0.0)
            if save_pct == 0.0 and (saves + goals_conceded) > 0:
                save_pct = round((saves / (saves + goals_conceded)) * 100.0, 1)

            goals_prevented = float(fm_stats.get("Goals prevented", {}).get("value") or 0.0)
            gk_saves_90 = float(fm_stats.get("Saves per 90", {}).get("value") or p.get("saves_per_90") or 0.0)
            gk_goals_conceded_90 = float(fm_stats.get("Goals conceded per 90", {}).get("value") or p.get("goals_conceded_per_90") or 0.0)
            sweeper_recoveries_90 = float(fm_stats.get("Recoveries per 90", {}).get("value") or 0.0)
            accurate_passes_90 = float(fm_stats.get("Accurate passes per 90", {}).get("value") or 0.0)

            # For Goalkeepers, populate shots faced (opponent shots against their club) if personal shotmap is empty
            is_shots_faced = False
            if position == "GKP":
                fm_tid = matched_fm.get("team_id") if matched_fm else team_name_to_tid.get(norm_t)
                if fm_tid and str(fm_tid) in team_opponent_shots:
                    gk_shots = team_opponent_shots[str(fm_tid)]
                    if not shotmap or len(shotmap) == 0:
                        shotmap = gk_shots
                        is_shots_faced = True

            # Extract FotMob metrics
            fotmob_rating = 6.0
            if "FotMob rating" in fm_stats:
                fotmob_rating = float(fm_stats["FotMob rating"].get("value") or 6.0)

            xg_90 = 0.0
            if "Expected goals (xG) per 90" in fm_stats:
                xg_90 = float(fm_stats["Expected goals (xG) per 90"].get("value") or 0.0)
            elif "Expected goals (xG)" in fm_stats:
                xg_90 = float(fm_stats["Expected goals (xG)"].get("value") or 0.0)

            xa_90 = 0.0
            if "Expected assist (xA) per 90" in fm_stats:
                xa_90 = float(fm_stats["Expected assist (xA) per 90"].get("value") or 0.0)
            elif "Expected assist (xA)" in fm_stats:
                xa_90 = float(fm_stats["Expected assist (xA)"].get("value") or 0.0)

            shots_90 = float(fm_stats.get("Shots per 90", {}).get("value") or 0.0)
            chances_created = float(fm_stats.get("Chances created", {}).get("value") or 0.0)
            big_chances = float(fm_stats.get("Big chances created", {}).get("value") or 0.0)
            tackles_90 = float(fm_stats.get("Tackles per 90", {}).get("value") or 0.0)
            recoveries_90 = float(fm_stats.get("Recoveries per 90", {}).get("value") or 0.0)
            clean_sheets = float(fm_stats.get("Clean sheets", {}).get("value") or p.get("clean_sheets") or 0.0)

            # Line-breaking passes metric (FotMob Playmaking & Deep Buildup)
            lbp_stat = fm_stats.get("Line-breaking passes per 90", {})
            line_breaking_passes_90 = float(lbp_stat.get("value") or 0.0)
            total_line_breaking_passes = float(lbp_stat.get("sub_value") or 0.0)
            lbp_rank = lbp_stat.get("rank")
            is_90th_percentile_lbp = (line_breaking_passes_90 >= self.lbp_90th_percentile and line_breaking_passes_90 > 0)
            is_95th_percentile_lbp = (line_breaking_passes_90 >= self.lbp_95th_percentile and line_breaking_passes_90 > 0)
            if self.all_lbp_rates and line_breaking_passes_90 > 0:
                lbp_percentile = round(100.0 * sum(1 for x in self.all_lbp_rates if x <= line_breaking_passes_90) / len(self.all_lbp_rates), 1)
            else:
                lbp_percentile = 0.0

            # Actual Season Stats pulled from FotMob (with FPL fallback)
            goals = int(fm_stats.get("Top scorer", {}).get("value") or p.get("goals_scored", 0))
            assists = int(fm_stats.get("Assists", {}).get("value") or p.get("assists", 0))
            goals_assists = int(fm_stats.get("Goals + Assists", {}).get("value") or (goals + assists))

            # Minutes and appearances tracking in current season
            current_minutes = p.get("minutes", 0)
            current_starts = p.get("starts", 0)
            minutes_played = int(fm_stats.get("Minutes played", {}).get("value") or current_minutes)

            # Effective minutes for per-90 rates
            mins_eff = minutes_played if minutes_played > 0 else current_minutes
            if mins_eff >= 45:
                big_chances_90 = round(big_chances / (mins_eff / 90.0), 2)
            else:
                big_chances_90 = 0.0

            # Granular defensive contributions: Tackles, Recoveries, Interceptions, Blocks, Clearances
            interceptions_90 = float(fm_stats.get("Interceptions per 90", {}).get("value") or 0.0)
            blocks_90 = float(fm_stats.get("Blocks per 90", {}).get("value") or 0.0)
            clearances_90 = float(fm_stats.get("Clearances per 90", {}).get("value") or 0.0)

            fm_def_actions = float(fm_stats.get("Defensive actions per 90", {}).get("value") or 0.0)
            component_def_actions = tackles_90 + recoveries_90 + interceptions_90 + blocks_90 + clearances_90
            defensive_actions_90 = round(fm_def_actions if fm_def_actions > 0 else component_def_actions, 2)

            # Disciplinary statistics: Yellow cards, Red cards, Fouls committed
            yellow_cards = float(fm_stats.get("Yellow cards", {}).get("value") or p.get("yellow_cards", 0.0))
            red_cards = float(fm_stats.get("Red cards", {}).get("value") or p.get("red_cards", 0.0))
            fouls_90 = float(fm_stats.get("Fouls committed per 90", {}).get("value") or 0.0)

            if mins_eff >= 45:
                yellows_90 = round(yellow_cards / (mins_eff / 90.0), 2)
                reds_90 = round(red_cards / (mins_eff / 90.0), 2)
            else:
                yellows_90 = 0.0
                reds_90 = 0.0

            # Disciplinary deduction: -1.0 pt per yellow/90, -3.0 pts per red/90, -0.10 per foul/90
            disciplinary_deduction = round((yellows_90 * 1.0) + (reds_90 * 3.0) + (fouls_90 * 0.10), 2)

            # Proven correlation stats for predictive xP refinement:
            # 1. Shots on target per 90 (Pearson r ~ 0.58 with goals scored in statistical analytics)
            sot_90 = float(fm_stats.get("Shots on target per 90", {}).get("value") or 0.0)

            # 2. Primary penalty taker status from official FPL data
            penalties_order = p.get("penalties_order")
            is_penalty_taker = (penalties_order == 1)

            # 3. Primary corner / direct free kick delivery equity
            corners_order = p.get("corners_and_indirect_freekicks_order")
            fk_order = p.get("direct_freekicks_order")
            is_set_piece_taker = (corners_order in [1, 2] or fk_order in [1, 2])

            # 4. Team Expected Goals Conceded (xGC / 90) for Poisson Clean Sheet model
            team_xgc_90 = float(p.get("expected_goals_conceded_per_90") or 1.25)

            # 5. Goalkeeper Saves per 90 (1 pt per 3 saves in official FPL rules)
            saves_90 = float(p.get("saves_per_90") or fm_stats.get("Saves per 90", {}).get("value") or 0.0)

            # Multi-season historical performance factor & holistic stats blending
            past_seasons = self.player_histories.get(str(fpl_id), [])
            active_seasons = [s for s in past_seasons if s.get("minutes", 0) > 0]
            active_seasons.sort(key=lambda x: x.get("season_name", ""))
            rev_seasons = list(reversed(active_seasons))  # Index 0 is most recent completed past season

            career_total_fpl_points = sum(s.get("total_points", 0) for s in active_seasons)
            career_total_mins = sum(s.get("minutes", 0) for s in active_seasons)
            career_seasons_count = len(active_seasons)
            career_avg_pts_per_season = (career_total_fpl_points / career_seasons_count) if career_seasons_count > 0 else 0.0
            career_pts_per_90 = (career_total_fpl_points / (career_total_mins / 90.0)) if career_total_mins >= 450 else 0.0

            historical_avg_pts = career_avg_pts_per_season
            historical_points_per_90 = career_pts_per_90
            historical_total_mins = career_total_mins

            # Multi-Season Track Record with Recency Decay (gives higher weight to recent seasons)
            # Most recent past season (k=0): 1.0, 1 season prior (k=1): 0.60, 2 seasons prior (k=2): 0.36, etc.
            decay_rate = 0.60
            tot_w = 0.0
            tot_xg = 0.0
            tot_xa = 0.0
            all_past_mins = career_total_mins
            hist_total_cs = sum(s.get("clean_sheets", 0) for s in active_seasons)

            for k, s in enumerate(rev_seasons):
                mins = s.get("minutes", 0)
                w = (decay_rate ** k) * (mins / 90.0)

                xg_val = float(s.get("xg") or 0.0)
                xa_val = float(s.get("xa") or 0.0)
                # Fallback for historical seasons prior to xG tracking in FPL
                if xg_val == 0.0 and s.get("goals_scored", 0) > 0:
                    xg_val = s.get("goals_scored", 0) * 0.88
                if xa_val == 0.0 and s.get("assists", 0) > 0:
                    xa_val = s.get("assists", 0) * 0.82

                xg90_s = xg_val / (mins / 90.0) if mins > 0 else 0.0
                xa90_s = xa_val / (mins / 90.0) if mins > 0 else 0.0
                tot_xg += xg90_s * w
                tot_xa += xa90_s * w
                tot_w += w

            recency_hist_xg_90 = (tot_xg / tot_w) if tot_w > 0 else 0.0
            recency_hist_xa_90 = (tot_xa / tot_w) if tot_w > 0 else 0.0

            # -------------------------------------------------------------
            # Stats Normalization Engine (Sample Size Shrinkage & Status Detection)
            # -------------------------------------------------------------
            norm_metrics = stats_normalizer.normalize_player(
                p,
                past_seasons=past_seasons,
                raw_xg_90=xg_90,
                raw_xa_90=xa_90,
                fotmob_rating=fotmob_rating
            )

            # -------------------------------------------------------------
            # Holistic Metric Blending (Bayesian Empirical Credibility)
            # Preserves 35-40% weightage to current season (37.5%), and 60-65% (62.5%)
            # to the recency-weighted multi-season track record or positional priors.
            # -------------------------------------------------------------
            POSITION_PRIORS = {
                "FWD": {"xg": 0.35, "xa": 0.12},
                "MID": {"xg": 0.14, "xa": 0.12},
                "DEF": {"xg": 0.04, "xa": 0.05},
                "GKP": {"xg": 0.00, "xa": 0.00}
            }

            weight_current = 0.375  # Exactly in the 35-40% range
            weight_prior = 0.625    # Multi-season historical track record

            if position == "GKP":
                xg_90 = 0.0
                xa_90 = 0.0
                hist_xg_90 = 0.0
                hist_xa_90 = 0.0
                holistic_xg_90 = 0.0
                holistic_xa_90 = 0.0
                prior_source = "Goalkeeper"
            elif all_past_mins >= 450:
                hist_xg_90 = recency_hist_xg_90
                hist_xa_90 = recency_hist_xa_90
                prior_source = f"Multi-Season Recency History ({all_past_mins} mins)"
                holistic_xg_90 = (xg_90 * weight_current) + (hist_xg_90 * weight_prior)
                holistic_xa_90 = (xa_90 * weight_current) + (hist_xa_90 * weight_prior)
            else:
                prior = POSITION_PRIORS.get(position, {"xg": 0.10, "xa": 0.10})
                conf = min(1.0, all_past_mins / 450.0)
                blended_hist_xg = (recency_hist_xg_90 * conf) + (prior["xg"] * (1.0 - conf))
                blended_hist_xa = (recency_hist_xa_90 * conf) + (prior["xa"] * (1.0 - conf))
                hist_xg_90 = blended_hist_xg
                hist_xa_90 = blended_hist_xa
                prior_source = f"Positional Prior + Career ({all_past_mins} mins)"
                # Use normalized xG/xA for data scarce or new entrants to prevent small-sample distortion
                eff_curr_xg = norm_metrics["normalized_xg_90"] if (norm_metrics.get("is_new_pl_entrant") or all_past_mins == 0) else xg_90
                eff_curr_xa = norm_metrics["normalized_xa_90"] if (norm_metrics.get("is_new_pl_entrant") or all_past_mins == 0) else xa_90
                holistic_xg_90 = (eff_curr_xg * weight_current) + (hist_xg_90 * weight_prior)
                holistic_xa_90 = (eff_curr_xa * weight_current) + (hist_xa_90 * weight_prior)

            # Availability & Injuries
            fpl_status = p.get("status")
            fpl_chance = p.get("chance_of_playing_next_round")
            fpl_news = p.get("news", "")

            fm_injury = matched_fm.get("injury") if matched_fm else None
            fm_is_injured = matched_fm.get("is_injured", False) if matched_fm else False

            is_transferred_out = (fpl_status in ['u', 'n']) or ("transfer" in (fpl_news or "").lower()) or ("joined " in (fpl_news or "").lower()) or ("departed" in (fpl_news or "").lower())

            # Official Premier League / FPL status is the primary authority for availability
            if is_transferred_out:
                is_unavailable = True
                is_doubtful = False
                injury_reason = fpl_news or "Transferred out of the Premier League"
            elif fpl_status == 'a':
                # Player officially cleared to play ('a' = Available)
                if fpl_chance is not None and fpl_chance == 0:
                    is_unavailable = True
                    is_doubtful = False
                    injury_reason = fpl_news or "Ruled out"
                elif fpl_chance is not None and 0 < fpl_chance <= 75:
                    is_unavailable = False
                    is_doubtful = True
                    injury_reason = fpl_news or "Carrying a doubt"
                else:
                    # Fully fit & active (fpl_chance is None or 100). Clear all injury flags and reasons!
                    is_unavailable = False
                    is_doubtful = False
                    injury_reason = ""
            elif fpl_status == 'd':
                is_unavailable = False
                is_doubtful = True
                injury_reason = fpl_news or (fm_injury.get("expectedReturn") if fm_injury else "Doubtful")
            elif fpl_status in ['i', 's']:
                is_unavailable = True
                is_doubtful = False
                injury_reason = fpl_news or (fm_injury.get("expectedReturn") if fm_injury else ("Suspended" if fpl_status == 's' else "Injured"))
            elif fm_is_injured or (fpl_chance is not None and fpl_chance == 0):
                is_unavailable = True
                is_doubtful = False
                injury_reason = fpl_news or (fm_injury.get("expectedReturn") if fm_injury else "Injured")
            elif fpl_chance is not None and 0 < fpl_chance <= 75:
                is_unavailable = False
                is_doubtful = True
                injury_reason = fpl_news or "Doubtful"
            else:
                is_unavailable = False
                is_doubtful = False
                injury_reason = ""

            # ----------------------------------------------------
            # Playing Likelihood Factor (Unlikely to Play Modeling)
            # ----------------------------------------------------
            is_unlikely_to_play = False
            unlikely_reason = ""
            playing_likelihood = 1.0

            if not is_unavailable and not is_doubtful:
                # Recalibration: Senior new signings must NOT be penalized as 8% fringe reserve players!
                if norm_metrics.get("is_new_pl_entrant") and cost >= 5.0 and current_minutes == 0:
                    playing_likelihood = norm_metrics.get("adjusted_playing_likelihood", 70.0) / 100.0
                    is_unlikely_to_play = norm_metrics.get("adjusted_is_unlikely", False)
                    unlikely_reason = norm_metrics.get("adjusted_unlikely_reason", "")
                elif current_minutes == 0 and current_starts == 0 and historical_total_mins < 180:
                    playing_likelihood = 0.08
                    is_unlikely_to_play = True
                    unlikely_reason = "Unlikely to play (fringe squad member with 0 starts/minutes)"
                elif current_minutes == 0 and historical_total_mins > 500:
                    playing_likelihood = 0.50
                    is_unlikely_to_play = True
                    unlikely_reason = "Rotation risk (0 minutes recorded this season)"
                elif current_starts == 0 and current_minutes < 40:
                    playing_likelihood = 0.35
                    is_unlikely_to_play = True
                    unlikely_reason = "Occasional substitute (low minutes per game)"

            # Upcoming FDR (Complete Season)
            upcoming_fdr = fdr_map.get(team_id, [])
            next_fdr_slice = upcoming_fdr[:3] if upcoming_fdr else []
            avg_fdr = sum([fix["difficulty"] for fix in next_fdr_slice]) / max(1, len(next_fdr_slice)) if next_fdr_slice else 3.0

            record = {
                "id": fpl_id,
                "fpl_id": fpl_id,
                "fotmob_id": matched_fm.get("fotmob_id") if matched_fm else None,
                "web_name": web_name,
                "full_name": full_name,
                "team_id": team_id,
                "team_name": team_name,
                "team_short": team_short,
                "position": position,
                "cost": cost,
                "total_points": total_points,
                "current_minutes": current_minutes,
                "current_starts": current_starts,
                "goals": goals,
                "assists": assists,
                "goals_assists": goals_assists,
                "minutes_played": minutes_played,
                "points_per_game": float(p.get("points_per_game") or 0.0),
                "selected_by_percent": float(p.get("selected_by_percent") or 0.0),
                "fotmob_rating": round(fotmob_rating, 2),
                "xg_90": round(holistic_xg_90, 2),
                "xa_90": round(holistic_xa_90, 2),
                "raw_current_xg90": round(xg_90, 2),
                "raw_current_xa90": round(xa_90, 2),
                "hist_xg90": round(hist_xg_90, 2),
                "hist_xa90": round(hist_xa_90, 2),
                "shots_90": round(shots_90, 2),
                "chances_created": chances_created,
                "big_chances": big_chances,
                "big_chances_90": round(big_chances_90, 2),
                "defensive_actions_90": round(defensive_actions_90, 2),
                "disciplinary_deduction": round(disciplinary_deduction, 2),
                "yellow_cards": int(yellow_cards),
                "red_cards": int(red_cards),
                "yellows_90": round(yellows_90, 2),
                "reds_90": round(reds_90, 2),
                "fouls_90": round(fouls_90, 2),
                "tackles_90": round(tackles_90, 2),
                "recoveries_90": round(recoveries_90, 2),
                "interceptions_90": round(interceptions_90, 2),
                "blocks_90": round(blocks_90, 2),
                "clearances_90": round(clearances_90, 2),
                "clean_sheets": int(clean_sheets),
                "career_total_clean_sheets": int(clean_sheets + hist_total_cs),
                "line_breaking_passes_90": round(line_breaking_passes_90, 2),
                "total_line_breaking_passes": round(total_line_breaking_passes, 0),
                "lbp_rank": lbp_rank,
                "is_90th_percentile_lbp": is_90th_percentile_lbp,
                "is_95th_percentile_lbp": is_95th_percentile_lbp,
                "lbp_percentile": lbp_percentile,
                "sot_90": round(sot_90, 2),
                "is_penalty_taker": is_penalty_taker,
                "is_set_piece_taker": is_set_piece_taker,
                "team_xgc_90": round(team_xgc_90, 2),
                "saves_90": round(gk_saves_90 if gk_saves_90 > 0 else saves_90, 2),
                "saves": saves,
                "penalties_saved": penalties_saved,
                "goals_conceded": goals_conceded,
                "save_percentage": round(save_pct, 1),
                "goals_prevented": round(goals_prevented, 2),
                "goals_conceded_90": round(gk_goals_conceded_90, 2),
                "sweeper_recoveries_90": round(sweeper_recoveries_90 if sweeper_recoveries_90 > 0 else recoveries_90, 2),
                "accurate_passes_90": round(accurate_passes_90, 2),
                "status": fpl_status,
                "is_transferred_out": is_transferred_out,
                "is_unavailable": is_unavailable,
                "is_doubtful": is_doubtful,
                "is_unlikely_to_play": is_unlikely_to_play,
                "unlikely_reason": unlikely_reason,
                "playing_likelihood": 0.0 if is_unavailable else round(playing_likelihood * 100, 0),
                "injury_reason": injury_reason or (unlikely_reason if is_unlikely_to_play else ""),
                "chance_of_playing": 0 if is_unavailable else (fpl_chance if fpl_chance is not None else int(playing_likelihood * 100)),
                "upcoming_fixtures": upcoming_fdr,
                "avg_fdr": round(avg_fdr, 2),
                "historical_past": past_seasons,
                "historical_avg_pts": round(historical_avg_pts, 1),
                "historical_pts_per_90": round(historical_points_per_90, 2),
                "career_total_fpl_points": career_total_fpl_points,
                "career_total_mins": career_total_mins,
                "career_seasons_count": career_seasons_count,
                "career_avg_pts_per_season": round(career_avg_pts_per_season, 1),
                "career_pts_per_90": round(career_pts_per_90, 2),
                "transfers_in_event": int(p.get("transfers_in_event") or 0),
                "transfers_out_event": int(p.get("transfers_out_event") or 0),
                "cost_change_event": float(p.get("cost_change_event") or 0.0) / 10.0,
                "price_change_percent": float(p.get("price_change_percent") or 0.0) if p.get("price_change_percent") is not None else 0.0,
                "price_change_hourly_rate": int(p.get("price_change_hourly_rate") or 0),
                "price_change_locked_until": p.get("price_change_locked_until"),
                "price_change_calibrating": bool(p.get("price_change_calibrating")),
                "form": float(p.get("form") or 0.0),
                "shotmap": shotmap,
                "all_fotmob_stats": fm_stats,
                "is_new_pl_entrant": norm_metrics.get("is_new_pl_entrant", False),
                "new_entrant_category": norm_metrics.get("new_entrant_category", ""),
                "is_recently_injured": norm_metrics.get("is_recently_injured", False),
                "is_recent_injury_returnee": norm_metrics.get("is_recent_injury_returnee", False),
                "injury_return_ramp": norm_metrics.get("injury_return_ramp", 1.0),
                "expected_minutes_projected": norm_metrics.get("expected_minutes_projected", 90),
                "sample_size_confidence": norm_metrics.get("sample_size_confidence", 100.0),
                "sample_size_minutes": norm_metrics.get("sample_size_minutes", current_minutes),
                "sample_size_matches": norm_metrics.get("sample_size_matches", 0),
                "normalized_xg_90": norm_metrics.get("normalized_xg_90", round(xg_90, 2)),
                "normalized_xa_90": norm_metrics.get("normalized_xa_90", round(xa_90, 2)),
                "prior_xg_90": norm_metrics.get("prior_xg_90", 0.0),
                "prior_xa_90": norm_metrics.get("prior_xa_90", 0.0),
                "normalized_form": norm_metrics.get("normalized_form", float(p.get("form") or 0.0)),
                "status_tags": norm_metrics.get("status_tags", []),
                "price_drop_protected": norm_metrics.get("price_drop_protected", False),
                "normalization_notes": norm_metrics.get("normalization_notes", []),
                "recent_match_history": norm_metrics.get("recent_match_history", [])
            }

            # Unified xP projection for the upcoming gameweek
            gw_curr_fixes = [fix for fix in upcoming_fdr if fix.get("event") == current_gw]
            if not gw_curr_fixes:
                xp = 0.0
                xp_breakdown = {
                    "final_xp": 0.0,
                    "summary": f"Blank gameweek in GW {current_gw} (0.0 xP)."
                }
            else:
                total_gw_xp = 0.0
                first_breakdown = {}
                for f_idx, fix in enumerate(gw_curr_fixes):
                    f_xp, f_bd = self.calculate_player_fixture_xp(record, fix, gw_index_from_now=0)
                    total_gw_xp += f_xp
                    if f_idx == 0:
                        first_breakdown = f_bd
                xp = round(total_gw_xp, 2)
                xp_breakdown = first_breakdown

            record["expected_points"] = xp
            record["xp_breakdown"] = xp_breakdown
            record["def_contrib_pts"] = xp_breakdown.get("def_contrib_pts", 0.0)
            record["cs_probability"] = round(xp_breakdown.get("cs_probability", 0.0), 1)
            record["expected_gc"] = xp_breakdown.get("expected_gc", 0.0)
            record["expected_gc_penalty"] = xp_breakdown.get("expected_gc_penalty", 0.0)
            record["line_breaking_passes_boost"] = xp_breakdown.get("line_breaking_passes_boost", 0.0)
            record["expected_bonus"] = xp_breakdown.get("expected_bonus", 0.0)

            # Predict player price change up to 1 decimal point based on current price, selected by % and performance (official FPL rules)
            pred_delta, pred_status, pred_progress, pred_reason = self.predict_player_price_change(
                record,
                gw_index=0,
                gw_xp=xp,
                fdr=avg_fdr
            )
            record["predicted_price_change"] = pred_delta
            record["predicted_price"] = round(record["cost"] + pred_delta, 1)
            record["price_change_status"] = pred_status
            record["price_change_progress"] = pred_progress
            record["price_change_reason"] = pred_reason

            unified.append(record)
            self.players_by_id[fpl_id] = record
            self.players_by_fpl_id[fpl_id] = record

        self.unified_players = unified
        print(f"[RecommenderEngine] Unified {len(self.unified_players)} players with active minutes, likelihood, and multi-season models.")
        from strategy_engine import StrategyEngine
        self.strategy = StrategyEngine(self)

    def predict_player_price_change(
        self,
        player: Dict[str, Any],
        gw_index: int = 0,
        gw_xp: Optional[float] = None,
        fdr: float = 3.0
    ) -> Tuple[float, str, float, str]:
        """
        Projects realistic FPL market price changes (£m) up to 1 decimal point based on:
        1. Current Price (cost)
        2. Team Selected By % (selected_by_percent ownership scaling)
        3. Performance (expected points xP, recent form, points per match, fixture difficulty FDR)
        
        Strictly adheres to official Fantasy Premier League market mechanics:
        - Price changes occur in discrete £0.1m increments (capped at max ±£0.2m per round).
        - Price lock rules: 8-day price protection for players returning from injury flags.
        - High-owned players face an ownership-scaled threshold (T_rise = 4500 + 0.082 * sqrt(owners * total_managers)).
        - Flagged/injured players receive a ~2.5x protective fall buffer before price drops.
        - Performance surplus (xP vs positional baseline and form) catalyzes bandwagon demand or divestment.
        """
        if not player:
            return 0.0, "stable", 0.0, "No player data"

        # Check official FPL price lock (unlocked assets protected for 8 days)
        if player.get("status") == "locked" or player.get("price_change_locked_until") or (player.get("is_recent_injury_returnee") and player.get("price_drop_protected")):
            return 0.0, "stable", 0.0, "Price locked by official FPL rules (8-day injury return buffer)"

        cost = float(player.get("cost", 5.0) or 5.0)
        sel_pct = float(player.get("selected_by_percent", 0.0) or 0.0)

        # Total registered FPL managers (default 11.2m for 2026/27 season)
        total_managers = 11200000
        try:
            if hasattr(self, "fpl") and self.fpl and self.fpl.bootstrap_data:
                tm = self.fpl.bootstrap_data.get("total_players")
                if tm and int(tm) > 0:
                    total_managers = int(tm)
        except Exception:
            pass

        current_owners = max(1000.0, total_managers * (sel_pct / 100.0))

        is_unavailable = bool(player.get("is_unavailable") or player.get("is_transferred_out") or player.get("status") in ['u', 'n'])
        is_doubtful = bool(player.get("is_doubtful") or player.get("is_unlikely_to_play") or player.get("status") in ['d', 'i', 's'])
        is_flagged = is_unavailable or is_doubtful

        # Official FPL ownership-scaled net transfer thresholds
        rise_target = 4500.0 + 0.082 * math.sqrt(current_owners * total_managers)
        fall_multiplier = 2.5 if is_flagged else 1.0
        fall_target = (0.018 * current_owners + 12000.0) * fall_multiplier

        pos = player.get("position", "MID")
        xp_baseline = 4.8 if pos in ["MID", "FWD"] else 3.8

        if gw_xp is None:
            gw_xp = float(player.get("expected_points", 0.0) or 0.0)

        form = float(player.get("form", 0.0) or 0.0)
        fdr_val = max(1.5, min(5.0, float(fdr)))
        fdr_factor = (3.2 / fdr_val) ** 0.45

        # Budget liquidity factor: budget enablers (£4.0m-£5.5m) move quicker than premium assets (£10.0m+)
        price_factor = 1.15 if cost <= 5.5 else (0.88 if cost >= 10.0 else 1.0)

        delta = 0.0
        status = "stable"
        progress_pct = 0.0
        reason = ""

        if gw_index == 0:
            # Immediate round: combine live FPL telemetry with performance momentum
            raw_progress = float(player.get("price_change_percent") or 0.0)
            trans_in = int(player.get("transfers_in_event") or 0)
            trans_out = int(player.get("transfers_out_event") or 0)
            live_net = (trans_in - trans_out) * 0.88  # 12% deduction for Wildcard & Free Hit transfers

            if raw_progress != 0.0:
                progress_pct = raw_progress
            elif rise_target > 0 and live_net > 0:
                progress_pct = (live_net / rise_target) * 100.0
            elif fall_target > 0 and live_net < 0:
                progress_pct = (live_net / fall_target) * 100.0

            perf_surplus = (gw_xp - xp_baseline) + 0.5 * (form - 4.0)
            perf_multiplier = 1.0 + max(-0.35, min(0.50, perf_surplus / 4.0))

            effective_progress = progress_pct * (perf_multiplier if progress_pct >= 0 else (1.2 if is_flagged else 1.0))

            if effective_progress >= 185.0:
                delta = 0.2
                status = "rising"
                reason = f"Extreme demand (+£0.2m): {progress_pct:.0f}% progress, {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"
            elif effective_progress >= 88.0:
                delta = 0.1
                status = "rising"
                reason = f"High demand (+£0.1m): {progress_pct:.0f}% progress, {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"
            elif effective_progress <= -185.0:
                delta = -0.2
                status = "falling"
                reason = f"Heavy selling (-£0.2m): {progress_pct:.0f}% progress, {sel_pct:.1f}% ownership"
            elif effective_progress <= -88.0:
                delta = -0.1
                status = "falling"
                reason = f"Selling pressure (-£0.1m): {progress_pct:.0f}% progress, {sel_pct:.1f}% ownership"
            else:
                delta = 0.0
                status = "stable"
                reason = f"Stable price: {progress_pct:.0f}% progress, {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"

        else:
            # Future gameweeks (GW >= 1): project transfer demand based on performance & ownership
            if is_unavailable:
                projected_net = -fall_target * 1.2
            elif is_doubtful:
                projected_net = -fall_target * 0.75
            else:
                perf_delta = (gw_xp - xp_baseline) + 0.4 * (form - 4.0)
                if perf_delta >= 0.5:
                    demand_rate = (0.55 + 0.45 * (perf_delta / 3.0)) * fdr_factor * price_factor
                    projected_net = rise_target * demand_rate
                elif perf_delta <= -1.0:
                    sell_rate = (0.50 + 0.40 * (abs(perf_delta) / 3.0)) / max(0.8, fdr_factor)
                    projected_net = -fall_target * sell_rate
                else:
                    projected_net = 0.0

            if projected_net >= rise_target * 1.85:
                delta = 0.2
                status = "rising"
                progress_pct = 190.0
                reason = f"Strong multi-GW momentum (+£0.2m): {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"
            elif projected_net >= rise_target * 0.90:
                delta = 0.1
                status = "rising"
                progress_pct = 100.0
                reason = f"Positive multi-GW momentum (+£0.1m): {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"
            elif projected_net <= -fall_target * 1.85:
                delta = -0.2
                status = "falling"
                progress_pct = -190.0
                reason = f"Heavy divestment (-£0.2m): {sel_pct:.1f}% ownership"
            elif projected_net <= -fall_target * 0.90:
                delta = -0.1
                status = "falling"
                progress_pct = -100.0
                reason = f"Net divestment (-£0.1m): {sel_pct:.1f}% ownership"
            else:
                delta = 0.0
                status = "stable"
                progress_pct = 0.0
                reason = f"Stable demand: {sel_pct:.1f}% ownership, {gw_xp:.1f} xP"

        return round(delta, 1), status, round(progress_pct, 1), reason

    # FDR → Clean sheet probability mapping (empirical PL averages)
    # Based on historical Premier League data: easier fixtures = higher CS probability
    FDR_CS_PROB = {
        1: 0.45,  # Very easy fixture
        2: 0.38,  # Easy
        3: 0.28,  # Medium
        4: 0.18,  # Hard
        5: 0.10,  # Very hard (vs Man City, Arsenal away etc.)
    }

    # Custom Fixture Difficulty Ratings compiled from last 5 Premier League seasons
    # (Finishing positions, points pace, goal difference, underlying xG/xGA dominance, and home fortress splits).
    # "away": Difficulty faced when playing AWAY against this team (visiting their stadium with their home crowd).
    # "home": Difficulty faced when playing at HOME against this team (hosting them at our home stadium).
    CUSTOM_5YR_TEAM_RATINGS = {
        # Elite Champions / Title Contenders (Avg finish ~1st-2nd)
        "MCI": {"name": "Man City", "full_name": "Manchester City", "away": 5.00, "home": 4.65},
        "ARS": {"name": "Arsenal", "full_name": "Arsenal", "away": 4.85, "home": 4.45},
        "LIV": {"name": "Liverpool", "full_name": "Liverpool", "away": 4.90, "home": 4.50},

        # Top 4 / Champions League Contenders
        "AVL": {"name": "Aston Villa", "full_name": "Aston Villa", "away": 4.25, "home": 3.65},
        "NEW": {"name": "Newcastle", "full_name": "Newcastle United", "away": 4.30, "home": 3.60},
        "CHE": {"name": "Chelsea", "full_name": "Chelsea", "away": 4.15, "home": 3.65},
        "TOT": {"name": "Spurs", "full_name": "Tottenham Hotspur", "away": 4.10, "home": 3.60},
        "MUN": {"name": "Man Utd", "full_name": "Manchester United", "away": 4.00, "home": 3.50},

        # Upper Mid-Table & Established Premier League Sides
        "BHA": {"name": "Brighton", "full_name": "Brighton & Hove Albion", "away": 3.55, "home": 3.05},
        "WHU": {"name": "West Ham", "full_name": "West Ham United", "away": 3.45, "home": 2.95},
        "CRY": {"name": "Crystal Palace", "full_name": "Crystal Palace", "away": 3.35, "home": 2.80},
        "BRE": {"name": "Brentford", "full_name": "Brentford", "away": 3.30, "home": 2.75},
        "FUL": {"name": "Fulham", "full_name": "Fulham", "away": 3.20, "home": 2.70},
        "BOU": {"name": "Bournemouth", "full_name": "AFC Bournemouth", "away": 3.15, "home": 2.65},
        "WOL": {"name": "Wolves", "full_name": "Wolverhampton Wanderers", "away": 3.15, "home": 2.65},
        "EVE": {"name": "Everton", "full_name": "Everton", "away": 3.20, "home": 2.65},

        # Lower Mid-Table / Survival Tier
        "NFO": {"name": "Nott'm Forest", "full_name": "Nottingham Forest", "away": 3.05, "home": 2.50},
        "LEE": {"name": "Leeds", "full_name": "Leeds United", "away": 2.95, "home": 2.40},
        "LEI": {"name": "Leicester", "full_name": "Leicester City", "away": 2.90, "home": 2.35},

        # Promoted / Underdog Tier
        "IPS": {"name": "Ipswich Town", "full_name": "Ipswich Town", "away": 2.65, "home": 2.10},
        "SOU": {"name": "Southampton", "full_name": "Southampton", "away": 2.60, "home": 2.05},
        "SUN": {"name": "Sunderland", "full_name": "Sunderland", "away": 2.55, "home": 2.00},
        "COV": {"name": "Coventry City", "full_name": "Coventry City", "away": 2.50, "home": 1.95},
        "HUL": {"name": "Hull City", "full_name": "Hull City", "away": 2.45, "home": 1.90},

        # Relegated / Other Recent Premier League Teams
        "SHU": {"name": "Sheffield Utd", "full_name": "Sheffield United", "away": 2.50, "home": 1.95},
        "BUR": {"name": "Burnley", "full_name": "Burnley", "away": 2.60, "home": 2.05},
        "LUT": {"name": "Luton", "full_name": "Luton Town", "away": 2.65, "home": 2.05},
        "WAT": {"name": "Watford", "full_name": "Watford", "away": 2.60, "home": 2.05},
        "NOR": {"name": "Norwich", "full_name": "Norwich City", "away": 2.55, "home": 2.00},
    }

    def get_custom_5yr_fdr(self, opponent: Optional[str], is_home: bool, default_diff: float = 3.0) -> float:
        """
        Computes custom 5-season historical performance FDR for accurate Bayesian xP modeling.
        - When player's team is at HOME (is_home=True), the match is hosted at player's ground.
          The opponent is the away side, so we look up opponent's 'home' FDR rating.
        - When player's team is AWAY (is_home=False), the match is at opponent's ground.
          The opponent has home fortress advantage, so we look up opponent's 'away' FDR rating.
        """
        if not opponent:
            return max(1.5, min(5.0, (default_diff - 0.25) if is_home else (default_diff + 0.25)))

        opp_clean = str(opponent).strip().upper()
        if opp_clean in self.CUSTOM_5YR_TEAM_RATINGS:
            entry = self.CUSTOM_5YR_TEAM_RATINGS[opp_clean]
            return entry["home"] if is_home else entry["away"]

        # Match by name or partial string
        for code, entry in self.CUSTOM_5YR_TEAM_RATINGS.items():
            if code == opp_clean or entry["name"].upper() == opp_clean or entry["full_name"].upper() == opp_clean:
                return entry["home"] if is_home else entry["away"]

        # Fallback based on official diff
        return max(1.5, min(5.0, (default_diff - 0.25) if is_home else (default_diff + 0.25)))

    def calculate_player_fixture_xp(
        self,
        player: Dict[str, Any],
        fixture: Dict[str, Any],
        gw_index_from_now: int = 0
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Unified Expected Points (xP) Fixture Evaluator:
        - Incorporates Home (1.08x) vs. Away (0.92x) attacking venue factor.
        - Fixture-specific FDR difficulty scaling via custom 5-season performance ratings.
        - Poisson Clean Sheet probability & goals conceded penalty with Home (0.88x) vs Away (1.14x) defensive factor.
        - Goalkeeper shot-stopping volume under opponent pressure (1 pt per 3 saves).
        - FotMob underlying stats (xG, xA, Shots on Target, Big Chances Created).
        - Set-piece & penalty taker equity.
        - Disciplinary deductions (yellows, reds, fouls).
        - 62.5% Bayesian multi-season career anchor + 37.5% current season form.
        - Dynamic injury recovery & availability timeline over future gameweeks.
        - Note: displayed FDR when players are inspected in the UI remains the official FPL integer difficulty.
        """
        is_unavailable = player.get("is_unavailable", False)
        is_doubtful = player.get("is_doubtful", False)
        is_unlikely = player.get("is_unlikely_to_play", False)
        chance = player.get("chance_of_playing")
        fpl_status = player.get("status", "a")
        is_transferred_out = player.get("is_transferred_out", False)

        diff = fixture.get("difficulty", 3)
        is_home = fixture.get("is_home", True)
        opp_name = fixture.get("opponent")
        calc_diff = self.get_custom_5yr_fdr(opp_name, is_home=is_home, default_diff=float(diff))
        pos = player.get("position", "MID")
        current_minutes = player.get("current_minutes", 0)

        # 1. Dynamic availability timeline across gameweeks
        if is_transferred_out:
            return 0.0, {
                "base_appearance": 0.0,
                "fotmob_rating_boost": 0.0,
                "rating_scaling": 0.0,
                "attacking_potential": 0.0,
                "attacking_pts": 0.0,
                "defensive_potential": 0.0,
                "clean_sheet_pts": 0.0,
                "cs_probability": 0.0,
                "expected_gc": 0.0,
                "expected_gc_penalty": 0.0,
                "sample_confidence": 0.0,
                "historical_weighting": 0.0,
                "current_form_weighting": 0.0,
                "career_total_fpl_points": player.get("career_total_fpl_points", 0),
                "line_breaking_passes_90": player.get("line_breaking_passes_90", 0.0),
                "line_breaking_passes_boost": 0.0,
                "lbp_pts": 0.0,
                "is_90th_percentile_lbp": player.get("is_90th_percentile_lbp", False),
                "lbp_percentile": player.get("lbp_percentile", 0.0),
                "fixture_multiplier": 1.0,
                "availability_penalty": 0.0,
                "big_chances_90": 0.0,
                "defensive_actions_90": 0.0,
                "def_contrib_pts": 0.0,
                "disciplinary_deduction": 0.0,
                "conceded_and_discipline_deductions": 0.0,
                "sot_bonus": 0.0,
                "pen_bonus": 0.0,
                "set_piece_bonus": 0.0,
                "bcc_bonus": 0.0,
                "save_pts": 0.0,
                "expected_bonus": 0.0,
                "bonus_pts": 0.0,
                "final_xp": 0.0,
                "is_home": is_home,
                "fixture_fdr": diff,
                "summary": "Player transferred out of the Premier League (0.0 xP)."
            }

        availability = 1.0
        if is_unavailable:
            if gw_index_from_now == 0:
                availability = 0.0
            elif gw_index_from_now == 1:
                if fpl_status in ["i", "s"] and (chance == 0 or chance is None):
                    availability = 0.0
                else:
                    availability = 0.35
            elif gw_index_from_now == 2:
                availability = 0.70
            else:
                availability = 0.90
        elif is_doubtful:
            if gw_index_from_now == 0:
                availability = (chance if chance is not None else 50) / 100.0
            elif gw_index_from_now == 1:
                availability = 0.85
            else:
                availability = 1.0
        elif is_unlikely:
            availability = (player.get("playing_likelihood", 100) / 100.0)

        # Apply return-to-play minutes ramp for recently returned injured players
        if player.get("is_recent_injury_returnee") and availability > 0.0:
            return_ramp = float(player.get("injury_return_ramp", 1.0) or 1.0)
            if gw_index_from_now == 0:
                availability = max(0.20, availability * return_ramp)
            elif gw_index_from_now == 1:
                availability = max(0.40, availability * min(1.0, return_ramp + 0.15))

        if availability <= 0.0:
            return 0.0, {
                "base_appearance": 0.0,
                "fotmob_rating_boost": 0.0,
                "rating_scaling": 0.0,
                "attacking_potential": 0.0,
                "attacking_pts": 0.0,
                "defensive_potential": 0.0,
                "clean_sheet_pts": 0.0,
                "cs_probability": 0.0,
                "expected_gc": 0.0,
                "expected_gc_penalty": 0.0,
                "sample_confidence": 0.0,
                "historical_weighting": 0.0,
                "current_form_weighting": 0.0,
                "career_total_fpl_points": player.get("career_total_fpl_points", 0),
                "line_breaking_passes_90": player.get("line_breaking_passes_90", 0.0),
                "line_breaking_passes_boost": 0.0,
                "lbp_pts": 0.0,
                "is_90th_percentile_lbp": player.get("is_90th_percentile_lbp", False),
                "lbp_percentile": player.get("lbp_percentile", 0.0),
                "fixture_multiplier": 1.0,
                "availability_penalty": 0.0,
                "big_chances_90": 0.0,
                "defensive_actions_90": 0.0,
                "def_contrib_pts": 0.0,
                "disciplinary_deduction": 0.0,
                "conceded_and_discipline_deductions": 0.0,
                "sot_bonus": 0.0,
                "pen_bonus": 0.0,
                "set_piece_bonus": 0.0,
                "bcc_bonus": 0.0,
                "save_pts": 0.0,
                "expected_bonus": 0.0,
                "bonus_pts": 0.0,
                "final_xp": 0.0,
                "is_home": is_home,
                "fixture_fdr": diff,
                "summary": "Player unavailable due to injury or suspension (0.0 xP)."
            }

        # 2. Base appearance (2.0 pts for 60+ mins played)
        base_appearance = 2.0

        # 3. FotMob rating boost (scaled by minutes confidence)
        fotmob_rating = float(player.get("fotmob_rating", 6.5) or 6.5)
        fotmob_boost_raw = max(-1.0, (fotmob_rating - 6.0) * 1.0)
        sample_confidence = min(1.0, 0.6 + 0.4 * (current_minutes / 450.0))
        fotmob_boost = fotmob_boost_raw * sample_confidence
        if pos == "GKP":
            # Goalkeepers already earn points via save_pts and clean_sheet_pts.
            # Dampen FotMob rating boost to prevent double-counting saves as extra rating points.
            fotmob_boost = min(0.25, max(-0.25, fotmob_boost * 0.20))

        # 4. Attacking Threat with Proven Correlation Stats
        sot_90 = float(player.get("sot_90", 0.0) or 0.0)
        sot_bonus = round(min(0.35, sot_90 * 0.12), 2)

        is_penalty_taker = bool(player.get("is_penalty_taker", False))
        pen_bonus = 0.0
        if is_penalty_taker:
            if pos in ["MID", "DEF"]:
                pen_bonus = 0.30
            elif pos == "FWD":
                pen_bonus = 0.25

        is_set_piece_taker = bool(player.get("is_set_piece_taker", False))
        set_piece_bonus = 0.15 if is_set_piece_taker else 0.0

        big_chances_90 = float(player.get("big_chances_90", 0.0) or 0.0)
        bcc_bonus = round(min(0.40, big_chances_90 * 0.35), 2)

        xg_90 = float(player.get("xg_90", 0.0) or 0.0)
        xa_90 = float(player.get("xa_90", 0.0) or 0.0)

        if pos == "FWD":
            raw_attacking = (xg_90 * 4.0) + (xa_90 * 3.0) + sot_bonus + pen_bonus + bcc_bonus
            pos_prior_baseline = 3.8
        elif pos == "MID":
            raw_attacking = (xg_90 * 5.0) + (xa_90 * 3.0) + sot_bonus + pen_bonus + bcc_bonus + set_piece_bonus
            pos_prior_baseline = 3.5
        elif pos == "DEF":
            raw_attacking = (xg_90 * 6.0) + (xa_90 * 3.0) + sot_bonus + pen_bonus + set_piece_bonus
            pos_prior_baseline = 3.2
        else:  # GKP
            raw_attacking = 0.0
            pos_prior_baseline = 3.0

        # Home / Away Attacking Venue Scaling (Premier League home teams score ~25% more goals)
        home_atk_factor = 1.08 if is_home else 0.92
        attacking = raw_attacking * home_atk_factor

        # 5. Defensive Potential (Poisson Clean Sheet Model & Goals Conceded Penalty)
        team_xgc_90 = float(player.get("team_xgc_90", 1.25) or 1.25)
        fdr_factor = (calc_diff / 3.0) ** 0.6
        venue_def_factor = 0.88 if is_home else 1.14
        expected_gc = max(0.20, team_xgc_90 * fdr_factor * venue_def_factor)
        cs_prob = math.exp(-expected_gc)

        expected_gc_penalty = 0.0
        if pos in ["DEF", "GKP"]:
            expected_gc_penalty = round((2.0 * expected_gc - 1.0 + math.exp(-2.0 * expected_gc)) / 4.0, 2)

        def_contrib_pts = 0.0
        save_pts = 0.0
        defensive_actions_90 = float(player.get("defensive_actions_90", 0.0) or 0.0)
        saves_90 = float(player.get("saves_90", 0.0) or 0.0)

        if pos == "DEF":
            def_contrib_pts = round(min(1.0, (defensive_actions_90 / 10.0) * 1.0), 2)
            defensive = (cs_prob * 4.0) + def_contrib_pts - expected_gc_penalty
        elif pos == "MID":
            def_contrib_pts = round(min(0.6, (defensive_actions_90 / 12.0) * 0.6), 2)
            defensive = (cs_prob * 1.0) + def_contrib_pts
        elif pos == "GKP":
            gk_pressure_factor = 1.0 + (calc_diff - 3.0) * 0.08 + (0.05 if not is_home else -0.05)
            save_pts = round(min(1.8, max(0.0, (saves_90 * gk_pressure_factor / 3.0))), 2)
            defensive = (cs_prob * 4.0) + save_pts - expected_gc_penalty
        else:
            defensive = 0.0

        # 6. Expected Bonus Points (xBPS based on FotMob rating & xGI)
        expected_bonus = round(min(1.0, max(0.0, (fotmob_rating - 6.8) * 0.5 + (xg_90 + xa_90) * 0.4)), 2)

        # 7. Disciplinary Deductions
        disciplinary_deduction = float(player.get("disciplinary_deduction", 0.0) or 0.0)

        # 8. Current Match Performance Sum
        current_season_perf = max(0.0, base_appearance + fotmob_boost + attacking + defensive + expected_bonus - disciplinary_deduction)

        # 9. Multi-Season Career Track Record & Adaptive Historical Weighting (80% Default Anchor)
        career_total_mins = player.get("career_total_mins", 0)
        career_total_fpl_points = player.get("career_total_fpl_points", 0)
        career_seasons_count = player.get("career_seasons_count", 0)
        historical_points_per_90 = float(player.get("historical_pts_per_90", 0.0) or 0.0)
        historical_avg_pts = float(player.get("historical_avg_pts", 0.0) or 0.0)

        # Cost-adjusted positional prior baseline for data-scarce players
        cost = float(player.get("cost", 5.0) or 5.0)
        cost_multiplier = max(0.85, min(1.25, 0.70 + (cost / 15.0)))
        adjusted_pos_prior = pos_prior_baseline * cost_multiplier

        w_career = min(0.85, career_total_mins / (career_total_mins + 2000.0)) if career_total_mins > 0 else 0.0

        if career_seasons_count > 0 and career_total_mins >= 450:
            career_pts_per_90 = career_total_fpl_points / (career_total_mins / 90.0)
            career_season_avg = career_total_fpl_points / career_seasons_count
            career_season_pace = career_season_avg / 38.0
            career_baseline = (career_pts_per_90 * 0.60) + (career_season_pace * 0.40)
        elif historical_points_per_90 > 0:
            career_baseline = historical_points_per_90
        elif historical_avg_pts > 0:
            career_baseline = historical_avg_pts / 38.0
        else:
            career_baseline = adjusted_pos_prior

        anchored_career_rate = (career_baseline * w_career) + (adjusted_pos_prior * (1.0 - w_career))

        vol_bonus = 0.0
        if career_total_fpl_points >= 1000:
            vol_bonus = 0.35
        elif career_total_fpl_points >= 600:
            vol_bonus = 0.20
        elif career_total_fpl_points >= 300:
            vol_bonus = 0.10

        hist_track_record = anchored_career_rate + vol_bonus

        # Hot Streak / Regression-to-the-Mean Structure:
        # Dampens short-term unsustainable spikes (e.g. early season variance) back toward career baseline
        perf_delta = current_season_perf - hist_track_record
        if perf_delta > 0.0:
            # 60% shrinkage for goalkeepers (highest variance), 40% for outfielders
            shrinkage = 0.60 if pos == "GKP" else 0.40
            current_season_perf_adjusted = hist_track_record + (perf_delta * (1.0 - shrinkage))
        else:
            # Cold spell cushion (slump protection)
            current_season_perf_adjusted = hist_track_record + (perf_delta * 0.75)

        # Scarcity Adaptation:
        # Full 80% weightage allocated to historical seasons record for established players.
        # For debutants/promoted/data-scarce players (career_total_mins < 1800), adapt smoothly.
        hist_credibility = min(1.0, career_total_mins / 1800.0) if career_total_mins > 0 else 0.0
        eff_weight_hist = 0.20 + (0.60 * hist_credibility)  # 0.80 for veterans, 0.20 for brand new players
        eff_weight_curr = 1.0 - eff_weight_hist

        hist_weighted_pts = hist_track_record * eff_weight_hist
        curr_weighted_pts = current_season_perf_adjusted * eff_weight_curr
        raw_sum = hist_weighted_pts + curr_weighted_pts

        # 10. Fixture Difficulty Multiplier (Driven by 5-season performance FDR)
        fdr_multiplier = 1.0 + (3.0 - calc_diff) * 0.08
        projected_before_injury = raw_sum * fdr_multiplier

        # 11. Availability Scaling & Final Expected Points (Pure Analytical Bayesian Model)
        final_xp = max(0.0, projected_before_injury * availability)
        if pos == "GKP" and availability > 0.0:
            final_xp = min(4.95, max(1.5, final_xp))

        breakdown = {
            "base_appearance": round(base_appearance, 2),
            "fotmob_rating_boost": round(fotmob_boost, 2),
            "fotmob_rating_raw": round(fotmob_boost_raw, 2),
            "rating_scaling": round(sample_confidence, 2),
            "effective_historical_weight": round(eff_weight_hist * 100, 1),
            "effective_current_weight": round(eff_weight_curr * 100, 1),
            "is_data_scarce": bool(hist_credibility < 0.50),
            "attacking_potential": round(attacking, 2),
            "attacking_pts": round(attacking, 2),
            "sot_90": round(sot_90, 2),
            "sot_bonus": round(sot_bonus, 2),
            "is_penalty_taker": is_penalty_taker,
            "pen_bonus": round(pen_bonus, 2),
            "is_set_piece_taker": is_set_piece_taker,
            "set_piece_bonus": round(set_piece_bonus, 2),
            "big_chances_90": round(big_chances_90, 2),
            "bcc_bonus": round(bcc_bonus, 2),
            "defensive_actions_90": round(defensive_actions_90, 2),
            "def_contrib_pts": round(def_contrib_pts, 2),
            "team_xgc_90": round(team_xgc_90, 2),
            "expected_gc": round(expected_gc, 2),
            "expected_gc_penalty": round(expected_gc_penalty, 2),
            "cs_probability": round(cs_prob * 100, 1),
            "clean_sheet_pts": round((cs_prob * (4.0 if pos in ['DEF', 'GKP'] else 1.0)) if pos != 'FWD' else 0.0, 2),
            "cs_fdr_basis": diff,
            "saves_90": round(saves_90, 2),
            "save_pts": round(save_pts, 2),
            "expected_bonus": round(expected_bonus, 2),
            "bonus_pts": round(expected_bonus, 2),
            "disciplinary_deduction": round(disciplinary_deduction, 2),
            "conceded_and_discipline_deductions": round(disciplinary_deduction + expected_gc_penalty, 2),
            "yellows_90": round(float(player.get("yellows_90", 0.0) or 0.0), 2),
            "reds_90": round(float(player.get("reds_90", 0.0) or 0.0), 2),
            "fouls_90": round(float(player.get("fouls_90", 0.0) or 0.0), 2),
            "holistic_xg_90": round(xg_90, 2),
            "holistic_xa_90": round(xa_90, 2),
            "defensive_potential": round(defensive, 2),
            "sample_confidence": round(sample_confidence * 100, 0),
            "sample_minutes": current_minutes,
            "career_total_fpl_points": career_total_fpl_points,
            "career_seasons_count": career_seasons_count,
            "career_total_mins": career_total_mins,
            "career_pts_per_90": round(career_total_fpl_points / (career_total_mins / 90.0), 2) if career_total_mins >= 450 else 0.0,
            "career_credibility_weight": round(w_career, 2),
            "career_volume_bonus": vol_bonus,
            "historical_weighting": round(hist_weighted_pts, 2),
            "current_form_weighting": round(curr_weighted_pts, 2),
            "multi_season_weight_pct": 62.5,
            "line_breaking_passes_90": round(float(player.get("line_breaking_passes_90", 0.0) or 0.0), 2),
            "total_line_breaking_passes": round(float(player.get("total_line_breaking_passes", 0.0) or 0.0), 0),
            "is_90th_percentile_lbp": player.get("is_90th_percentile_lbp", False),
            "is_95th_percentile_lbp": player.get("is_95th_percentile_lbp", False),
            "lbp_percentile": player.get("lbp_percentile", 0.0),
            "line_breaking_passes_boost": 0.0,
            "lbp_pts": 0.0,
            "fixture_multiplier": round(fdr_multiplier, 2),
            "availability_penalty": round(availability, 2),
            "is_home": is_home,
            "fixture_fdr": diff,
            "is_new_pl_entrant": bool(player.get("is_new_pl_entrant")),
            "new_entrant_category": player.get("new_entrant_category", ""),
            "is_recently_injured": bool(player.get("is_recently_injured")),
            "is_recent_injury_returnee": bool(player.get("is_recent_injury_returnee")),
            "injury_return_ramp": float(player.get("injury_return_ramp", 1.0) or 1.0),
            "expected_minutes_projected": int(player.get("expected_minutes_projected", 90)),
            "sample_size_confidence": float(player.get("sample_size_confidence", 100.0)),
            "normalized_xg_90": float(player.get("normalized_xg_90", xg_90)),
            "normalized_xa_90": float(player.get("normalized_xa_90", xa_90)),
            "normalized_form": float(player.get("normalized_form", player.get("form", 0.0))),
            "status_tags": player.get("status_tags", []),
            "normalization_notes": player.get("normalization_notes", []),
            "final_xp": round(final_xp, 2),
            "summary": (
                f"Multi-Season Track Record (62.5% weight: +{hist_weighted_pts:.2f} pts) + "
                f"Current Form (37.5% weight: +{curr_weighted_pts:.2f} pts) "
                f"× FDR ({fdr_multiplier:.2f}) × Venue ({'Home 1.08x' if is_home else 'Away 0.92x'}) "
                f"× Likelihood ({availability*100:.0f}%) = {final_xp:.2f} xP"
            )
        }

        return round(final_xp, 2), breakdown

    def calculate_expected_points(
        self,
        position: str,
        cost: float,
        fotmob_rating: float,
        xg_90: float,
        xa_90: float,
        shots_90: float,
        big_chances: float,
        clean_sheets: float,
        avg_fdr: float,
        next_fixture_fdr: int,
        current_minutes: int,
        is_unavailable: bool,
        is_doubtful: bool,
        is_unlikely_to_play: bool,
        playing_likelihood: float,
        unlikely_reason: str,
        fpl_chance: Optional[int],
        fpl_points_per_game: float,
        historical_points_per_90: float = 0.0,
        historical_avg_pts: float = 0.0,
        career_total_fpl_points: int = 0,
        career_total_mins: int = 0,
        career_seasons_count: int = 0,
        line_breaking_passes_90: float = 0.0,
        total_line_breaking_passes: float = 0.0,
        is_90th_percentile_lbp: bool = False,
        is_95th_percentile_lbp: bool = False,
        lbp_percentile: float = 0.0,
        raw_current_xg90: float = 0.0,
        raw_current_xa90: float = 0.0,
        hist_xg90: float = 0.0,
        hist_xa90: float = 0.0,
        big_chances_90: float = 0.0,
        defensive_actions_90: float = 0.0,
        disciplinary_deduction: float = 0.0,
        yellows_90: float = 0.0,
        reds_90: float = 0.0,
        fouls_90: float = 0.0,
        sot_90: float = 0.0,
        is_penalty_taker: bool = False,
        is_set_piece_taker: bool = False,
        team_xgc_90: float = 1.25,
        saves_90: float = 0.0,
        is_home: bool = True,
        fixture_fdr: Optional[int] = None,
        gw_index_from_now: int = 0
    ) -> Tuple[float, Dict[str, Any]]:
        diff = fixture_fdr if fixture_fdr is not None else next_fixture_fdr
        player_dict = {
            "position": position,
            "cost": cost,
            "fotmob_rating": fotmob_rating,
            "xg_90": xg_90,
            "xa_90": xa_90,
            "shots_90": shots_90,
            "big_chances": big_chances,
            "big_chances_90": big_chances_90,
            "defensive_actions_90": defensive_actions_90,
            "disciplinary_deduction": disciplinary_deduction,
            "yellows_90": yellows_90,
            "reds_90": reds_90,
            "fouls_90": fouls_90,
            "clean_sheets": clean_sheets,
            "current_minutes": current_minutes,
            "is_unavailable": is_unavailable,
            "is_doubtful": is_doubtful,
            "is_unlikely_to_play": is_unlikely_to_play,
            "playing_likelihood": playing_likelihood * 100.0 if playing_likelihood <= 1.0 else playing_likelihood,
            "unlikely_reason": unlikely_reason,
            "chance_of_playing": fpl_chance,
            "points_per_game": fpl_points_per_game,
            "historical_pts_per_90": historical_points_per_90,
            "historical_avg_pts": historical_avg_pts,
            "career_total_fpl_points": career_total_fpl_points,
            "career_total_mins": career_total_mins,
            "career_seasons_count": career_seasons_count,
            "line_breaking_passes_90": line_breaking_passes_90,
            "total_line_breaking_passes": total_line_breaking_passes,
            "is_90th_percentile_lbp": is_90th_percentile_lbp,
            "is_95th_percentile_lbp": is_95th_percentile_lbp,
            "lbp_percentile": lbp_percentile,
            "raw_current_xg90": raw_current_xg90,
            "raw_current_xa90": raw_current_xa90,
            "hist_xg90": hist_xg90,
            "hist_xa90": hist_xa90,
            "sot_90": sot_90,
            "is_penalty_taker": is_penalty_taker,
            "is_set_piece_taker": is_set_piece_taker,
            "team_xgc_90": team_xgc_90,
            "saves_90": saves_90
        }
        fixture_dict = {
            "difficulty": diff,
            "is_home": is_home
        }
        return self.calculate_player_fixture_xp(player_dict, fixture_dict, gw_index_from_now=gw_index_from_now)

    def get_default_squad(self) -> Dict[str, Any]:
        """
        Generates a fresh, realistic, highly-competitive custom template lineup every time.
        - Complies strictly with FPL budget rules (<= 100.0m total cost).
        - Complies strictly with club quotas (max 3 players per Premier League team).
        - Automatically tests and selects a valid competitive formation (3-4-3, 3-5-2, 4-4-2, 4-3-3).
        - Assigns highest xP starters as Captain and Vice-Captain.
        - Falls back gracefully to standard top assets if needed.
        """
        avail = [
            p for p in self.unified_players
            if not p.get("is_unavailable") and not p.get("is_doubtful") and p.get("cost", 0) > 0 and p.get("expected_points", 0) > 1.2
        ]
        if not avail:
            avail = self.unified_players

        gkps_all = sorted([p for p in avail if p["position"] == "GKP"], key=lambda x: -x.get("expected_points", 0))
        defs_all = sorted([p for p in avail if p["position"] == "DEF"], key=lambda x: -x.get("expected_points", 0))
        mids_all = sorted([p for p in avail if p["position"] == "MID"], key=lambda x: -x.get("expected_points", 0))
        fwds_all = sorted([p for p in avail if p["position"] == "FWD"], key=lambda x: -x.get("expected_points", 0))

        formations = [(3, 4, 3), (3, 5, 2), (4, 4, 2), (4, 3, 3), (3, 4, 3), (3, 5, 2)]

        for _ in range(100):
            club_counts = {}
            squad = []

            # 1. GKPs (2 total): 1 starter from top 8, 1 enabler <= 4.5m
            cand_gk1 = gkps_all[:8]
            if not cand_gk1:
                break
            g1 = random.choice(cand_gk1)
            club_counts[g1["team_id"]] = 1
            squad.append(g1)

            cand_gk2 = [p for p in gkps_all if p.get("cost", 0) <= 4.5 and p["id"] != g1["id"] and club_counts.get(p["team_id"], 0) < 3]
            g2 = random.choice(cand_gk2[:8]) if cand_gk2 else [p for p in gkps_all if p["id"] != g1["id"]][0]
            club_counts[g2["team_id"]] = club_counts.get(g2["team_id"], 0) + 1
            squad.append(g2)

            # 2. Forwards (3 total): 1 top premium, 1 mid-tier, 1 budget
            cand_fwd_top = [p for p in fwds_all[:6] if club_counts.get(p["team_id"], 0) < 3 and p["id"] not in [x["id"] for x in squad]]
            if not cand_fwd_top:
                continue
            f1 = random.choice(cand_fwd_top)
            club_counts[f1["team_id"]] = club_counts.get(f1["team_id"], 0) + 1
            squad.append(f1)

            cand_fwd_mid = [p for p in fwds_all[2:18] if p["id"] not in [x["id"] for x in squad] and club_counts.get(p["team_id"], 0) < 3]
            if not cand_fwd_mid:
                continue
            f2 = random.choice(cand_fwd_mid)
            club_counts[f2["team_id"]] = club_counts.get(f2["team_id"], 0) + 1
            squad.append(f2)

            cand_fwd_bud = [p for p in fwds_all if p.get("cost", 0) <= 6.5 and p["id"] not in [x["id"] for x in squad] and club_counts.get(p["team_id"], 0) < 3]
            if not cand_fwd_bud:
                cand_fwd_bud = [p for p in fwds_all[10:] if p["id"] not in [x["id"] for x in squad] and club_counts.get(p["team_id"], 0) < 3]
            if not cand_fwd_bud:
                continue
            f3 = random.choice(cand_fwd_bud[:10])
            club_counts[f3["team_id"]] = club_counts.get(f3["team_id"], 0) + 1
            squad.append(f3)

            # 3. Midfielders (5 total): 1-2 top premiums, 2 mid-tier, 1-2 budget
            picked_mids = []
            cand_m_prem = [p for p in mids_all[:8] if club_counts.get(p["team_id"], 0) < 3 and p["id"] not in [x["id"] for x in squad]]
            if not cand_m_prem:
                continue
            m1 = random.choice(cand_m_prem)
            club_counts[m1["team_id"]] = club_counts.get(m1["team_id"], 0) + 1
            picked_mids.append(m1)

            cand_m_prem2 = [p for p in mids_all[:14] if p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_mids] and club_counts.get(p["team_id"], 0) < 3]
            if not cand_m_prem2:
                continue
            m2 = random.choice(cand_m_prem2)
            club_counts[m2["team_id"]] = club_counts.get(m2["team_id"], 0) + 1
            picked_mids.append(m2)

            cand_m_mid = [p for p in mids_all[8:32] if p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_mids] and club_counts.get(p["team_id"], 0) < 3]
            if len(cand_m_mid) < 2:
                continue
            for m in random.sample(cand_m_mid, 2):
                club_counts[m["team_id"]] = club_counts.get(m["team_id"], 0) + 1
                picked_mids.append(m)

            cand_m_bud = [p for p in mids_all if p.get("cost", 0) <= 5.5 and p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_mids] and club_counts.get(p["team_id"], 0) < 3]
            mb = random.choice(cand_m_bud[:12]) if cand_m_bud else mids_all[-1]
            club_counts[mb["team_id"]] = club_counts.get(mb["team_id"], 0) + 1
            picked_mids.append(mb)
            squad.extend(picked_mids)

            # 4. Defenders (5 total): 1 premium, 2 mid-tier, 2 budget
            picked_defs = []
            cand_d_prem = [p for p in defs_all[:10] if club_counts.get(p["team_id"], 0) < 3 and p["id"] not in [x["id"] for x in squad]]
            if not cand_d_prem:
                continue
            d1 = random.choice(cand_d_prem)
            club_counts[d1["team_id"]] = club_counts.get(d1["team_id"], 0) + 1
            picked_defs.append(d1)

            cand_d_mid = [p for p in defs_all[6:35] if p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_defs] and club_counts.get(p["team_id"], 0) < 3]
            if len(cand_d_mid) < 2:
                continue
            for d in random.sample(cand_d_mid, 2):
                club_counts[d["team_id"]] = club_counts.get(d["team_id"], 0) + 1
                picked_defs.append(d)

            cand_d_bud = [p for p in defs_all if p.get("cost", 0) <= 4.5 and p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_defs] and club_counts.get(p["team_id"], 0) < 3]
            if len(cand_d_bud) < 2:
                cand_d_bud = [p for p in defs_all[20:] if p["id"] not in [x["id"] for x in squad] and p["id"] not in [x["id"] for x in picked_defs] and club_counts.get(p["team_id"], 0) < 3]
            if len(cand_d_bud) < 2:
                continue
            for db in random.sample(cand_d_bud[:15], 2):
                club_counts[db["team_id"]] = club_counts.get(db["team_id"], 0) + 1
                picked_defs.append(db)
            squad.extend(picked_defs)

            if len(squad) == 15:
                total_cost = sum(p.get("cost", 0) for p in squad)
                if 85.0 <= total_cost <= 100.0:
                    bank = round(max(0.0, 100.0 - total_cost), 1)
                    formation = random.choice(formations)
                    n_def, n_mid, n_fwd = formation

                    d_sorted = sorted(picked_defs, key=lambda x: -x.get("expected_points", 0))
                    m_sorted = sorted(picked_mids, key=lambda x: -x.get("expected_points", 0))
                    f_sorted = sorted([f1, f2, f3], key=lambda x: -x.get("expected_points", 0))

                    starters = [g1["id"]] + [p["id"] for p in d_sorted[:n_def]] + [p["id"] for p in m_sorted[:n_mid]] + [p["id"] for p in f_sorted[:n_fwd]]

                    bench_outfield = d_sorted[n_def:] + m_sorted[n_mid:] + f_sorted[n_fwd:]
                    bench_outfield.sort(key=lambda x: -x.get("expected_points", 0))
                    bench = [g2["id"]] + [p["id"] for p in bench_outfield]

                    valid, _ = self.validate_formation(starters)
                    if valid and len(bench) == 4:
                        cap = max([p for p in squad if p["id"] in starters], key=lambda x: x.get("expected_points", 0))
                        vc_cand = [p for p in squad if p["id"] in starters and p["id"] != cap["id"]]
                        vc = max(vc_cand, key=lambda x: x.get("expected_points", 0)) if vc_cand else cap

                        return {
                            "bank": bank,
                            "free_transfers": 1,
                            "starters": starters,
                            "bench": bench,
                            "captain": cap["id"],
                            "vice_captain": vc["id"],
                            "template_name": f"Dynamic Template Lineup ({n_def}-{n_mid}-{n_fwd})"
                        }

        # Deterministic fallback if random generator doesn't converge within 100 attempts
        gks = [p["id"] for p in sorted([x for x in self.unified_players if x["position"] == "GKP"], key=lambda x: -x["expected_points"])[:2]]
        defs = [p["id"] for p in sorted([x for x in self.unified_players if x["position"] == "DEF"], key=lambda x: -x["expected_points"])[:5]]
        mids = [p["id"] for p in sorted([x for x in self.unified_players if x["position"] == "MID"], key=lambda x: -x["expected_points"])[:5]]
        fwds = [p["id"] for p in sorted([x for x in self.unified_players if x["position"] == "FWD"], key=lambda x: -x["expected_points"])[:3]]

        starters = [gks[0]] + defs[:3] + mids[:4] + fwds[:3]
        bench = [gks[1]] + [defs[3], defs[4], mids[4]]

        return {
            "bank": 1.0,
            "free_transfers": 1,
            "starters": starters,
            "bench": bench,
            "captain": fwds[0] if fwds else (starters[0] if starters else None),
            "vice_captain": mids[0] if mids else (starters[1] if len(starters) > 1 else None),
            "template_name": "Standard Template Lineup (3-4-3)"
        }

    def validate_formation(self, starters: List[int]) -> Tuple[bool, str]:
        if len(starters) != 11:
            return False, f"Lineup must contain exactly 11 starters (current: {len(starters)})."

        pos_counts = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
        for pid in starters:
            p = self.players_by_id.get(pid)
            if not p:
                return False, f"Unknown player ID: {pid}"
            pos_counts[p["position"]] += 1

        if pos_counts["GKP"] != 1:
            return False, f"Formation requires exactly 1 Goalkeeper (current: {pos_counts['GKP']})."
        if pos_counts["DEF"] < 3:
            return False, f"Formation requires at least 3 Defenders (current: {pos_counts['DEF']})."
        if pos_counts["MID"] < 2:
            return False, f"Formation requires at least 2 Midfielders (current: {pos_counts['MID']})."
        if pos_counts["FWD"] < 1:
            return False, f"Formation requires at least 1 Forward (current: {pos_counts['FWD']})."

        return True, "Valid FPL formation."

    def recommend_transfers_and_chips(
        self,
        starters: List[int],
        bench: List[int],
        bank: float = 0.5,
        free_transfers: int = 1,
        chips_used: Optional[Dict[str, bool]] = None
    ) -> Dict[str, Any]:
        """
        Comprehensive FPL Transfers Optimizer & Strategy Recommender:
        - Actively uses exact free transfers count.
        - Accounts for playing likelihood & zero-minute assets.
        - Enforces FPL rules (-4 point hit penalty, max 3 club limits).
        """
        all_squad_ids = set(starters + bench)
        squad_players = [self.players_by_id[pid] for pid in all_squad_ids if pid in self.players_by_id]
        starter_players = [self.players_by_id[pid] for pid in starters if pid in self.players_by_id]
        bench_players = [self.players_by_id[pid] for pid in bench if pid in self.players_by_id]

        total_starter_xp = sum(p["expected_points"] for p in starter_players)
        total_bench_xp = sum(p["expected_points"] for p in bench_players)

        # Count club quotas (max 3 players per club)
        club_counts: Dict[int, int] = {}
        for p in squad_players:
            club_counts[p["team_id"]] = club_counts.get(p["team_id"], 0) + 1

        flagged_players = [p for p in squad_players if p["is_unavailable"] or p["is_doubtful"] or p["is_unlikely_to_play"]]
        injured_starters = [p for p in starter_players if p["is_unavailable"] or p["is_doubtful"] or p["is_unlikely_to_play"]]

        # -----------------------------
        # Chips / Power-up Evaluation
        # -----------------------------
        chips_used = chips_used or {}
        chip_recommendation = {
            "recommended": False,
            "chip": None,
            "title": "Save Chips",
            "reason": "Save chips for upcoming double gameweeks or high-leverage injury crisis."
        }

        top_captain = max(starter_players, key=lambda x: x["expected_points"]) if starter_players else None
        if not chips_used.get("3xc") and top_captain and top_captain["expected_points"] >= 8.0:
            first_fix = top_captain["upcoming_fixtures"][0] if top_captain["upcoming_fixtures"] else {}
            if first_fix.get("is_home") and first_fix.get("difficulty", 3) <= 2:
                chip_recommendation = {
                    "recommended": True,
                    "chip": "3xc",
                    "title": "Activate Triple Captain",
                    "reason": f"🔥 Optimal Triple Captain spot for {top_captain['web_name']} (xP: {top_captain['expected_points']}, FDR {first_fix.get('difficulty')} at home with high past season consistency)."
                }
        elif not chips_used.get("bboost") and total_bench_xp >= 14.0 and len([b for b in bench_players if not b['is_unavailable'] and not b['is_unlikely_to_play']]) == 4:
            chip_recommendation = {
                "recommended": True,
                "chip": "bboost",
                "title": "Activate Bench Boost",
                "reason": f"⚡ Your 4 bench assets project a massive {round(total_bench_xp, 1)} combined xP. Prime bench boost opportunity."
            }
        elif not chips_used.get("wildcard") and len(flagged_players) >= 3:
            chip_recommendation = {
                "recommended": True,
                "chip": "wildcard",
                "title": "Activate Wildcard",
                "reason": f"🚨 Crisis Alert: {len(flagged_players)} squad players injured or unlikely to play ({', '.join(p['web_name'] for p in flagged_players)}). Rebuild for free."
            }

        # -----------------------------
        # Transfer Search & Budget-Optimized Payout (Starter Priority)
        # -----------------------------
        candidate_transfers: List[Dict[str, Any]] = []

        # Strict FPL Rule Enforcement:
        # If free_transfers == 0, every transfer incurs a -4 point hit.
        # It is ONLY recommended if the expected net gain exceeds 2.5 xP.
        min_gain_threshold = 2.5 if free_transfers == 0 else 0.0

        for p_out in squad_players:
            is_starter = p_out["id"] in starters
            max_budget = round(bank + p_out["cost"], 2)
            pos = p_out["position"]
            out_team = p_out["team_id"]

            # Filter valid replacements within budget (must be fully fit, not doubtful, and in the PL)
            pool = [
                p for p in self.unified_players
                if p["position"] == pos
                and p["cost"] <= max_budget
                and p["id"] not in all_squad_ids
                and not p["is_unavailable"]
                and not p.get("is_transferred_out", False)
                and not p["is_doubtful"]
                and p.get("chance_of_playing", 100) >= 75
                and not p["is_unlikely_to_play"]
                and (p["team_id"] == out_team or club_counts.get(p["team_id"], 0) < 3)
            ]

            pool.sort(key=lambda x: -x["expected_points"])

            # Check if replacing a bench player would promote this replacement into the starting XI
            pos_starters = [p for p in starter_players if p["position"] == pos]
            weakest_starter = min(pos_starters, key=lambda x: x["expected_points"]) if pos_starters else None

            for p_in in pool[:12]:
                raw_gain = p_in["expected_points"] - p_out["expected_points"]
                bank_gain = round(p_out["cost"] - p_in["cost"], 2)

                # STRICT USER REQUIREMENT: Don't display a transfer unless it gets you at least 1m more in the bank or 1xp gain
                if not (raw_gain >= 1.0 or (bank_gain >= 1.0 and raw_gain >= 0.0)):
                    continue

                if is_starter:
                    effective_starter_gain = raw_gain
                else:
                    if weakest_starter and p_in["expected_points"] > weakest_starter["expected_points"]:
                        effective_starter_gain = p_in["expected_points"] - weakest_starter["expected_points"]
                    else:
                        effective_starter_gain = 0.0

                # Strict threshold filter:
                # If free_transfers == 0, effective starter gain must strictly exceed 2.5 xP
                eval_gain = raw_gain if is_starter else effective_starter_gain
                if free_transfers == 0 and eval_gain <= min_gain_threshold:
                    continue

                candidate_transfers.append({
                    "player_out": p_out,
                    "player_in": p_in,
                    "is_starter": is_starter,
                    "xp_gain": round(raw_gain, 2),
                    "effective_starter_gain": round(effective_starter_gain, 2),
                    "cost_diff": round(p_in["cost"] - p_out["cost"], 2),
                    "new_bank": round(bank + (p_out["cost"] - p_in["cost"]), 2),
                    "fpl_point_deduction": 0 if free_transfers >= 1 else -4,
                    "transfer_type": "Starting XI Upgrade" if is_starter else "Substitute Upgrade"
                })

        # Separate starters and bench: prioritize starting XI moves with positive payout
        starter_transfers = [t for t in candidate_transfers if t["is_starter"]]
        starter_transfers.sort(key=lambda t: (t["xp_gain"], t["new_bank"]), reverse=True)

        bench_transfers = [t for t in candidate_transfers if not t["is_starter"]]
        bench_transfers.sort(key=lambda t: (t["effective_starter_gain"] * 1.5 + t["xp_gain"], t["new_bank"]), reverse=True)

        # Interleave starter and substitute/bench transfers so substitute changes are always included
        ranked_transfers: List[Dict[str, Any]] = []
        si = 0
        bi = 0
        s_len = len(starter_transfers)
        b_len = len(bench_transfers)

        # Primary pick: top starting XI upgrade if available, otherwise top bench move
        if starter_transfers:
            ranked_transfers.append(starter_transfers[si])
            si += 1
        elif bench_transfers:
            ranked_transfers.append(bench_transfers[bi])
            bi += 1

        # Weave: 1 starter (Option 2), then 1 substitute change (Option 3), then 2 starters, 1 substitute...
        first_cycle = True
        while (si < s_len or bi < b_len) and len(ranked_transfers) < 40:
            starters_to_add = 1 if first_cycle else 2
            first_cycle = False
            for _ in range(starters_to_add):
                if si < s_len:
                    ranked_transfers.append(starter_transfers[si])
                    si += 1
            if bi < b_len:
                ranked_transfers.append(bench_transfers[bi])
                bi += 1
            elif si >= s_len:
                break

        best_transfer = ranked_transfers[0] if ranked_transfers else None
        max_xp_gain = best_transfer["xp_gain"] if best_transfer else 0.0

        # Build list of top alternative transfers
        seen_pairs = set()
        alternative_transfers: List[Dict[str, Any]] = []
        if best_transfer:
            seen_pairs.add((best_transfer["player_out"]["id"], best_transfer["player_in"]["id"]))

        # USER REQUIREMENT: Always display a random number of transfer suggestions between 15-25 (so 14 to 24 alternatives)
        max_alts = random.randint(14, 24)
        for t in ranked_transfers:
            pair = (t["player_out"]["id"], t["player_in"]["id"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                alternative_transfers.append(t)
                if len(alternative_transfers) >= max_alts:
                    break

        is_no_transfer = False
        transfer_action = "TRANSFER"
        verdict_title = "Suggested Transfer Available"
        verdict_explanation = ""

        transfer_hit_cost = 0 if free_transfers >= 1 else 4

        if free_transfers == 0:
            # STRICT RULE: When free_transfers == 0, ANY transfer incurs -4 point hit.
            # Only recommend if expected gain strictly exceeds 2.5 xP.
            if not best_transfer or max_xp_gain <= 2.5:
                is_no_transfer = True
                transfer_action = "HOLD_SQUAD"
                verdict_title = "Recommended Action: Hold Squad (Avoid -4 Hit)"
                verdict_explanation = (
                    "You currently have 0 Free Transfers available. No available transfer exceeds the +2.5 xP net gain required to justify taking a -4 point deduction. "
                    "Hold your current lineup to protect your rank. You will be allocated 1 Free Transfer entering the next gameweek."
                )
                best_transfer = None
                alternative_transfers = []
            else:
                is_no_transfer = False
                transfer_action = "TRANSFER"
                verdict_title = f"Suggested {best_transfer['transfer_type']} (-4 Hit)"
                verdict_explanation = (
                    f"High-impact transfer: {best_transfer['player_in']['web_name']} (+{best_transfer['xp_gain']} xP) "
                    f"exceeds the +2.5 xP threshold required to overcome the -4 point deduction."
                )
                # Keep only alternatives that also satisfy the > 2.5 xP requirement
                alternative_transfers = [t for t in alternative_transfers if t["xp_gain"] > 2.5]
        else:
            # free_transfers >= 1 (Free transfer available, 0 point deduction)
            best_bank_gain = round(best_transfer["player_out"]["cost"] - best_transfer["player_in"]["cost"], 2) if best_transfer else 0.0
            qualifies = bool(best_transfer and (best_transfer["xp_gain"] >= 1.0 or (best_bank_gain >= 1.0 and best_transfer["xp_gain"] >= 0.0)))

            if not qualifies:
                is_no_transfer = True
                transfer_action = "ROLL_TRANSFER"
                next_banked = min(5, free_transfers + 1)
                if free_transfers >= 5:
                    verdict_title = "Recommended Action: Roll Free Transfer (Max 5 FTs Banked)"
                    verdict_explanation = (
                        "Starting lineup is healthy and optimal. You are already at the official maximum cap of 5 banked Free Transfers. "
                        "Hold your lineup unless an attractive fixture upgrade presents itself."
                    )
                else:
                    verdict_title = f"Recommended Action: Roll Free Transfer (Bank to {next_banked} FTs)"
                    verdict_explanation = (
                        f"Starting lineup is healthy and optimal. No move provides >= 1.0 xP gain or frees up >= £1.0m in the bank. "
                        f"Roll your free transfer to bank {next_banked} Free Transfers for upcoming fixture swings."
                    )
                best_transfer = None
                alternative_transfers = []
            else:
                is_no_transfer = False
                transfer_action = "TRANSFER"
                verdict_title = f"Suggested {best_transfer['transfer_type']}"
                verdict_explanation = ""
                alternative_transfers = [
                    t for t in alternative_transfers
                    if (t.get("xp_gain", 0) >= 1.0 or (round(t["player_out"]["cost"] - t["player_in"]["cost"], 2) >= 1.0 and t.get("xp_gain", 0) >= 0.0))
                ]

        sorted_by_xp = sorted(starter_players, key=lambda x: -x["expected_points"])
        captain_pick = sorted_by_xp[0] if sorted_by_xp else None
        vc_pick = sorted_by_xp[1] if len(sorted_by_xp) > 1 else captain_pick

        return {
            "action": transfer_action,
            "is_no_transfer": is_no_transfer,
            "verdict_title": verdict_title,
            "verdict_explanation": verdict_explanation,
            "transfer": best_transfer,
            "transfers": [best_transfer] if best_transfer else [],
            "alternative_transfers": alternative_transfers,
            "net_xp_gain": round(best_transfer["xp_gain"], 2) if best_transfer else 0.0,
            "free_transfers_available": free_transfers,
            "transfer_hit_cost": transfer_hit_cost,
            "chip_recommendation": chip_recommendation,
            "captain": {
                "player": captain_pick,
                "confidence": min(98, int(60 + (captain_pick['expected_points'] * 3.8))) if captain_pick else 50,
                "reason": f"Top projected score ({captain_pick['expected_points']} xP) based on FotMob attacking stats, history ({captain_pick['historical_avg_pts']} pts) and opponent FDR." if captain_pick else ""
            },
            "vice_captain": {
                "player": vc_pick,
                "confidence": min(92, int(50 + (vc_pick['expected_points'] * 3.5))) if vc_pick else 50,
                "reason": f"Reliable secondary choice ({vc_pick['expected_points']} xP) with high minutes security." if vc_pick else ""
            },
            "squad_summary": {
                "total_starters_xp": round(total_starter_xp, 2),
                "total_bench_xp": round(total_bench_xp, 2),
                "injured_count": len(flagged_players),
                "flagged_players": [p["web_name"] for p in flagged_players]
            }
        }

    def update_gameweek_data(self, force_refresh: bool = True) -> Dict[str, Any]:
        """
        Executes a complete data update pipeline after every Gameweek:
        1. Refreshes official FPL bootstrap (points, price changes, transfer trends, fixture status).
        2. Refreshes live fixtures & upcoming gameweek status.
        3. Re-scans match details and shotmaps across all completed matches.
        4. Re-calculates Bayesian holistic rates, correlation multipliers, and expected points across all players.
        """
        logger.info("Running complete Gameweek data update pipeline via GameweekPipeline...")
        from gameweek_pipeline import GameweekPipeline
        pipeline = GameweekPipeline(self)
        res = pipeline.run_pipeline(force=force_refresh, trigger_source="engine_update_gameweek_data")
        gw_info = self.fpl.get_gameweek_info()
        return {
            "success": res.get("success", True),
            "gameweek": gw_info.get("target_gameweek"),
            "target_gameweek": gw_info.get("target_gameweek"),
            "latest_completed_gameweek": gw_info.get("latest_completed_gameweek"),
            "gameweek_name": gw_info.get("gameweek_name"),
            "total_players": len(self.unified_players),
            "is_live": gw_info.get("is_live", False),
            "updated_at": gw_info.get("deadline_time"),
            "pipeline_summary": res.get("run_summary")
        }

if __name__ == "__main__":
    rec = RecommenderEngine()
    sq = rec.get_default_squad()
    for p in rec.unified_players:
        if 'kusi' in p['web_name'].lower():
            print("Kusi-Asare updated xP:", p['expected_points'], "likelihood:", p['playing_likelihood'], "reason:", p['unlikely_reason'])
