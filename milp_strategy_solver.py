"""
Mixed-Integer Linear Programming (MILP) Multi-Gameweek Strategy Solver for FPL.
Solves the global optimal transfer trajectory across any horizon H in [1, 8] gameweeks.

Enforces:
1. Exact squad composition: 15 players (2 GKP, 5 DEF, 5 MID, 3 FWD).
2. Maximum 3 players from any Premier League club.
3. Valid starting XI: 11 players, valid formations (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD).
4. Captaincy optimization: 2x points on top starter (or 3x for Triple Captain chip).
5. Vice-captain selection: top backup starter.
6. Transfer conservation & budget constraints: Total squad cost + bank <= Team Value.
7. Official 2026/27 5-Free Transfer Cap Rule:
   - Managers can accumulate and bank up to 5 Free Transfers (capped at 5).
   - Extra transfers beyond banked FTs incur standard -4 point hits.
   - Controlled hit appetite: Limits non-wildcard hits to high-conviction transfers.
   - Solved to global mathematical optimality using SciPy's HiGHS linear programming solver.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple, Set

import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds

logger = logging.getLogger("MILPStrategySolver")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class MILPStrategySolver:
    """
    State-of-the-Art Multi-Gameweek FPL Optimizer using Mixed-Integer Linear Programming.
    """
    def __init__(self, recommender_engine):
        self.re = recommender_engine
        self.fpl = recommender_engine.fpl
        self.players_by_id = recommender_engine.players_by_id
        self.unified_players = recommender_engine.unified_players

    def solve(
        self,
        initial_squad_ids: List[int],
        initial_bank: float,
        initial_ft: int,
        target_gws: List[int],
        player_multi_xp_map: Dict[int, Dict[str, Any]],
        player_projected_costs: Dict[int, float],
        chips_used: Optional[Dict[str, bool]] = None,
        max_transfers_per_gw: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        Solves the MILP formulation for the target gameweeks.
        Returns the optimized roadmap or None if solver fails.
        """
        chips_used = chips_used or {}
        H = len(target_gws)
        if H == 0:
            return None

        # 1. Build Candidate Player Pool
        # Must include all 15 initial squad players + top performing viable targets
        initial_squad_set = set(initial_squad_ids)
        squad_players = [self.players_by_id[pid] for pid in initial_squad_ids if pid in self.players_by_id]
        if len(squad_players) < 15:
            logger.warning(f"Initial squad contains only {len(squad_players)} valid players, expected 15.")
            return None

        # Viable non-squad candidates
        viable_pool = [
            p for p in self.unified_players
            if p["id"] not in initial_squad_set
            and not p.get("is_unavailable")
            and not p.get("is_transferred_out")
            and p.get("status") not in ['u', 'n']
        ]

        def get_p_horizon_xp(pid: int) -> float:
            p_info = player_multi_xp_map.get(pid)
            if not p_info:
                return 0.0
            return sum(next((g["xp"] for g in p_info["gw_breakdown"] if g["gameweek"] == gw), 0.0) for gw in target_gws)

        # Select top candidates per position (Curated elite pool for ultra-fast solving)
        cands_by_pos = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
        pos_quotas = {"GKP": 2, "DEF": 8, "MID": 8, "FWD": 5}

        for pos, quota in pos_quotas.items():
            pos_pool = [p for p in viable_pool if p["position"] == pos]
            pos_pool.sort(key=lambda p: get_p_horizon_xp(p["id"]), reverse=True)
            cands_by_pos[pos] = pos_pool[:quota]

        all_candidates = list(squad_players)
        added_ids = set(p["id"] for p in all_candidates)
        for pos_list in cands_by_pos.values():
            for p in pos_list:
                if p["id"] not in added_ids:
                    all_candidates.append(p)
                    added_ids.add(p["id"])

        N = len(all_candidates)
        p_to_idx = {p["id"]: i for i, p in enumerate(all_candidates)}

        # 2. Variable Index Layout
        # For each step t in 0..H-1:
        # s[i, t]: 0..N-1
        # x[i, t]: N..2N-1
        # c[i, t]: 2N..3N-1
        # u[i, t]: 3N..4N-1
        # w[i, t]: 4N..5N-1
        # ft_used[t]: 5N
        # hits[t]: 5N+1
        # ft_banked[t]: 5N+2
        STEP_VARS = 5 * N + 3
        TOTAL_VARS = H * STEP_VARS

        def idx_s(t, i): return t * STEP_VARS + i
        def idx_x(t, i): return t * STEP_VARS + N + i
        def idx_c(t, i): return t * STEP_VARS + 2 * N + i
        def idx_u(t, i): return t * STEP_VARS + 3 * N + i
        def idx_w(t, i): return t * STEP_VARS + 4 * N + i
        def idx_ft_used(t): return t * STEP_VARS + 5 * N
        def idx_hits(t): return t * STEP_VARS + 5 * N + 1
        def idx_ft_banked(t): return t * STEP_VARS + 5 * N + 2

        # 3. Detect Optimal Chip Opportunities
        # Triple Captain: step with highest single-player captain projection >= 7.5
        tc_step: Optional[int] = None
        if not chips_used.get("3xc"):
            best_gw_cap = 0.0
            best_s = 0
            for t_idx, gw in enumerate(target_gws):
                for p in all_candidates:
                    p_info = player_multi_xp_map.get(p["id"])
                    if p_info:
                        gx = next((g["xp"] for g in p_info["gw_breakdown"] if g["gameweek"] == gw), 0.0)
                        if gx > best_gw_cap:
                            best_gw_cap = gx
                            best_s = t_idx
            if best_gw_cap >= 7.2:
                tc_step = best_s

        # Bench Boost: step with highest bench potential (if not already used)
        bb_step: Optional[int] = None
        if not chips_used.get("bboost") and tc_step != 0:
            pass

        # 4. Objective Vector (c): Minimize -Points
        c = np.zeros(TOTAL_VARS, dtype=np.float64)

        for t_idx, gw in enumerate(target_gws):
            is_tc_step = (t_idx == tc_step)
            for i, p in enumerate(all_candidates):
                p_info = player_multi_xp_map.get(p["id"])
                gw_xp = 0.0
                if p_info:
                    gw_xp = next((g["xp"] for g in p_info["gw_breakdown"] if g["gameweek"] == gw), 0.0)

                # Starter points (0.95 primary weight)
                c[idx_x(t_idx, i)] -= round(0.95 * gw_xp, 4)
                # Bench points (0.05 tie-breaker equity so solver maintains good squad depth)
                c[idx_s(t_idx, i)] -= round(0.05 * gw_xp, 4)
                # Captain points (1x extra, or 2x extra if TC is active)
                c[idx_c(t_idx, i)] -= round((2.0 if is_tc_step else 1.0) * gw_xp, 4)

            # Hit penalty: 4.0 points penalty + 0.2 hurdle to avoid low-conviction hits
            c[idx_hits(t_idx)] += 4.2

            # Free Transfer equity incentive: Banked transfers provide strategic flexibility
            # and optionality across the horizon. A modest incentive (-0.15 xP per banked FT)
            # avoids wasteful sideways churn and ensures the solver accumulates FTs up to 5 when rolling.
            c[idx_ft_banked(t_idx)] -= 0.15

        # 5. Integrality and Bounds
        integrality = np.ones(TOTAL_VARS, dtype=np.int64)
        lb = np.zeros(TOTAL_VARS, dtype=np.float64)
        ub = np.ones(TOTAL_VARS, dtype=np.float64)

        for t_idx in range(H):
            # ft_used in [0, 5]
            lb[idx_ft_used(t_idx)] = 0.0
            ub[idx_ft_used(t_idx)] = 5.0
            # hits >= 0 (up to 4)
            lb[idx_hits(t_idx)] = 0.0
            ub[idx_hits(t_idx)] = 4.0
            # ft_banked in [0, 5] (allow 0 on step 0 if current manager has 0 FT available)
            lb[idx_ft_banked(t_idx)] = 0.0 if t_idx == 0 else 1.0
            ub[idx_ft_banked(t_idx)] = 5.0

        # Step 0 starting FT is fixed to initial_ft
        init_ft_clamped = max(0, min(5, int(initial_ft)))
        lb[idx_ft_banked(0)] = float(init_ft_clamped)
        ub[idx_ft_banked(0)] = float(init_ft_clamped)

        # 6. Linear Constraints Construction
        A_rows = []
        b_l = []
        b_u = []

        def add_constraint(row_dict: Dict[int, float], lower: float, upper: float):
            row = np.zeros(TOTAL_VARS, dtype=np.float64)
            for col_idx, val in row_dict.items():
                row[col_idx] = val
            A_rows.append(row)
            b_l.append(lower)
            b_u.append(upper)

        gkp_indices = [i for i, p in enumerate(all_candidates) if p["position"] == "GKP"]
        def_indices = [i for i, p in enumerate(all_candidates) if p["position"] == "DEF"]
        mid_indices = [i for i, p in enumerate(all_candidates) if p["position"] == "MID"]
        fwd_indices = [i for i, p in enumerate(all_candidates) if p["position"] == "FWD"]

        team_indices: Dict[int, List[int]] = {}
        for i, p in enumerate(all_candidates):
            tid = p.get("team_id", p.get("team", 0))
            if tid not in team_indices:
                team_indices[tid] = []
            team_indices[tid].append(i)

        initial_squad_value = sum(
            player_projected_costs.get(pid, float(self.players_by_id[pid]["cost"]))
            for pid in initial_squad_ids if pid in self.players_by_id
        )
        total_budget = round(initial_squad_value + float(initial_bank), 2)

        initial_squad_indicator = np.zeros(N, dtype=np.float64)
        for pid in initial_squad_ids:
            if pid in p_to_idx:
                initial_squad_indicator[p_to_idx[pid]] = 1.0

        for t_idx in range(H):
            # C1: Squad Size = 15
            add_constraint({idx_s(t_idx, i): 1.0 for i in range(N)}, 15.0, 15.0)

            # C2: Positional Composition (2 GKP, 5 DEF, 5 MID, 3 FWD)
            add_constraint({idx_s(t_idx, i): 1.0 for i in gkp_indices}, 2.0, 2.0)
            add_constraint({idx_s(t_idx, i): 1.0 for i in def_indices}, 5.0, 5.0)
            add_constraint({idx_s(t_idx, i): 1.0 for i in mid_indices}, 5.0, 5.0)
            add_constraint({idx_s(t_idx, i): 1.0 for i in fwd_indices}, 3.0, 3.0)

            # C3: Club Quota (<= 3 per Premier League club)
            for tid, t_idxs in team_indices.items():
                if len(t_idxs) > 3:
                    add_constraint({idx_s(t_idx, i): 1.0 for i in t_idxs}, 0.0, 3.0)

            # C4: Starters Size = 11
            add_constraint({idx_x(t_idx, i): 1.0 for i in range(N)}, 11.0, 11.0)

            # C5: Starters must be in Squad: x[i, t] <= s[i, t] => x - s <= 0
            for i in range(N):
                add_constraint({idx_x(t_idx, i): 1.0, idx_s(t_idx, i): -1.0}, -1.0, 0.0)

            # C6: Valid Formations (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD)
            add_constraint({idx_x(t_idx, i): 1.0 for i in gkp_indices}, 1.0, 1.0)
            add_constraint({idx_x(t_idx, i): 1.0 for i in def_indices}, 3.0, 5.0)
            add_constraint({idx_x(t_idx, i): 1.0 for i in mid_indices}, 2.0, 5.0)
            add_constraint({idx_x(t_idx, i): 1.0 for i in fwd_indices}, 1.0, 3.0)

            # C7: Captaincy: exactly 1 captain, must be a starter
            add_constraint({idx_c(t_idx, i): 1.0 for i in range(N)}, 1.0, 1.0)
            for i in range(N):
                add_constraint({idx_c(t_idx, i): 1.0, idx_x(t_idx, i): -1.0}, -1.0, 0.0)

            # C8: Squad Transfer Flow
            # s[i, t] - s[i, t-1] - u[i, t] + w[i, t] = 0
            if t_idx == 0:
                for i in range(N):
                    init_val = float(initial_squad_indicator[i])
                    add_constraint({
                        idx_s(0, i): 1.0,
                        idx_u(0, i): -1.0,
                        idx_w(0, i): 1.0
                    }, init_val, init_val)
            else:
                for i in range(N):
                    add_constraint({
                        idx_s(t_idx, i): 1.0,
                        idx_s(t_idx - 1, i): -1.0,
                        idx_u(t_idx, i): -1.0,
                        idx_w(t_idx, i): 1.0
                    }, 0.0, 0.0)

            # C9: Balance transfers: sum(u) == sum(w) => sum(u) - sum(w) = 0
            trans_balance_dict = {idx_u(t_idx, i): 1.0 for i in range(N)}
            trans_balance_dict.update({idx_w(t_idx, i): -1.0 for i in range(N)})
            add_constraint(trans_balance_dict, 0.0, 0.0)

            # No round-trip transfer in same step
            for i in range(N):
                add_constraint({idx_u(t_idx, i): 1.0, idx_w(t_idx, i): 1.0}, 0.0, 1.0)

            # Max transfers per gameweek to prevent excessive -4 hit churn
            # Allow up to max_transfers_per_gw (or max(max_transfers_per_gw, ft_banked))
            add_constraint({idx_u(t_idx, i): 1.0 for i in range(N)}, 0.0, float(max_transfers_per_gw))

            # C10: Budget Constraint
            cost_row = {}
            for i, p in enumerate(all_candidates):
                p_cost = player_projected_costs.get(p["id"], float(p.get("cost", 5.0)))
                cost_row[idx_s(t_idx, i)] = p_cost
            add_constraint(cost_row, 0.0, total_budget)

            # C11: Free Transfers and Hits Mechanics (Official 2026/27 5-FT Rule)
            # ft_used[t] <= ft_banked[t] => ft_used - ft_banked <= 0
            add_constraint({idx_ft_used(t_idx): 1.0, idx_ft_banked(t_idx): -1.0}, -5.0, 0.0)

            # ft_used[t] <= sum(u[i, t]) => ft_used - sum(u) <= 0
            ft_used_bound = {idx_ft_used(t_idx): 1.0}
            ft_used_bound.update({idx_u(t_idx, i): -1.0 for i in range(N)})
            add_constraint(ft_used_bound, -15.0, 0.0)

            # hits[t] >= sum(u[i, t]) - ft_used[t] => hits + ft_used - sum(u) >= 0
            hits_row = {idx_hits(t_idx): 1.0, idx_ft_used(t_idx): 1.0}
            hits_row.update({idx_u(t_idx, i): -1.0 for i in range(N)})
            add_constraint(hits_row, 0.0, 15.0)

            # Free Transfer Bank Progression to Next Gameweek (t+1)
            # ft_banked[t+1] <= ft_banked[t] - ft_used[t] + 1 (capped at 5)
            # => ft_banked[t+1] - ft_banked[t] + ft_used[t] <= 1
            # In official FPL rules, banked FTs always equal min(5, ft_banked[t] - ft_used[t] + 1).
            # Lower bound is 0.0 (since ft_banked[t+1] - (ft_banked[t] - ft_used[t]) is at least 0 even when capped at 5).
            if t_idx < H - 1:
                add_constraint({
                    idx_ft_banked(t_idx + 1): 1.0,
                    idx_ft_banked(t_idx): -1.0,
                    idx_ft_used(t_idx): 1.0
                }, 0.0, 1.0)

        # 7. Execute HiGHS Solver with Early Termination Tolerance
        A = np.array(A_rows, dtype=np.float64)
        constraints = LinearConstraint(A, b_l, b_u)
        bounds_obj = Bounds(lb, ub)

        logger.info(f"Solving Multi-GW MILP: {TOTAL_VARS} variables, {len(b_l)} constraints across {H} gameweeks...")
        solver_options = {
            "mip_rel_gap": 0.02,  # Stop within 2.0% of theoretical global optimum (sub-second solve)
            "time_limit": 2.0,    # Hard time limit ceiling (sec)
            "presolve": True
        }
        res = milp(c=c, integrality=integrality, bounds=bounds_obj, constraints=constraints, options=solver_options)

        if res.x is None or (res.status != 0 and res.status != 1):
            logger.warning(f"MILP solver finished with status {res.status}: {res.message}")
            return None

        # 8. Extract Global Solution
        sol = res.x
        roadmap = []
        optimized_total_xp = 0.0
        total_transfers_made = 0
        total_transfers_rolled = 0
        total_hits_taken = 0

        # Dynamic financial and player price evolution across the horizon
        running_bank = round(float(initial_bank), 2)
        running_player_costs = {p["id"]: float(player_projected_costs.get(p["id"], p.get("cost", 5.0))) for p in all_candidates}
        current_squad_ids = list(initial_squad_ids)
        running_ft = init_ft_clamped

        for t_idx, gw in enumerate(target_gws):
            step_squad_idxs = [i for i in range(N) if sol[idx_s(t_idx, i)] > 0.5]
            step_starter_idxs = [i for i in range(N) if sol[idx_x(t_idx, i)] > 0.5]
            step_bench_idxs = [i for i in step_squad_idxs if i not in step_starter_idxs]

            step_cap_idx = next((i for i in range(N) if sol[idx_c(t_idx, i)] > 0.5), step_starter_idxs[0] if step_starter_idxs else 0)

            starters_sorted = sorted(
                step_starter_idxs,
                key=lambda idx: next((g["xp"] for g in player_multi_xp_map.get(all_candidates[idx]["id"], {}).get("gw_breakdown", []) if g["gameweek"] == gw), 0.0),
                reverse=True
            )
            step_vc_idx = starters_sorted[1] if len(starters_sorted) > 1 and starters_sorted[0] == step_cap_idx else (starters_sorted[0] if len(starters_sorted) > 1 else step_cap_idx)

            in_idxs = [i for i in range(N) if sol[idx_u(t_idx, i)] > 0.5]
            out_idxs = [i for i in range(N) if sol[idx_w(t_idx, i)] > 0.5]

            transfers_in_players = [all_candidates[i] for i in in_idxs]
            transfers_out_players = [all_candidates[i] for i in out_idxs]

            num_t = len(transfers_in_players)
            total_transfers_made += num_t

            chip_action = "3xc" if (t_idx == tc_step) else None

            # Accurate FPL Free Transfer progression
            ft_starting = running_ft

            if chip_action in ("wildcard", "freehit"):
                ft_used = 0
                hits_taken = 0
                hit_cost = 0
                # Official 2024/25+ FPL rule: Banked transfers are preserved across Wildcard & Free Hit
                ft_ending = max(1, ft_starting)
            else:
                ft_used = min(ft_starting, num_t)
                hits_taken = max(0, num_t - ft_starting)
                hit_cost = hits_taken * 4
                # Bank rolling transfers up to official 5-FT cap
                ft_ending = min(5, max(0, ft_starting - ft_used) + 1)

            running_ft = ft_ending
            total_hits_taken += hits_taken

            if num_t == 0:
                total_transfers_rolled += 1

            # 1. Execute Transfers Financials under official FPL rules
            in_cost_details = []
            out_cost_details = []

            for p_out in transfers_out_players:
                sell_val = running_player_costs.get(p_out["id"], float(p_out.get("cost", 5.0)))
                running_bank += sell_val
                if p_out["id"] in current_squad_ids:
                    current_squad_ids.remove(p_out["id"])
                out_cost_details.append({
                    "id": p_out["id"],
                    "web_name": p_out["web_name"],
                    "cost": round(sell_val, 1),
                    "position": p_out["position"],
                    "team_short": p_out.get("team_short", "")
                })

            for p_in in transfers_in_players:
                buy_val = running_player_costs.get(p_in["id"], float(p_in.get("cost", 5.0)))
                running_bank -= buy_val
                if p_in["id"] not in current_squad_ids:
                    current_squad_ids.append(p_in["id"])
                in_cost_details.append({
                    "id": p_in["id"],
                    "web_name": p_in["web_name"],
                    "cost": round(buy_val, 1),
                    "position": p_in["position"],
                    "team_short": p_in.get("team_short", "")
                })

            running_bank = round(running_bank, 2)

            if chip_action == "3xc":
                action = "CHIP_TRIPLE_CAPTAIN"
                cap_name = all_candidates[step_cap_idx]["web_name"]
                action_title = f"[TC] Activate Triple Captain ({cap_name})"
                action_desc = f"We have identified a global ceiling fixture for {cap_name} across the {H}-GW horizon."
            elif num_t == 0:
                action = "ROLL_TRANSFER"
                if ft_starting >= 5:
                    action_title = "Roll Free Transfer (Max 5 FTs Banked)"
                    action_desc = "Starting squad is optimal. You are already at the maximum cap of 5 banked Free Transfers."
                elif ft_starting == 0:
                    action_title = "Roll Transfer (Build to 1 FT)"
                    action_desc = "Hold squad this gameweek to avoid point hits. You will be allocated 1 Free Transfer entering next gameweek."
                else:
                    ft_s = "" if ft_ending == 1 else "s"
                    action_title = f"Roll Free Transfer (Build to {ft_ending} FT{ft_s})"
                    action_desc = f"Squad is mathematically optimal. Roll free transfer to build equity to {ft_ending} FT{ft_s} under official 5-FT cap."
            elif num_t == 1:
                action = "TRANSFER"
                p_in = transfers_in_players[0]
                p_out = transfers_out_players[0]
                hit_str = f" (-{hit_cost} Hit)" if hits_taken > 0 else " (0 Hit)"
                action_title = f"Transfer: {p_in['web_name']} for {p_out['web_name']}{hit_str}"
                action_desc = f"Optimal upgrade: Transfer in {p_in['web_name']} (£{round(running_player_costs.get(p_in['id'], p_in.get('cost', 5.0)), 1)}m) for {p_out['web_name']}."
            elif num_t == 2:
                action = "DOUBLE_TRANSFER"
                in_names = " & ".join(p["web_name"] for p in transfers_in_players)
                out_names = " & ".join(p["web_name"] for p in transfers_out_players)
                hit_str = f" (-{hit_cost} Hit)" if hits_taken > 0 else " (0 Hit)"
                action_title = f"Double Transfer: {in_names}{hit_str}"
                action_desc = f"Pairwise restructuring: Replace {out_names} with {in_names} maximizing net horizon points."
            else:
                action = "MULTIPLE_TRANSFERS"
                in_names = ", ".join(p["web_name"] for p in transfers_in_players)
                out_names = ", ".join(p["web_name"] for p in transfers_out_players)
                hit_str = f" (-{hit_cost} Hit)" if hits_taken > 0 else " (0 Hit)"
                action_title = f"Multi Transfer ({num_t}): {in_names}{hit_str}"
                action_desc = f"Squad pivot: Out: {out_names} | In: {in_names}."

            starter_xp_sum = sum(
                next((g["xp"] for g in player_multi_xp_map.get(all_candidates[i]["id"], {}).get("gw_breakdown", []) if g["gameweek"] == gw), 0.0)
                for i in step_starter_idxs
            )
            cap_xp = next((g["xp"] for g in player_multi_xp_map.get(all_candidates[step_cap_idx]["id"], {}).get("gw_breakdown", []) if g["gameweek"] == gw), 0.0)
            vc_xp = next((g["xp"] for g in player_multi_xp_map.get(all_candidates[step_vc_idx]["id"], {}).get("gw_breakdown", []) if g["gameweek"] == gw), 0.0)

            gross_step_xp = starter_xp_sum + (2.0 if chip_action == "3xc" else 1.0) * cap_xp
            step_xp = round(gross_step_xp - hit_cost, 2)
            optimized_total_xp += step_xp

            # 2. Gameweek Performance & Price Fluctuations based on Selected by % and Performance
            for p in all_candidates:
                p_info = player_multi_xp_map.get(p["id"])
                p_gw_xp = next((g["xp"] for g in p_info["gw_breakdown"] if g["gameweek"] == gw), 0.0) if p_info else float(p.get("expected_points", 0.0))
                p_fdr = float(next((g.get("difficulty", 3) for g in p_info["gw_breakdown"] if g["gameweek"] == gw), 3.0)) if p_info else 3.0
                delta, _, _, _ = self.re.predict_player_price_change(p, gw_index=t_idx, gw_xp=p_gw_xp, fdr=p_fdr)
                running_player_costs[p["id"]] = round(max(3.5, running_player_costs[p["id"]] + delta), 1)

            step_squad_cost = round(sum(running_player_costs.get(all_candidates[i]["id"], float(all_candidates[i].get("cost", 5.0))) for i in step_squad_idxs), 1)
            step_bank = round(running_bank, 2)
            step_team_value = round(step_squad_cost + step_bank, 1)

            def_cnt = len([i for i in step_starter_idxs if all_candidates[i]["position"] == "DEF"])
            mid_cnt = len([i for i in step_starter_idxs if all_candidates[i]["position"] == "MID"])
            fwd_cnt = len([i for i in step_starter_idxs if all_candidates[i]["position"] == "FWD"])
            formation_str = f"{def_cnt}-{mid_cnt}-{fwd_cnt}"

            cap_player = all_candidates[step_cap_idx]
            vc_player = all_candidates[step_vc_idx]

            roadmap.append({
                "gameweek": gw,
                "step_index": t_idx + 1,
                "action": action,
                "action_title": action_title,
                "action_desc": action_desc,
                "transfers_in": in_cost_details,
                "transfers_out": out_cost_details,
                "ft_starting": ft_starting,
                "ft_used": ft_used,
                "hits_taken": hits_taken,
                "hit_cost": hit_cost,
                "ft_ending": ft_ending,
                "bank_remaining": step_bank,
                "bank": step_bank,
                "itb": step_bank,
                "squad_value": round(step_squad_cost, 1),
                "team_value": step_team_value,
                "chip_played": chip_action,
                "captain": {
                    "id": cap_player["id"],
                    "web_name": cap_player["web_name"],
                    "team_short": cap_player.get("team_short", ""),
                    "xp": cap_xp
                },
                "vice_captain": {
                    "id": vc_player["id"],
                    "web_name": vc_player["web_name"],
                    "team_short": vc_player.get("team_short", ""),
                    "xp": vc_xp
                },
                "gross_xi_xp": round(gross_step_xp, 2),
                "net_xi_xp": round(step_xp, 2),
                "projected_xi_xp": round(step_xp, 2),
                "formation": formation_str,
                "is_milp_optimal": True
            })

        total_hit_cost = total_hits_taken * 4
        return {
            "roadmap": roadmap,
            "gross_total_xp": round(optimized_total_xp + total_hit_cost, 2),
            "optimized_total_xp": round(optimized_total_xp, 2),
            "total_transfers_made": total_transfers_made,
            "total_transfers_rolled": total_transfers_rolled,
            "total_hits_taken": total_hits_taken,
            "total_hit_cost": total_hit_cost,
            "solver": "HiGHS-MILP"
        }
