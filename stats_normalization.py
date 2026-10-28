"""
stats_normalization.py
Comprehensive Stats Normalization & Data Pulling Engine for:
1. Players who have recently entered the Premier League (new signings, promoted players, low PL minutes).
2. Players who were recently injured or are returning from injury layoffs.

Key Normalization Methodologies:
- Empirical Bayes shrinkage for small-sample per-90 metrics (xG/90, xA/90, shots/90) toward position- and cost-adjusted baselines.
- Playing likelihood recalibration: distinguishes senior marquee/key signings from youth academy reserves so new stars aren't penalized as "fringe players".
- Return-to-play minutes ramp-up curves for injured players returning to action (GW0: 45-60m, GW1: 70m, GW2: 90m).
- Diluted Form Normalization: converts FPL 30-day diluted form to true per-appearance productivity.
- Price lock enforcement for players returning from injury flags.
"""

import os
import json
import time
import urllib.request
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("StatsNormalization")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")
MATCH_HISTORY_CACHE_FILE = os.path.join(CACHE_DIR, "player_match_history_cache.json")
CACHE_TTL_SECONDS = 3600 * 6  # 6 hours TTL for match histories

POSITION_PRIORS = {
    "FWD": {"xg": 0.35, "xa": 0.12, "baseline_pts": 3.8},
    "MID": {"xg": 0.14, "xa": 0.12, "baseline_pts": 3.5},
    "DEF": {"xg": 0.04, "xa": 0.05, "baseline_pts": 2.8},
    "GKP": {"xg": 0.00, "xa": 0.00, "baseline_pts": 2.5}
}


