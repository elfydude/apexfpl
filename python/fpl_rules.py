"""
Official Fantasy Premier League (FPL) Rules, Constraints & Scoring Engine.
Codifies all official FPL rules, squad requirements, budget mechanics, transfer policies,
5-FT banking regulations, chip rules, and scoring systems for ChatFPL.
"""

from typing import Dict, Any, List, Optional, Tuple, Set

# =============================================================================
# 1. OFFICIAL FPL RULES & CONSTRAINTS SPECIFICATION
# =============================================================================

FPL_RULES_SUMMARY = """
### 📋 OFFICIAL FANTASY PREMIER LEAGUE (FPL) RULES & CONSTRAINTS SPECIFICATION

1. **Squad Composition & Position Constraints**:
   - **Total Squad Size**: Exactly 15 players.
   - **Position Allocation**:
     * 2 Goalkeepers (GKP)
     * 5 Defenders (DEF)
     * 5 Midfielders (MID)
     * 3 Forwards (FWD)
   - **Starting XI Requirements**: Exactly 11 players.
     * Minimum 1 Goalkeeper (GKP)
     * Minimum 3 Defenders (DEF)
     * Minimum 2 Midfielders (MID)
     * Minimum 1 Forward (FWD)
   - **Valid Formations**: 3-5-2, 3-4-3, 4-4-2, 4-3-3, 4-5-1, 5-3-2, 5-4-1, 5-2-3 (Total: 8 valid formation combinations).
   - **Bench (Substitutes)**: Exactly 4 players.
     * 1 Backup Goalkeeper (only substitutes for the starting GKP).
     * 3 Outfield Substitutes, ordered 1st Sub, 2nd Sub, 3rd Sub.

2. **Club / Team Quota Constraints**:
   - **Strict Team Limit**: A maximum of 3 players from any single Premier League club across the entire 15-man squad.
   - If a squad already has 3 players from a club (e.g., 3 Arsenal players), no additional player from that club may be purchased unless an existing player from that club is sold in the same transaction.

3. **Budget & Player Valuation Mechanics**:
   - **Initial Budget**: £100.0m.
   - **Bank (ITB)**: Unspent cash reserve. Must never be negative (£0.0m minimum).
   - **Squad Value**: Current sum of all 15 player market prices + In The Bank (ITB).
   - **Selling Price (50% Profit Rule)**:
     * If a player's market price increases while owned, their selling price equals:
       `Selling Price = Purchase Price + ((Current Price - Purchase Price) // 2) * 0.1`
       (i.e., £0.1m profit for every £0.2m price increase, rounded down).
     * If a player's market price drops, their selling price drops penny-for-penny with the current market price (100% loss absorbed by the manager).
   - **Transfer In Budget Rule**:
     `Player_In.cost <= Player_Out.sell_price + Bank`.

4. **Transfers, Banking & Hit Deductions**:
   - **Allocation**: 1 Free Transfer (FT) awarded after each gameweek deadline.
   - **Free Transfer Banking (Official 2024/25+ & 2026/27 Rule)**:
     * Unused Free Transfers roll over and accumulate up to a maximum cap of **5 Free Transfers** (up from the old 2 FT limit).
     * This makes rolling transfers a high-leverage strategy to target fixture swings.
   - **Wildcard & Free Hit Banking Preservation**:
     * In the modern rules, activating Wildcard or Free Hit NO LONGER resets or wipes accumulated banked Free Transfers! Banked FTs are preserved and carry into the next gameweek.
   - **Transfer Hits**:
     * Each transfer made beyond the available Free Transfers incurs an automatic **-4 point deduction** ("transfer hit") deducted from the gameweek score.
   - **Transfer Discipline / EV Threshold**:
     * A -4 hit is only mathematically justified if:
       (a) An essential starter is ruled out (0% chance) and no playing bench cover exists, or
       (b) The replacement has an overwhelming net expected points gain (> +2.5 to +4.0 xP over the immediate horizon) to clear the -4 hit hurdle.
   - **Rolling Strategy**:
     * If the starting XI is fully fit and no move yields >= 1.0 xP gain or frees >= £1.0m in the bank, the optimal mathematical play is to **Roll the Free Transfer** to bank up to 5 FTs for upcoming fixture swings.

5. **Chips Constraints & Strategy**:
   - **Wildcard (2 per season)**:
     * Grants unlimited permanent transfers without point deductions.
     * 1st Wildcard must be played before the Gameweek 19 deadline.
     * 2nd Wildcard is available from the Gameweek 20 deadline through the end of the season.
     * Banked Free Transfers are preserved through a Wildcard.
   - **Free Hit (1 per season)**:
     * Grants unlimited transfers for ONE gameweek only.
     * Squad completely reverts to the pre-Free Hit lineup immediately after the gameweek concludes.
     * Best deployed in major Blank Gameweeks (BGW) or high-upside Double Gameweeks (DGW).
     * Banked Free Transfers are preserved through a Free Hit.
   - **Triple Captain (1 per season)**:
     * Multiplies the captain's score by 3x instead of 2x (vice-captain gets 3x if the captain plays 0 minutes).
     * Best deployed in a Double Gameweek on an elite premium attacker with two favorable home matches.
   - **Bench Boost (1 per season)**:
     * Automatically adds the scores of all 4 bench players to your gameweek score.
     * Best deployed in a Double Gameweek when all 15 players have confirmed starting fixtures.
   - **Chip Activation Constraint**:
     * Only ONE chip can be active in any single gameweek.
     * Once confirmed, a chip CANNOT be cancelled or reverted.

6. **Captain & Vice-Captain Rules**:
   - The designated Captain's points are doubled (2x).
   - If the Captain plays 0 minutes, the Vice-Captain's points are doubled (2x).
   - If neither the Captain nor the Vice-Captain plays any minutes, no player receives double points.

7. **Substitutions & Auto-Substitution Rules**:
   - Automatic substitutions occur at the conclusion of all matches in the gameweek if a starting player played 0 minutes.
   - The starter is replaced by the highest-priority bench player (in order: 1st Sub, 2nd Sub, 3rd Sub) who played at least 1 minute, provided that formation rules remain valid (minimum 1 GKP, 3 DEF, 2 MID, 1 FWD).
   - A starting goalkeeper can ONLY be substituted by the backup goalkeeper.

8. **Official Scoring & Points System**:
   - **Playing Time**:
     * 1 to 59 minutes: 1 point.
     * 60+ minutes (excluding stoppage time): 2 points.
   - **Goals Scored**:
     * Forward (FWD): 4 points.
     * Midfielder (MID): 5 points.
     * Defender (DEF) or Goalkeeper (GKP): 6 points.
   - **Assists**: 3 points (includes fantasy assists: rebounds, saved shots, penalties/free-kicks won).
   - **Clean Sheets** (playing 60+ minutes without conceding while on pitch):
     * Goalkeeper (GKP) & Defender (DEF): 4 points.
     * Midfielder (MID): 1 point.
     * Forward (FWD): 0 points.
   - **Goals Conceded**:
     * Goalkeeper & Defender lose -1 point for every 2 goals conceded while on the pitch.
   - **Saves**:
     * Goalkeeper earns 1 point for every 3 saves made in a match.
   - **Penalties**:
     * Penalty Save (Goalkeeper): 5 points.
     * Penalty Miss: -2 points.
   - **Disciplinary & Negative Events**:
     * Yellow Card: -1 point.
     * Red Card: -3 points.
     * Own Goal: -2 points.
   - **Bonus Points System (BPS)**:
     * Top 3 performers in a match receive 3, 2, and 1 bonus points based on underlying match statistics.

9. **Gameweek Deadlines**:
   - The transfer and team selection deadline is strictly **90 minutes before the scheduled kick-off of the first match** of the gameweek.
"""


class FPLRulesEngine:
    """Helper utility for validating and checking FPL squad constraints and rules."""

    MAX_CLUB_PLAYERS = 3
    SQUAD_SIZE = 15
    STARTERS_COUNT = 11
    BENCH_COUNT = 4
    MAX_BANKED_FTS = 5

    VALID_FORMATIONS = [
        (3, 5, 2),
        (3, 4, 3),
        (4, 4, 2),
        (4, 3, 3),
        (4, 5, 1),
        (5, 3, 2),
        (5, 4, 1),
        (5, 2, 3),
    ]

    @staticmethod
    def get_rules_text() -> str:
        """Returns the complete, authoritative FPL rules text."""
        return FPL_RULES_SUMMARY

    @staticmethod
    def analyze_squad_constraints(
        starters: List[Dict[str, Any]],
        bench: List[Dict[str, Any]],
        bank: float,
        free_transfers: int = 1
    ) -> Dict[str, Any]:
        """
        Conducts a deep audit of the user's squad against all official FPL rules and constraints:
        - Team distribution & 3-per-club quota limits.
        - Position balance & formation validity.
        - Budget, bank, and squad valuation.
        - Free transfers, banking capacity, and hit implications.
        - Health & injury flags.
        """
        all_players = starters + bench
        bank = float(bank)
        ft = int(free_transfers)

        # 1. Club Quota Distribution
        club_counts: Dict[str, int] = {}
        for p in all_players:
            tm = p.get("team_short", p.get("team_name", "PL"))
            club_counts[tm] = club_counts.get(tm, 0) + 1

        clubs_at_limit = [tm for tm, cnt in club_counts.items() if cnt >= FPLRulesEngine.MAX_CLUB_PLAYERS]
        clubs_near_limit = [tm for tm, cnt in club_counts.items() if cnt == FPLRulesEngine.MAX_CLUB_PLAYERS - 1]

        # 2. Position Balance & Formation
        starter_pos_counts: Dict[str, int] = {"GKP": 0, "DEF": 0, "MID": 0, "FWD": 0}
        for p in starters:
            pos = p.get("position", "")
            if pos in starter_pos_counts:
                starter_pos_counts[pos] += 1

        curr_def = starter_pos_counts["DEF"]
        curr_mid = starter_pos_counts["MID"]
        curr_fwd = starter_pos_counts["FWD"]
        formation_tuple = (curr_def, curr_mid, curr_fwd)
        is_valid_formation = formation_tuple in FPLRulesEngine.VALID_FORMATIONS and starter_pos_counts["GKP"] == 1

        formation_str = f"{curr_def}-{curr_mid}-{curr_fwd}"

        # 3. Budget & Financial Metrics
        starters_cost = round(sum(float(p.get("cost", 0.0)) for p in starters), 1)
        bench_cost = round(sum(float(p.get("cost", 0.0)) for p in bench), 1)
        total_squad_value = round(starters_cost + bench_cost, 1)
        total_budget = round(total_squad_value + bank, 1)

        # 4. Squad Health & Availability
        flagged_starters = [
            p for p in starters
            if p.get("is_unavailable") or p.get("is_doubtful") or p.get("is_transferred_out") or p.get("chance_of_playing", 100) < 75
        ]
        flagged_bench = [
            p for p in bench
            if p.get("is_unavailable") or p.get("is_doubtful") or p.get("is_transferred_out") or p.get("chance_of_playing", 100) < 75
        ]

        # 5. Transfer & Hit Evaluation
        next_banked_ft = min(FPLRulesEngine.MAX_BANKED_FTS, ft + 1)
        is_hit_required = ft == 0
        hit_cost = 0 if ft > 0 else 4

        return {
            "squad_size": len(all_players),
            "starters_count": len(starters),
            "bench_count": len(bench),
            "bank": bank,
            "starters_value": starters_cost,
            "bench_value": bench_cost,
            "total_squad_value": total_squad_value,
            "total_budget": total_budget,
            "free_transfers": ft,
            "next_banked_ft": next_banked_ft,
            "is_max_ft_banked": ft >= FPLRulesEngine.MAX_BANKED_FTS,
            "hit_cost_if_transfer": hit_cost,
            "club_counts": club_counts,
            "clubs_at_limit": clubs_at_limit,
            "clubs_near_limit": clubs_near_limit,
            "formation": formation_str,
            "is_valid_formation": is_valid_formation,
            "starter_pos_counts": starter_pos_counts,
            "flagged_starters": flagged_starters,
            "flagged_bench": flagged_bench,
            "has_injury_crisis": len(flagged_starters) >= 2 or (len(flagged_starters) + len(flagged_bench)) >= 3
        }

    @staticmethod
    def validate_transfer(
        p_out: Dict[str, Any],
        p_in: Dict[str, Any],
        squad_players: List[Dict[str, Any]],
        bank: float
    ) -> Tuple[bool, str]:
        """
        Validates whether a specific transfer (Sell p_out, Buy p_in) is legal under all official FPL rules:
        1. Position match.
        2. Budget constraint (cost <= sell_price + bank).
        3. Club quota constraint (team_count <= 3).
        4. Player availability (active in PL, not unavailable/doubtful).
        """
        # 1. Position
        if p_out.get("position") != p_in.get("position"):
            return False, f"Position mismatch: Cannot swap {p_out.get('position')} for {p_in.get('position')}."

        # 2. Player Active in PL
        if p_in.get("is_transferred_out"):
            return False, f"{p_in.get('web_name')} is no longer active in the Premier League."
        if p_in.get("is_unavailable"):
            return False, f"{p_in.get('web_name')} is currently unavailable ({p_in.get('injury_reason', 'Injured')})."
        if p_in.get("is_doubtful") or p_in.get("chance_of_playing", 100) < 75:
            return False, f"{p_in.get('web_name')} is an injury doubt ({p_in.get('chance_of_playing', 50)}% chance)."

        # 3. Budget
        p_out_cost = float(p_out.get("cost", 0.0))
        p_in_cost = float(p_in.get("cost", 0.0))
        max_budget = round(float(bank) + p_out_cost, 2)
        if p_in_cost > max_budget:
            shortfall = round(p_in_cost - max_budget, 2)
            return False, f"Budget exceeded: {p_in.get('web_name')} costs £{p_in_cost:.1f}m, but your maximum purchasing budget is £{max_budget:.1f}m (short by £{shortfall:.1f}m)."

        # 4. Club limit (Max 3 players per club)
        p_in_team = p_in.get("team_short", p_in.get("team_name"))
        p_out_team = p_out.get("team_short", p_out.get("team_name"))

        current_club_count = sum(1 for p in squad_players if p.get("team_short", p.get("team_name")) == p_in_team)
        # If replacing a player from the same club, the count remains unchanged
        effective_count = current_club_count - 1 if p_out_team == p_in_team else current_club_count

        if effective_count >= FPLRulesEngine.MAX_CLUB_PLAYERS:
            return False, f"Club quota exceeded: You already have {current_club_count} players from {p_in_team} (maximum allowed is 3)."

        # 5. Already owned
        if any(p.get("id") == p_in.get("id") for p in squad_players):
            return False, f"{p_in.get('web_name')} is already in your squad."

        return True, "Valid transfer."