class StatsNormalizationEngine:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.match_history_cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(MATCH_HISTORY_CACHE_FILE):
            try:
                with open(MATCH_HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                    self.match_history_cache = json.load(f)
                logger.info(f"Loaded {len(self.match_history_cache)} player match logs from cache.")
            except Exception as e:
                logger.warning(f"Error loading match history cache: {e}")

    def save_cache(self):
        try:
            with open(MATCH_HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.match_history_cache, f, indent=2)
            logger.info(f"Saved {len(self.match_history_cache)} player match logs to cache.")
        except Exception as e:
            logger.error(f"Error saving match history cache: {e}")

    def fetch_player_element_summary(self, element_id: int, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetches official FPL element-summary (current season match log + past seasons)
        for a player, with local disk caching.
        """
        str_id = str(element_id)
        now = time.time()

        if not force_refresh and str_id in self.match_history_cache:
            entry = self.match_history_cache[str_id]
            cached_at = entry.get("_cached_at", 0)
            if now - cached_at < CACHE_TTL_SECONDS:
                return entry

        url = f"https://fantasy.premierleague.com/api/element-summary/{element_id}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                payload = {
                    "element_id": element_id,
                    "_cached_at": now,
                    "history": data.get("history", []),
                    "history_past": data.get("history_past", [])
                }
                self.match_history_cache[str_id] = payload
                return payload
        except Exception as e:
            logger.warning(f"Failed to fetch element summary for player {element_id}: {e}")
            if str_id in self.match_history_cache:
                return self.match_history_cache[str_id]
            return {"element_id": element_id, "history": [], "history_past": [], "_cached_at": now}

    def batch_prefetch_target_players(self, player_ids: List[int], max_requests: int = 150):
        """
        Prefetches match histories for targeted players (new entrants, injured players, top assets).
        """
        now = time.time()
        to_fetch = []
        for pid in player_ids:
            str_id = str(pid)
            if str_id not in self.match_history_cache:
                to_fetch.append(pid)
            else:
                cached_at = self.match_history_cache[str_id].get("_cached_at", 0)
                if now - cached_at >= CACHE_TTL_SECONDS:
                    to_fetch.append(pid)

        to_fetch = to_fetch[:max_requests]
        if not to_fetch:
            return

        logger.info(f"Prefetching element summary logs for {len(to_fetch)} players...")
        fetched = 0
        for pid in to_fetch:
            try:
                self.fetch_player_element_summary(pid, force_refresh=True)
                fetched += 1
                if fetched % 15 == 0:
                    time.sleep(0.15)
            except Exception as e:
                logger.warning(f"Error prefetching element {pid}: {e}")

        self.save_cache()
        logger.info(f"Prefetch complete. {fetched} player match histories updated.")

    def get_player_matches(self, element_id: int) -> List[Dict[str, Any]]:
        """Returns the current season match-by-match logs for an element ID."""
        data = self.match_history_cache.get(str(element_id))
        if data and "history" in data:
            return data["history"]
        return []

    # -------------------------------------------------------------------------
    # Detection Helpers
    # -------------------------------------------------------------------------
    def detect_new_pl_entrant(
        self,
        player_dict: Dict[str, Any],
        past_seasons: List[Dict[str, Any]],
        match_history: List[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Detects whether a player is a newcomer to the Premier League:
        - 0 prior completed PL seasons in history_past (career_seasons_count == 0).
        - Or total career PL minutes before current campaign was 0.
        Returns (is_new, category_str).
        """
        career_mins = sum(s.get("minutes", 0) for s in past_seasons)
        career_seasons = len([s for s in past_seasons if s.get("minutes", 0) > 0])
        cost = float(player_dict.get("cost") or player_dict.get("now_cost", 50) / 10.0 or 5.0)

        # If played in previous Premier League seasons, not a newcomer
        if career_seasons > 0 and career_mins >= 360:
            return False, ""

        # Determine newcomer category based on price tier and team profile
        if cost >= 7.5:
            category = "Marquee Signing"
        elif cost >= 6.0:
            category = "Key Signing"
        elif cost >= 5.0:
            category = "Regular Squad Signing"
        elif cost == 4.5:
            # Check starts
            starts = sum(1 for m in match_history if m.get("starts", 0) > 0)
            if starts >= 2:
                category = "Promoted / Budget Starter"
            else:
                category = "Budget Squad Option"
        else:
            category = "Academy / Fringe Prospect"

        return True, category

    def detect_recent_injury_status(
        self,
        player_dict: Dict[str, Any],
        match_history: List[Dict[str, Any]]
    ) -> Tuple[bool, bool, str, float]:
        """
        Detects injury state:
        - is_currently_injured: status in ['d', 'i'] or chance < 100 or injury news.
        - is_recent_returnee: status == 'a' and chance is 100 (or None), BUT missed games recently
          or returned in the last 1-2 gameweeks with managed minutes.
        Returns (is_currently_injured, is_recent_returnee, reason_str, return_ramp_factor).
        """
        status = player_dict.get("status", "a")
        chance = player_dict.get("chance_of_playing_next_round")
        if chance is None:
            chance = player_dict.get("chance_of_playing")
        news = player_dict.get("news") or player_dict.get("injury_reason") or ""
        low_news = news.lower()

        is_transferred = player_dict.get("is_transferred_out") or status in ['u', 'n']
        if is_transferred:
            return False, False, news or "Transferred out", 0.0

        # Currently flagged as injured or doubtful
        if status in ['i', 'd'] or (chance is not None and chance < 100) or ("injur" in low_news) or ("hamstring" in low_news) or ("knee" in low_news) or ("groin" in low_news) or ("ankle" in low_news):
            is_doubt = (status == 'd') or (chance is not None and 0 < chance <= 75)
            reason = news or ("Injury Doubt" if is_doubt else "Ruled out through injury")
            ramp = (chance / 100.0) if (chance is not None and chance > 0) else (0.50 if is_doubt else 0.0)
            return True, False, reason, ramp

        # Check for RECENT RETURN from injury (status == 'a' now, but was sidelined)
        if match_history and len(match_history) >= 2:
            # Sort by round ascending
            sorted_m = sorted(match_history, key=lambda x: x.get("round", 0))
            recent_matches = sorted_m[-3:]  # last 3 completed gameweeks
            recent_mins = [m.get("minutes", 0) for m in recent_matches]

            # Case A: Missed previous 2 games (0 mins) and just played <60 mins in the latest match
            if len(recent_mins) >= 3 and recent_mins[0] == 0 and recent_mins[1] == 0 and 0 < recent_mins[2] <= 65:
                return False, True, "Recently returned from injury layoff (1st full match back, minutes managed)", 0.70

            # Case B: Missed previous game (0 mins) and played partial minutes (<45 mins) in latest match
            if len(recent_mins) >= 2 and recent_mins[-2] == 0 and 0 < recent_mins[-1] <= 45:
                return False, True, "Returning from brief injury (cameo appearance, ramping up)", 0.75

            # Case C: Played 0 mins in the latest completed match but is officially cleared to 'a' with 100% chance
            if recent_mins[-1] == 0 and (status == 'a') and (chance is None or chance == 100) and player_dict.get("current_minutes", 0) > 90:
                return False, True, "Cleared to play following recent absence / benching", 0.85

        return False, False, "", 1.0

    # -------------------------------------------------------------------------
    # Core Normalization
    # -------------------------------------------------------------------------
    def normalize_player(
        self,
        player_dict: Dict[str, Any],
        past_seasons: Optional[List[Dict[str, Any]]] = None,
        match_history: Optional[List[Dict[str, Any]]] = None,
        raw_xg_90: Optional[float] = None,
        raw_xa_90: Optional[float] = None,
        fotmob_rating: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculates normalized statistics and sample-size corrections for a player.
        Returns a dict of enriched normalization attributes.
        """
        pid = player_dict.get("id") or player_dict.get("fpl_id")
        if past_seasons is None:
            past_seasons = player_dict.get("historical_past") or []
        if match_history is None:
            match_history = self.get_player_matches(pid)

        cost = float(player_dict.get("cost") or (player_dict.get("now_cost", 50) / 10.0) or 5.0)
        pos = player_dict.get("position", "MID")
        current_mins = int(player_dict.get("current_minutes") or player_dict.get("minutes", 0))
        current_starts = int(player_dict.get("current_starts") or player_dict.get("starts", 0))
        total_pts = int(player_dict.get("total_points") or 0)
        raw_form = float(player_dict.get("form") or 0.0)

        # 1. Detection
        is_new_entrant, new_entrant_category = self.detect_new_pl_entrant(player_dict, past_seasons, match_history)
        is_injured, is_recent_returnee, injury_reason, injury_ramp = self.detect_recent_injury_status(player_dict, match_history)

        status_tags: List[str] = []
        normalization_notes: List[str] = []

        if is_injured:
            status_tags.append("Injured / Doubt")
        elif is_recent_returnee:
            status_tags.append("Returning from Injury")

        # Price protection under official FPL rules (8-day price drop buffer for returning / flagged players)
        price_drop_protected = bool(is_injured or is_recent_returnee)

        # 2. Sample Size Confidence & Bayesian Regularization
        # Baseline threshold M0 = 450 minutes (approx. 5 full matches)
        reg_mins = 450.0
        confidence_factor = min(1.0, current_mins / reg_mins) if current_mins > 0 else 0.0

        pos_prior = POSITION_PRIORS.get(pos, POSITION_PRIORS["MID"])
        # Cost-scaled prior expectation (e.g. £10.0m mid has higher expected xG than £4.5m mid)
        cost_multiplier = max(0.70, min(1.40, 0.70 + (cost / 15.0)))
        prior_xg_90 = round(pos_prior["xg"] * cost_multiplier, 3)
        prior_xa_90 = round(pos_prior["xa"] * cost_multiplier, 3)

        if raw_xg_90 is None:
            raw_xg_90 = float(player_dict.get("raw_current_xg90") or player_dict.get("xg_90") or 0.0)
        if raw_xa_90 is None:
            raw_xa_90 = float(player_dict.get("raw_current_xa90") or player_dict.get("xa_90") or 0.0)

        # Shrunk / Regularized per-90 rates
        if is_new_entrant or (current_mins < 180 and pos != "GKP"):
            # Small sample: blend raw observed rate with cost-position prior
            norm_xg_90 = round((raw_xg_90 * confidence_factor) + (prior_xg_90 * (1.0 - confidence_factor)), 2)
            norm_xa_90 = round((raw_xa_90 * confidence_factor) + (prior_xa_90 * (1.0 - confidence_factor)), 2)
            normalization_notes.append(
                f"Small sample calibration: Rates regularized toward £{cost:.1f}m {pos} baseline "
                f"({confidence_factor*100:.0f}% sample confidence across {current_mins} mins)."
            )
        else:
            norm_xg_90 = round(raw_xg_90, 2)
            norm_xa_90 = round(raw_xa_90, 2)

        # 3. Form Normalization (De-diluting missed matches)
        matches_played = [m for m in match_history if m.get("minutes", 0) > 0]
        count_played = len(matches_played)

        if count_played > 0:
            total_played_pts = sum(m.get("total_points", 0) for m in matches_played)
            pts_per_played_match = total_played_pts / count_played
            # Blend per-match average with official rolling form to remove injury zeros
            normalized_form = round((pts_per_played_match * 0.70) + (raw_form * 0.30), 1)
        else:
            normalized_form = round(raw_form, 1)

        if is_recent_returnee or (is_injured and raw_form < 2.0 and count_played > 0):
            normalization_notes.append(
                f"Form normalized from {raw_form:.1f} to {normalized_form:.1f} to remove inactive injury match blanks."
            )

        # 4. Expected Minutes Projection
        base_expected_mins = 90 if current_starts >= 3 or (current_mins / max(1, count_played or 1) >= 65) else (
            60 if current_starts >= 1 else (30 if current_mins > 0 else 0)
        )

        if is_new_entrant and cost >= 6.0 and base_expected_mins < 60:
            # Senior signings slated for primary role
            base_expected_mins = 65

        if is_injured:
            expected_mins_projected = int(round(base_expected_mins * injury_ramp))
            ramp_factor = injury_ramp
        elif is_recent_returnee:
            expected_mins_projected = int(round(base_expected_mins * injury_ramp))
            ramp_factor = injury_ramp
            normalization_notes.append(
                f"Return-to-play minutes ramp: {ramp_factor*100:.0f}% capacity (~{expected_mins_projected} projected mins)."
            )
        else:
            expected_mins_projected = base_expected_mins
            ramp_factor = 1.0

        # 5. Correct Playing Likelihood for New Signings
        # Crucial fix: Senior new signings must NOT be penalized as 8% fringe reserve players!
        adjusted_likelihood = player_dict.get("playing_likelihood", 100.0)
        is_unlikely = player_dict.get("is_unlikely_to_play", False)
        unlikely_reason = player_dict.get("unlikely_reason", "")

        if is_new_entrant and not player_dict.get("is_unavailable") and not player_dict.get("is_doubtful"):
            if current_mins == 0 and cost >= 6.0:
                adjusted_likelihood = 80.0
                is_unlikely = False
                unlikely_reason = ""
                normalization_notes.append(f"Senior new signing (£{cost:.1f}m): Playing likelihood restored to 80% (first-team integration).")
            elif current_mins == 0 and cost >= 5.0:
                adjusted_likelihood = 65.0
                is_unlikely = False
                unlikely_reason = ""
                normalization_notes.append(f"Role calibration (£{cost:.1f}m): Playing likelihood set to 65%.")
            elif current_mins > 0 and current_starts == 0 and cost >= 6.0:
                adjusted_likelihood = 75.0
                is_unlikely = False
                unlikely_reason = ""
                normalization_notes.append(f"Role calibration (£{cost:.1f}m): Playing likelihood set to 75%.")

        return {
            "is_new_pl_entrant": is_new_entrant,
            "new_entrant_category": new_entrant_category,
            "is_recently_injured": is_injured,
            "is_recent_injury_returnee": is_recent_returnee,
            "injury_reason": injury_reason if (is_injured or is_recent_returnee) else player_dict.get("injury_reason", ""),
            "injury_return_ramp": round(ramp_factor, 2),
            "expected_minutes_projected": expected_mins_projected,
            "sample_size_confidence": round(confidence_factor * 100.0, 1),
            "sample_size_minutes": current_mins,
            "sample_size_matches": count_played,
            "normalized_xg_90": norm_xg_90,
            "normalized_xa_90": norm_xa_90,
            "prior_xg_90": prior_xg_90,
            "prior_xa_90": prior_xa_90,
            "normalized_form": normalized_form,
            "raw_form": raw_form,
            "adjusted_playing_likelihood": round(adjusted_likelihood, 1),
            "adjusted_is_unlikely": is_unlikely,
            "adjusted_unlikely_reason": unlikely_reason,
            "status_tags": status_tags,
            "price_drop_protected": price_drop_protected,
            "normalization_notes": normalization_notes,
            "recent_match_history": [
                {
                    "round": m.get("round"),
                    "minutes": m.get("minutes"),
                    "total_points": m.get("total_points"),
                    "goals": m.get("goals_scored"),
                    "assists": m.get("assists"),
                    "xg": float(m.get("expected_goals") or 0.0),
                    "xa": float(m.get("expected_assists") or 0.0)
                }
                for m in sorted(match_history, key=lambda x: x.get("round", 0))[-5:]
            ] if match_history else []
        }


# Global singleton instance
stats_normalizer = StatsNormalizationEngine()
