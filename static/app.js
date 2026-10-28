/**
 * Apex FPL Recommender - Interactive UI & Data Client
 * Features:
 * 1. Automatic Gameweek 3 targeting & active squad synchronization
 * 2. In-team Tactical Substitutions (swap starters with bench conforming to formation rules)
 * 3. Dedicated Transfers & Market Tab (Prices, Total Points, xP, FotMob stats)
 * 4. Head-to-Head Player Comparison at similar positions
 * 5. Transparent xP Formula Inspector & Official FPL Rules (including -4 point deduction limits)
 * 6. Multi-season historical performance metrics
 */

let allPlayers = [];
let playersMap = {};
let currentSquad = {
  starters: [],
  bench: [],
  bank: 0.5,
  captain: null,
  vice_captain: null,
  captain_name: "",
  vice_captain_name: ""
};
let currentUser = null;
let authToken = localStorage.getItem("chatfpl_auth_token") || null;
let selectedPlayerForModal = null;
let currentSortCol = "expected_points";
let currentSortAsc = false;
let marketCurrentPage = 1;
const MARKET_PAGE_SIZE = 25;
// Utility helpers for safe numeric and fixed string formatting
function safeNum(val, fallback = 0) {
  if (typeof val === "number" && !isNaN(val)) return val;
  const parsed = parseFloat(val);
  return isNaN(parsed) ? fallback : parsed;
}
function safeFixed(val, digits = 1, fallback = 0) {
  return safeNum(val, fallback).toFixed(digits);
}

let fplButtonState = "import"; // "import" | "reset"

function setFplButtonState(state) {
  const btn = document.getElementById("btnImportFPL");
  if (!btn) return;
  fplButtonState = state;
  if (state === "reset") {
    btn.className = "btn btn-reset";
    btn.title = "Reset to template squad and clear saved FPL ID";
    btn.innerHTML = `<span class="icon">✕</span> Reset`;
  } else {
    btn.className = "btn btn-primary pulse-btn";
    btn.title = "Import squad from official FPL";
    btn.innerHTML = `<span class="icon">🔄</span> Import Squad`;
  }
}

async function resetToTemplateSquad() {
  const managerInput = document.getElementById("fplManagerId");
  if (managerInput) managerInput.value = "";

  localStorage.removeItem("apex_fpl_manager_id");
  localStorage.removeItem("chatfpl_manager_id");
  localStorage.removeItem("apex_fpl_team_name");
  localStorage.removeItem("apex_fpl_manager_name");
  localStorage.removeItem("apex_fpl_overall_rank");
  localStorage.removeItem("apex_fpl_overall_points");
  localStorage.removeItem("apex_fpl_badge_url");

  currentSquad.manager_id = null;
  currentSquad.manager_name = null;
  currentSquad.team_name = null;
  currentSquad.badge_url = null;
  currentSquad.overall_points = null;
  currentSquad.overall_rank = null;
  currentManagerBadge = null;

  const bannerLabel = document.getElementById("userTeamLabel");
  if (bannerLabel) bannerLabel.textContent = "Managing Template Squad";
  const bannerSubtitle = document.getElementById("userTeamSubtitle");
  if (bannerSubtitle) bannerSubtitle.textContent = "Click 'Sign In / Register' to link your saved FPL Manager ID";
  const bannerActionText = document.getElementById("userTeamActionText");
  if (bannerActionText) bannerActionText.textContent = "Sign In / Register";
  updateManagerBadge(null, null, null);

  setFplButtonState("import");

  await loadDefaultSquad();
  runRecommendation();
  if (typeof window.updateChatFplHistory === "function") {
    window.updateChatFplHistory();
  }
  showBanner("Squad reset to default template team.");
}

// Initialize app on load
document.addEventListener("DOMContentLoaded", async () => {
  setupNavigationTabs();
  setupEventListeners();
  initChatFPL();
  initFplCookieModal();
  initOnboardingModal();

  // 1. Instantly load Gameweek info
  await loadGameweekInfo();

  // 2. Restore saved FPL Manager ID and cached team profile from localStorage across page reloads
  const hasSyncHash = window.location.hash && window.location.hash.includes("fpl_sync=");
  const savedManagerId = localStorage.getItem("apex_fpl_manager_id") || localStorage.getItem("chatfpl_manager_id");
  const savedTeamName = localStorage.getItem("apex_fpl_team_name");
  const savedManagerName = localStorage.getItem("apex_fpl_manager_name");
  const savedRank = localStorage.getItem("apex_fpl_overall_rank");
  const savedPoints = localStorage.getItem("apex_fpl_overall_points");
  const savedBadge = localStorage.getItem("apex_fpl_badge_url");

  const inputId = document.getElementById("fplManagerId");
  if (inputId && savedManagerId && !hasSyncHash) {
    inputId.value = savedManagerId;
  }

  if (savedManagerId && !hasSyncHash) {
    setFplButtonState("reset");
    currentSquad.manager_id = savedManagerId;
    if (savedTeamName) currentSquad.team_name = savedTeamName;
    if (savedManagerName) currentSquad.manager_name = savedManagerName;
    if (savedRank) currentSquad.overall_rank = savedRank;
    if (savedPoints !== null && savedPoints !== undefined) currentSquad.overall_points = savedPoints;
    if (savedBadge) currentSquad.badge_url = savedBadge;

    // Immediately update banner with saved manager details so user sees their team without delay
    updateAuthUI(currentUser);
  }

  // 3. Check for 1-Click Bookmarklet Sync Hash payload
  let didSyncFromHash = false;
  if (hasSyncHash) {
    didSyncFromHash = await handleFplBookmarkletSync();
  }

  // 4. Load squad: if saved manager ID exists, auto-sync their squad; otherwise load template squad
  let didLoadSavedSquad = false;
  if (!didSyncFromHash && savedManagerId) {
    didLoadSavedSquad = await importManagerSquad(savedManagerId, null, true);
    if (didLoadSavedSquad) {
      setFplButtonState("reset");
    }
  }
  if (!didSyncFromHash && !didLoadSavedSquad) {
    setFplButtonState("import");
    await loadDefaultSquad();
    runRecommendation();
  }

  // 5. Authenticate session if token exists
  await initAuth();

  // 5. Preload secondary datasets in background so initial UI is immediately interactive
  loadInjuries();
  preloadAllPlayers();

  // Auto-sync whenever the page loads
  autoSyncPipeline();
});

// 0. Load Gameweek Status
async function loadGameweekInfo() {
  try {
    const res = await fetch("/api/gameweek");
    const data = await res.json();
    currentGwInfo = data;
    const gwNum = data.target_gameweek || 6;
    document.getElementById("navGwText").textContent = `Gameweek ${gwNum}`;
    const badge = document.getElementById("verdictBadge");
    if (badge) badge.textContent = `GAMEWEEK ${gwNum}`;
  } catch (e) {
    console.error("Gameweek fetch error:", e);
  }
}

// 1. Fetch Players Database (On-demand & background)
let allPlayersPromise = null;
function ensureAllPlayersLoaded() {
  if (allPlayers && allPlayers.length > 0) {
    return Promise.resolve(allPlayers);
  }
  if (allPlayersPromise) {
    return allPlayersPromise;
  }
  allPlayersPromise = fetch("/api/players?t=" + Date.now(), { cache: "no-store" })
    .then(res => res.json())
    .then(data => {
      allPlayers = data.players || [];
      allPlayers.forEach(p => {
        playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
      });
      console.log(`Loaded ${allPlayers.length} players into client memory.`);
      updateCaptainPills();
      if (typeof populateMarketTeamFilter === "function") {
        populateMarketTeamFilter();
      }
      return allPlayers;
    })
    .catch(err => {
      console.error("Error loading players:", err);
      allPlayersPromise = null;
      return [];
    });
  return allPlayersPromise;
}

function preloadAllPlayers() {
  ensureAllPlayersLoaded();
}

async function loadAllPlayers() {
  return ensureAllPlayersLoaded();
}

function updateCaptainPills() {
  const capId = currentSquad.captain;
  const vcId = currentSquad.vice_captain;

  const capPlayer = (capId !== undefined && capId !== null)
    ? (playersMap[capId] || playersMap[Number(capId)] || (allPlayers && allPlayers.find(p => Number(p.id) === Number(capId))))
    : null;

  const vcPlayer = (vcId !== undefined && vcId !== null)
    ? (playersMap[vcId] || playersMap[Number(vcId)] || (allPlayers && allPlayers.find(p => Number(p.id) === Number(vcId))))
    : null;

  const capName = (capPlayer && (capPlayer.web_name || capPlayer.full_name))
    || currentSquad.captain_name
    || (currentSquad.starters && currentSquad.starters[0] && (playersMap[currentSquad.starters[0]] || playersMap[Number(currentSquad.starters[0])])
      ? (playersMap[currentSquad.starters[0]] || playersMap[Number(currentSquad.starters[0])]).web_name : "");

  const vcName = (vcPlayer && (vcPlayer.web_name || vcPlayer.full_name))
    || currentSquad.vice_captain_name
    || (currentSquad.starters && currentSquad.starters[1] && (playersMap[currentSquad.starters[1]] || playersMap[Number(currentSquad.starters[1])])
      ? (playersMap[currentSquad.starters[1]] || playersMap[Number(currentSquad.starters[1])]).web_name : "");

  const capDisplay = document.getElementById("currentCapDisplay");
  if (capDisplay) {
    capDisplay.textContent = capName ? `${capName} (C)` : "-- (C)";
  }

  const vcDisplay = document.getElementById("currentVcDisplay");
  if (vcDisplay) {
    vcDisplay.textContent = vcName ? `${vcName} (V)` : "-- (V)";
  }

  const capSubtext = document.getElementById("officialCapSubtext");
  if (capSubtext) {
    const capStr = capName || "--";
    const vcStr = vcName || "--";
    capSubtext.innerHTML = `Official Squad: <strong>${capStr} (C)</strong> • <strong>${vcStr} (V)</strong>`;
  }

  // If players dataset is still loading in background, resolve names as soon as it arrives
  if ((!capPlayer || !vcPlayer) && (currentSquad.captain || currentSquad.vice_captain)) {
    ensureAllPlayersLoaded().then(() => {
      const cP = playersMap[currentSquad.captain] || playersMap[Number(currentSquad.captain)];
      const vP = playersMap[currentSquad.vice_captain] || playersMap[Number(currentSquad.vice_captain)];
      if (cP && capDisplay) capDisplay.textContent = `${cP.web_name} (C)`;
      if (vP && vcDisplay) vcDisplay.textContent = `${vP.web_name} (V)`;
      if (capSubtext && (cP || vP)) {
        capSubtext.innerHTML = `Official Squad: <strong>${cP ? cP.web_name : (capName || '--')} (C)</strong> • <strong>${vP ? vP.web_name : (vcName || '--')} (V)</strong>`;
      }
    });
  }

  updateSquadFtDisplay();
}

function updateSquadFtDisplay() {
  const ftEl = document.getElementById("ftDisplay");
  if (ftEl) {
    const ft = currentSquad.free_transfers !== undefined ? Number(currentSquad.free_transfers) : 1;
    ftEl.textContent = `${ft} FT${ft === 1 ? "" : "s"}`;
    const pill = document.getElementById("currentFtPill");
    if (pill) {
      pill.title = `Official Available Free Transfers: ${ft} FT${ft === 1 ? "" : "s"} (under 2026/27 5-FT rolling rule)`;
    }
  }
}

function syncChipsUI(chips) {
  const chipMap = {
    wildcard: "chkWildcard",
    "3xc": "chk3xc",
    bboost: "chkBBoost",
    freehit: "chkFreeHit"
  };

  // Reset all
  Object.values(chipMap).forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.checked = false;
      const label = el.closest(".chip-chk");
      if (label) {
        label.classList.remove("chip-used");
        label.title = "Available for use";
      }
    }
  });

  if (!chips || !Array.isArray(chips)) return;

  chips.forEach(chip => {
    let chipName = null;
    let isPlayed = false;
    let eventGw = null;

    if (typeof chip === "string") {
      chipName = chip.toLowerCase();
      isPlayed = true;
    } else if (chip && typeof chip === "object") {
      chipName = (chip.name || "").toLowerCase();
      eventGw = chip.event || (chip.played_by_entry ? chip.played_by_entry[0] : null);
      isPlayed = chip.status_for_entry === "played" ||
                 Boolean(chip.event) ||
                 (chip.played_by_entry && chip.played_by_entry.length > 0) ||
                 Boolean(chip.used);
    }

    if (isPlayed && chipName && chipMap[chipName]) {
      const el = document.getElementById(chipMap[chipName]);
      if (el) {
        el.checked = true;
        const label = el.closest(".chip-chk");
        if (label) {
          label.classList.add("chip-used");
          label.title = eventGw ? `Played in Gameweek ${eventGw}` : "Used";
        }
      }
    }
  });
}

// 2. Fetch Starter Template Squad (from official FPL my-team & transfers)
async function loadDefaultSquad() {
  try {
    const res = await fetch("/api/squad/default");
    const data = await res.json();
    if (data.players && Array.isArray(data.players)) {
      data.players.forEach(p => {
        playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
      });
    }
    currentSquad.starters = data.starters || [];
    currentSquad.bench = data.bench || [];
    currentSquad.bank = data.bank !== undefined ? Number(data.bank) : 1.0;
    currentSquad.free_transfers = data.free_transfers !== undefined ? Number(data.free_transfers) : 1;
    currentSquad.free_transfers_allocated = data.free_transfers_allocated !== undefined ? Number(data.free_transfers_allocated) : 1;
    currentSquad.captain = data.captain || currentSquad.starters[0];
    currentSquad.vice_captain = data.vice_captain || currentSquad.starters[1];
    currentSquad.captain_name = data.captain_name || "";
    currentSquad.vice_captain_name = data.vice_captain_name || "";
    currentSquad.manager_id = null;
    currentSquad.manager_name = null;
    currentSquad.badge_url = null;
    currentSquad.overall_points = null;
    currentSquad.overall_rank = null;
    currentSquad.team_name = data.template_name || "Template Squad";
    currentManagerBadge = null;

    document.getElementById("bankDisplay").textContent = `£${currentSquad.bank.toFixed(1)}m`;

    const label = document.getElementById("userTeamLabel");
    if (label) label.textContent = data.template_name ? `Managing ${data.template_name}` : "Managing Template Squad";
    const subtitle = document.getElementById("userTeamSubtitle");
    if (subtitle) subtitle.textContent = "Custom lineup loaded for this session • Enter your FPL ID above to sync your team";

    updateCaptainPills();
    syncChipsUI(data.chips);

    renderPitch();
  } catch (err) {
    console.error("Error loading default squad:", err);
  }
}

// 3. Navigation Tabs & Deep Linking (SEO & Anchor Routing)
function switchView(viewId, updateHash = false) {
  if (!viewId) return;
  const targetSec = document.getElementById(viewId);
  if (!targetSec) return;

  const tabs = document.querySelectorAll(".nav-view-btn");
  tabs.forEach(t => {
    if (t.getAttribute("data-view") === viewId) {
      t.classList.add("active");
    } else {
      t.classList.remove("active");
    }
  });

  document.querySelectorAll(".view-section").forEach(sec => sec.classList.add("hidden"));
  targetSec.classList.remove("hidden");

  if (updateHash && window.location.hash !== `#${viewId}`) {
    history.pushState(null, "", `#${viewId}`);
  }

  if (viewId === "transfersMarketView") {
    loadExplorerOnDemand();
  } else if (viewId === "compareView") {
    loadCompareOnDemand();
  } else if (viewId === "multiGwView") {
    loadMultiGwStrategyOnDemand();
  }
}

function setupNavigationTabs() {
  const tabs = document.querySelectorAll(".nav-view-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const viewId = tab.getAttribute("data-view");
      switchView(viewId, true);
    });
  });

  // Footer navigation links with data-view
  document.querySelectorAll(".footer-nav-link[data-view]").forEach(link => {
    link.addEventListener("click", (e) => {
      const viewId = link.getAttribute("data-view");
      if (viewId) {
        e.preventDefault();
        switchView(viewId, true);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  });

  // Handle URL hash changes (deep linking from search engines / bookmarks)
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.replace("#", "");
    if (hash) {
      switchView(hash, false);
    }
  });

  // Handle initial page load hash
  const initialHash = window.location.hash.replace("#", "");
  if (initialHash && document.getElementById(initialHash)) {
    switchView(initialHash, false);
  }
}

let isExplorerRendered = false;
async function loadExplorerOnDemand() {
  if (!allPlayers || allPlayers.length === 0) {
    const tbody = document.getElementById("marketTableBody");
    if (tbody && !isExplorerRendered) {
      tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;padding:2.5rem;color:var(--text-muted);font-size:0.95rem;">⏳ Loading Player Pool stats on demand...</td></tr>`;
    }
    await ensureAllPlayersLoaded();
  }
  renderMarketTable();
  isExplorerRendered = true;
}

let isCompareInitialized = false;
async function loadCompareOnDemand() {
  if (!allPlayers || allPlayers.length === 0) {
    const layout = document.getElementById("compareCardsLayout");
    if (layout && !isCompareInitialized) {
      layout.innerHTML = `<div class="empty-state-hint" style="padding:2.5rem;text-align:center;color:var(--text-muted);font-size:0.95rem;">⏳ Loading player stats for head-to-head comparison...</div>`;
    }
    await ensureAllPlayersLoaded();
  }
  if (!isCompareInitialized) {
    populateCompareDropdowns();
    setupCompareSearchListeners();
    isCompareInitialized = true;
  }
  renderComparison();
}

// ============================================================================
// Multi-Gameweek Strategy & Transfer Planner Logic
// ============================================================================
let currentHorizon = 5;
let maxAvailableHorizon = 33;
let currentStartGw = 6;
let currentMultiGwData = null;
let currentScoutingPosFilter = "ALL";
let currentScoutingSearch = "";
let isMultiGwInitialized = false;

async function loadMultiGwStrategyOnDemand(forceReload = false) {
  if (!isMultiGwInitialized) {
    setupMultiGwControls();
    isMultiGwInitialized = true;
  }
  if (!currentMultiGwData || forceReload) {
    await fetchAndRenderMultiGwStrategy();
  }
}

function setupMultiGwControls() {
  // 1. Planning Horizon Dropdown (triggers real-time multi-gameweek simulation on change)
  const dropdown = document.getElementById("mgwHorizonSelect");
  if (dropdown) {
    dropdown.addEventListener("change", () => {
      const val = parseInt(dropdown.value, 10);
      if (!isNaN(val)) {
        setPlanningHorizon(val);
      }
    });
  }

  // 2. Search filter input (Pos pills removed per user request)

  // 3. Search filter input
  const searchInput = document.getElementById("mgwSearchInput");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      currentScoutingSearch = (e.target.value || "").toLowerCase().trim();
      mgwNext5CurrentPage = 1;
      renderMultiGwNext5Table();
    });
  }

  // 4. Next 5 GWs Table Sorting by column headers
  const mgwSortThs = document.querySelectorAll("#mgwNext5HeaderRow th[data-mgw-sort]");
  mgwSortThs.forEach(th => {
    th.addEventListener("click", () => {
      const col = th.getAttribute("data-mgw-sort");
      if (currentMgwNext5SortCol === col) {
        currentMgwNext5SortAsc = !currentMgwNext5SortAsc;
      } else {
        currentMgwNext5SortCol = col;
        // Default ascending for string columns, descending for projected points
        currentMgwNext5SortAsc = (col === "web_name" || col === "team_short");
      }
      mgwNext5CurrentPage = 1;
      renderMultiGwNext5Table();
    });
  });
}

function setPlanningHorizon(h) {
  h = Math.max(1, Math.min(maxAvailableHorizon, parseInt(h, 10) || 5));
  if (h === currentHorizon && currentMultiGwData) return;
  currentHorizon = h;
  mgwNext5CurrentPage = 1;
  syncHorizonControlsUI();
  fetchAndRenderMultiGwStrategy();
}

function syncHorizonControlsUI() {
  const dropdown = document.getElementById("mgwHorizonSelect");
  if (dropdown) {
    dropdown.value = currentHorizon;
  }
}

function populateHorizonDropdown(startGw) {
  const dropdown = document.getElementById("mgwHorizonSelect");
  if (!dropdown) return;

  const currentVal = currentHorizon;
  dropdown.innerHTML = "";
  for (let h = 1; h <= maxAvailableHorizon; h++) {
    const endGw = startGw + h - 1;
    const opt = document.createElement("option");
    opt.value = h;
    let label = "";
    if (h === 1) {
      label = `1 GW (GW ${startGw})`;
    } else if (h === maxAvailableHorizon) {
      label = `${h} GWs (Through GW 38 • Season End)`;
    } else {
      label = `${h} GWs (GW ${startGw}–${endGw})`;
    }
    opt.textContent = label;
    dropdown.appendChild(opt);
  }
  dropdown.value = currentVal;
}

async function fetchAndRenderMultiGwStrategy() {
  const roadmapEl = document.getElementById("mgwRoadmapContainer");
  const next5Body = document.getElementById("mgwNext5Body");
  const totalXpEl = document.getElementById("mgwTotalXp");
  const netGainEl = document.getElementById("mgwNetGainBadge");
  const tvChangeEl = document.getElementById("mgwTeamValueChange");
  const bankChangeEl = document.getElementById("mgwBankChange");
  const ftTrajEl = document.getElementById("mgwFtTrajectory");

  if (totalXpEl) totalXpEl.textContent = "...";
  if (netGainEl) netGainEl.textContent = "Simulating...";
  if (tvChangeEl) tvChangeEl.textContent = "Calculating...";
  if (bankChangeEl) bankChangeEl.textContent = "Calculating...";
  if (ftTrajEl) ftTrajEl.textContent = "...";

  if (roadmapEl) {
    roadmapEl.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2.5rem; color: var(--text-muted); font-size: 0.95rem;">
      ⏳ Simulating ${currentHorizon}-Gameweek transfer trajectories, roll decisions & power-up timing...
    </div>`;
  }
  if (next5Body) {
    next5Body.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 2.5rem; color: var(--text-muted); font-size: 0.95rem;">
      ⏳ Calculating expected points across upcoming 5 gameweeks...
    </td></tr>`;
  }

  try {
    const payload = {
      starters: currentSquad.starters,
      bench: currentSquad.bench,
      bank: currentSquad.bank !== undefined ? currentSquad.bank : 0.5,
      free_transfers: currentSquad.free_transfers !== undefined ? currentSquad.free_transfers : 1,
      chips_used: currentSquad.chips_used || {},
      horizon: currentHorizon
    };

    const resp = await fetch("/api/strategy/multi-gameweek", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!resp.ok) throw new Error(`Strategy API returned ${resp.status}`);
    const data = await resp.json();
    currentMultiGwData = data;

    if (data.start_gameweek) currentStartGw = data.start_gameweek;
    if (data.max_horizon) maxAvailableHorizon = data.max_horizon;
    else maxAvailableHorizon = Math.max(1, 38 - currentStartGw + 1);

    populateHorizonDropdown(currentStartGw);
    syncHorizonControlsUI();

    const subheading = document.querySelector(".mgw-subheading");
    if (subheading) {
      subheading.textContent = `Step-by-step game plan across your chosen horizon (GW ${data.start_gameweek}–GW ${data.end_gameweek} • ${data.horizon} Gameweek${data.horizon === 1 ? '' : 's'})`;
    }

    renderMultiGwKpis(data);
    renderMultiGwRoadmap(data.roadmap || []);
    renderMultiGwNext5Table();
  } catch (err) {
    console.error("Multi-GW strategy fetch error:", err);
    if (roadmapEl) {
      roadmapEl.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: #f87171;">
        ⚠️ Failed to simulate multi-gameweek strategy: ${err.message}. Please retry.
      </div>`;
    }
    if (next5Body) {
      next5Body.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 2rem; color: #f87171;">
        ⚠️ Error loading 5-Gameweek xP projections: ${err.message}.
      </td></tr>`;
    }
  }
}

function renderMultiGwKpis(data) {
  const totalXpEl = document.getElementById("mgwTotalXp");
  const netGainEl = document.getElementById("mgwNetGainBadge");
  const baselineSub = document.getElementById("mgwBaselineSubtext");
  const bankChangeEl = document.getElementById("mgwBankChange");
  const bankChangeSub = document.getElementById("mgwBankChangeSubtext");
  const ftTrajEl = document.getElementById("mgwFtTrajectory");
  const ftSub = document.getElementById("mgwFtSubtext");

  const totalHitCost = (data.total_hit_cost !== undefined)
    ? data.total_hit_cost
    : ((data.summary && data.summary.total_hit_cost) ? data.summary.total_hit_cost : 0);

  if (totalXpEl) {
    totalXpEl.textContent = `${data.optimized_total_xp} xP`;
    totalXpEl.removeAttribute("title");
  }
  if (netGainEl) {
    const sign = data.net_xp_gain >= 0 ? "+" : "";
    netGainEl.textContent = `${sign}${data.net_xp_gain} xP Uplift`;
    if (totalHitCost > 0) {
      netGainEl.title = `Net uplift incorporates -${totalHitCost} in transfer hits`;
    }
  }
  if (baselineSub) {
    const hitSuffix = totalHitCost > 0 ? ` • Net of -${totalHitCost} hit deduction${totalHitCost === 4 ? '' : 's'}` : '';
    baselineSub.textContent = `vs ${data.baseline_total_xp} xP status quo (GW${data.start_gameweek}–GW${data.end_gameweek})${hitSuffix}`;
  }
  // 2. Change in Team Value Projected KPI Card
  const tvChangeEl = document.getElementById("mgwTeamValueChange");
  const tvSubEl = document.getElementById("mgwTeamValueSubtext");

  const initialTV = typeof data.initial_team_value === 'number' ? data.initial_team_value : 100.0;
  const endingTV = typeof data.ending_team_value === 'number' ? data.ending_team_value : (data.roadmap && data.roadmap.length > 0 && typeof data.roadmap[data.roadmap.length - 1].team_value === 'number' ? data.roadmap[data.roadmap.length - 1].team_value : initialTV);
  const tvDelta = typeof data.team_value_change === 'number' ? data.team_value_change : Number((endingTV - initialTV).toFixed(2));
  const tvSign = tvDelta > 0 ? "+" : (tvDelta < 0 ? "-" : "");

  if (tvChangeEl) {
    tvChangeEl.textContent = `${tvSign}£${Math.abs(tvDelta).toFixed(1)}m`;
    if (tvDelta > 0) {
      tvChangeEl.style.color = "var(--fpl-green)";
    } else if (tvDelta < 0) {
      tvChangeEl.style.color = "#fb923c";
    } else {
      tvChangeEl.style.color = "#ffffff";
    }
  }

  if (tvSubEl) {
    tvSubEl.textContent = `£${initialTV.toFixed(1)}m start ➔ £${endingTV.toFixed(1)}m projected`;
  }

  // 3. Change in Money in Bank KPI Card
  const rm = data.roadmap || [];
  const initialBank = typeof data.current_bank === 'number' ? data.current_bank : ((currentSquad && currentSquad.bank) ? currentSquad.bank : 0.0);
  let endingBank = initialBank;
  if (rm.length > 0) {
    const lastStep = rm[rm.length - 1];
    endingBank = lastStep.bank_remaining !== undefined ? lastStep.bank_remaining : (lastStep.bank !== undefined ? lastStep.bank : initialBank);
  }
  const bankDelta = Number((endingBank - initialBank).toFixed(2));
  const deltaSign = bankDelta > 0 ? "+" : (bankDelta < 0 ? "-" : "");

  if (bankChangeEl) {
    bankChangeEl.textContent = `${deltaSign}£${Math.abs(bankDelta).toFixed(1)}m`;
    if (bankDelta > 0) {
      bankChangeEl.style.color = "var(--fpl-green)";
    } else if (bankDelta < 0) {
      bankChangeEl.style.color = "#fb923c";
    } else {
      bankChangeEl.style.color = "#ffffff";
    }
  }

  if (bankChangeSub) {
    bankChangeSub.textContent = `£${initialBank.toFixed(1)}m start ➔ £${endingBank.toFixed(1)}m projected`;
  }

  // 4. FT Bank Trajectory KPI Card (Zero mention of money in bank)
  const initFt = (data.free_transfers_initial !== undefined && data.free_transfers_initial !== null) ? Number(data.free_transfers_initial) : 1;
  const endFt = rm.length > 0 ? rm[rm.length - 1].ft_ending : initFt;

  if (ftTrajEl) {
    ftTrajEl.textContent = `${initFt} FT${initFt === 1 ? '' : 's'} ➔ ${endFt} FT${endFt === 1 ? '' : 's'}`;
  }
  if (ftSub) {
    const netFt = endFt - initFt;
    if (netFt > 0) {
      ftSub.textContent = `+${netFt} Free Transfer${netFt === 1 ? '' : 's'} banked • 5-FT cap rule`;
    } else if (netFt === 0) {
      ftSub.textContent = `Transfer neutral • 5-FT roll rule`;
    } else {
      ftSub.textContent = `${Math.abs(netFt)} Free Transfer${Math.abs(netFt) === 1 ? '' : 's'} net used`;
    }
  }
}

function renderMultiGwRoadmap(roadmap) {
  const container = document.getElementById("mgwRoadmapContainer");
  if (!container) return;
  if (!roadmap || roadmap.length === 0) {
    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 2rem;">No strategy steps available.</div>`;
    return;
  }

  container.innerHTML = roadmap.map(step => {
    let actionClass = "action-roll";
    let actionBadgeClass = "badge-action-roll";
    let actionIcon = "🟢";
    let actionLabel = "Roll Free Transfer";

    const numMoves = step.transfers_out ? step.transfers_out.length : 0;

    // Detect if chip is active in this gameweek
    const rawChip = (step.chip_played || step.chip || "").toString().toLowerCase();
    const actionStr = (step.action || "").toString();
    const isChipAction = actionStr.startsWith("CHIP") || rawChip.length > 0;

    let chipCode = null;
    let chipLabel = "";
    let chipIcon = "🟣";
    let chipBadgeClass = "badge-action-chip";

    if (rawChip.includes("3xc") || rawChip.includes("tc") || rawChip.includes("triple") || actionStr.includes("TRIPLE_CAPTAIN")) {
      chipCode = "tc";
      chipLabel = "Triple Captain";
      chipIcon = "🔥";
      chipBadgeClass = "badge-action-chip badge-chip-tc";
    } else if (rawChip.includes("bboost") || rawChip.includes("bb") || rawChip.includes("bench") || actionStr.includes("BENCH_BOOST")) {
      chipCode = "bb";
      chipLabel = "Bench Boost";
      chipIcon = "⚡";
      chipBadgeClass = "badge-action-chip badge-chip-bb";
    } else if (rawChip.includes("freehit") || rawChip.includes("fh") || rawChip.includes("free") || actionStr.includes("FREE_HIT")) {
      chipCode = "fh";
      chipLabel = "Free Hit";
      chipIcon = "🔄";
      chipBadgeClass = "badge-action-chip badge-chip-fh";
    } else if (rawChip.includes("wildcard") || rawChip.includes("wc") || rawChip.includes("wild") || actionStr.includes("WILDCARD")) {
      chipCode = "wc";
      chipLabel = "Wildcard";
      chipIcon = "🃏";
      chipBadgeClass = "badge-action-chip badge-chip-wc";
    } else if (isChipAction) {
      chipCode = "chip";
      chipLabel = step.chip_played ? step.chip_played.toUpperCase() : "Chip Active";
      chipIcon = "🟣";
      chipBadgeClass = "badge-action-chip";
    }

    if (numMoves === 2 || actionStr === "DOUBLE_TRANSFER") {
      actionClass = "action-transfer";
      actionBadgeClass = (step.hits_taken > 0 || step.hit_cost > 0) ? "badge-action-hit" : "badge-action-double-transfer";
      actionIcon = "🟠";
      actionLabel = (step.hits_taken > 0 || step.hit_cost > 0) ? `Double Transfer (-${step.hit_cost || (step.hits_taken * 4)} Hit)` : "Double Transfer";
    } else if (numMoves === 3 || actionStr === "TRIPLE_TRANSFER") {
      actionClass = "action-transfer";
      actionBadgeClass = (step.hits_taken > 0 || step.hit_cost > 0) ? "badge-action-hit" : "badge-action-triple-transfer";
      actionIcon = "🟠";
      actionLabel = (step.hits_taken > 0 || step.hit_cost > 0) ? `Triple Transfer (-${step.hit_cost || (step.hits_taken * 4)} Hit)` : "Triple Transfer";
    } else if (numMoves > 3 || actionStr === "MULTIPLE_TRANSFERS") {
      actionClass = "action-transfer";
      actionBadgeClass = (step.hits_taken > 0 || step.hit_cost > 0) ? "badge-action-hit" : "badge-action-double-transfer";
      actionIcon = "🟠";
      actionLabel = `${numMoves} Transfers`;
    } else if (numMoves === 1 || actionStr === "TRANSFER") {
      actionClass = "action-transfer";
      actionBadgeClass = (step.hits_taken > 0 || step.hit_cost > 0) ? "badge-action-hit" : "badge-action-transfer";
      actionIcon = "🟡";
      actionLabel = (step.hits_taken > 0 || step.hit_cost > 0) ? "Transfer Move (-4 Hit)" : "Transfer Move";
    } else if (isChipAction) {
      actionClass = "action-chip";
      actionBadgeClass = chipBadgeClass;
      actionIcon = chipIcon;
      actionLabel = chipLabel || "Chip Active";
    } else {
      actionClass = "action-roll";
      actionBadgeClass = "badge-action-roll";
      actionIcon = "🟢";
      actionLabel = (step.ft_starting >= 5) ? "Max 5 FTs Banked" : (step.ft_starting === 0 ? "Roll to 1 FT" : "Roll Free Transfer");
    }

    // Build Badges HTML (allowing double tagging if both transfer move and chip played)
    let badgesHtml = `
      <span class="roadmap-action-badge ${actionBadgeClass}">
        <span>${actionIcon}</span> ${actionLabel}
      </span>
    `;

    // Double tagging: if there were transfer moves AND a chip was active
    if (chipCode && numMoves > 0) {
      actionClass += " action-chip";
      badgesHtml += `
        <span class="roadmap-action-badge ${chipBadgeClass}">
          <span>${chipIcon}</span> ${chipLabel}
        </span>
      `;
    }

    // Sanitize title to remove any bracketed chip prefixes like [BB], [TC], [WC], [FH]
    let cleanTitle = (step.action_title || "")
      .replace(/^[\u{1F525}\u{26A1}\u{1F0CF}\u{1F504}\u{1F7E0}-\u{1F7EB}\u{26AA}\u{26AB}\s]*\[(BB|TC|WC|FH)\]\s*/iu, "")
      .replace(/^\[(BB|TC|WC|FH)\]\s*/i, "");
    if (!cleanTitle && step.action_title) cleanTitle = step.action_title;

    let transfersHtml = "";
    if (step.transfers_out && step.transfers_out.length > 0 && step.transfers_in && step.transfers_in.length > 0) {
      transfersHtml = `
        <div class="roadmap-list-transfers">
          ${step.transfers_out.map((outP, i) => {
            const inP = step.transfers_in[i] || {};
            const outCost = typeof outP.cost === 'number' ? outP.cost.toFixed(1) : Number(outP.cost || 0).toFixed(1);
            const inCost = typeof inP.cost === 'number' ? inP.cost.toFixed(1) : Number(inP.cost || 0).toFixed(1);
            return `
              <div class="transfer-pill-row">
                <span class="transfer-pill-out">OUT: ${escapeHtml(outP.web_name)} (£${outCost}m)</span>
                <span class="transfer-arrow">➔</span>
                <span class="transfer-pill-in">IN: ${escapeHtml(inP.web_name)} (£${inCost}m)</span>
              </div>
            `;
          }).join('')}
        </div>
      `;
    }

    return `
      <div class="roadmap-list-item ${actionClass}">
        <!-- Left: GW & Action / Chip Badges (Double Tagging Support) -->
        <div class="roadmap-list-col-left">
          <div class="roadmap-gw-badge">GW ${step.gameweek}</div>
          ${badgesHtml}
        </div>

        <!-- Center: Action Headline, Reasoning, and Transfers -->
        <div class="roadmap-list-col-main">
          <div class="roadmap-list-title">${escapeHtml(cleanTitle)}</div>
          <div class="roadmap-list-desc">${escapeHtml(step.action_desc)}</div>
          ${transfersHtml}
        </div>

        <!-- Right: Gameweek Key Metrics -->
        <div class="roadmap-list-col-stats">
          <div class="roadmap-list-stat-chip" title="Recommended Captain">
            <span class="stat-chip-label">Captain</span>
            <span class="stat-chip-val">${step.captain ? escapeHtml(step.captain.web_name) : '--'}</span>
          </div>
          <div class="roadmap-list-stat-chip" title="Net Projected xP for GW ${step.gameweek}">
            <span class="stat-chip-label">Net Projected xP</span>
            <span class="stat-chip-val" style="color:var(--fpl-cyan);">
              ${step.projected_xi_xp} xP
            </span>
          </div>
          <div class="roadmap-list-stat-chip" title="Money in the Bank after transfers">
            <span class="stat-chip-label">In Bank</span>
            <span class="stat-chip-val" style="color:var(--fpl-green);font-weight:700;">£${((step.bank_remaining !== undefined ? step.bank_remaining : (step.bank !== undefined ? step.bank : 0.0))).toFixed(1)}m</span>
          </div>
          <div class="roadmap-list-stat-chip" title="Free Transfers banked entering the following Gameweek">
            <span class="stat-chip-label">Banked FTs</span>
            <span class="stat-chip-val" style="color:var(--fpl-green);">${step.ft_ending} FT${Number(step.ft_ending) === 1 ? '' : 's'}</span>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// Helper: Determine injury status and severity (Red vs Yellow vs Green Returnee)
function getPlayerInjuryInfo(p) {
  if (!p) return { isInjured: false, severity: null, badgeHtml: '', chance: 100, reason: '' };
  
  const chance = p.chance_of_playing_next_round !== undefined && p.chance_of_playing_next_round !== null 
    ? Number(p.chance_of_playing_next_round) 
    : (p.is_unavailable ? 0 : (p.is_doubtful ? 75 : 100));

  const status = p.status || (p.is_unavailable ? 'i' : (p.is_doubtful ? 'd' : 'a'));
  const isRed = p.is_unavailable || status === 'i' || status === 's' || status === 'u' || status === 'n' || (chance !== null && !isNaN(chance) && chance <= 25);
  const isYellow = !isRed && (p.is_doubtful || status === 'd' || (chance !== null && !isNaN(chance) && chance <= 75));
  const isGreenReturnee = !isRed && !isYellow && (p.is_recent_injury_returnee || (p.status_tags && p.status_tags.includes("Returning from Injury")));

  if (isRed) {
    const label = chance !== null && !isNaN(chance) && chance <= 25 ? `${chance}%` : 'Out';
    const reason = p.injury_reason || p.injury_news || p.news || 'Unavailable / Ruled out';
    return {
      isInjured: true,
      isReturnee: false,
      severity: 'red',
      chance: chance,
      reason: reason,
      badgeHtml: `<span class="player-injury-tag injury-red" title="${escapeHtml(reason)}">🔴 ${label}</span>`
    };
  }
  
  if (isYellow) {
    const label = chance !== null && !isNaN(chance) && chance <= 75 ? `${chance}%` : 'Doubt';
    const reason = p.injury_reason || p.injury_news || p.news || 'Doubtful';
    return {
      isInjured: true,
      isReturnee: false,
      severity: 'yellow',
      chance: chance,
      reason: reason,
      badgeHtml: `<span class="player-injury-tag injury-yellow" title="${escapeHtml(reason)}">🟡 ${label}</span>`
    };
  }

  if (isGreenReturnee) {
    const ramp = p.injury_return_ramp ? Math.round(p.injury_return_ramp * 100) : 75;
    const expM = p.expected_minutes_projected || 60;
    const reason = p.injury_reason || (p.normalization_notes && p.normalization_notes[0]) || 'Recently returned from injury layoff';
    return {
      isInjured: false,
      isReturnee: true,
      severity: 'green',
      chance: 100,
      reason: reason,
      badgeHtml: `<span class="player-injury-tag injury-green" title="${escapeHtml(reason)} (Minutes Ramping: ~${expM}m)">🟢 Returning (${ramp}%)</span>`
    };
  }

  return { isInjured: false, isReturnee: false, severity: null, badgeHtml: '', chance: 100, reason: '' };
}

let currentMgwNext5SortCol = "total_5gw_xp";
let currentMgwNext5SortAsc = false;
let mgwNext5CurrentPage = 1;
const MGW_NEXT5_PAGE_SIZE = 25;

function renderMultiGwNext5Table() {
  const tbody = document.getElementById("mgwNext5Body");
  if (!tbody || !currentMultiGwData) return;

  const next5Gws = currentMultiGwData.next_5_gameweeks || [];
  // Dynamically update column header labels with actual GW numbers
  next5Gws.forEach((gwNum, idx) => {
    const th = document.getElementById(`thGw${idx}`);
    if (th) {
      th.textContent = `GW ${gwNum} xP`;
      th.title = `Sort by Gameweek ${gwNum} Expected Points`;
    }
  });

  // Update sort indicators on headers
  document.querySelectorAll("#mgwNext5HeaderRow th[data-mgw-sort]").forEach(th => {
    const col = th.getAttribute("data-mgw-sort");
    if (col === currentMgwNext5SortCol) {
      th.classList.add("active-sort");
      th.setAttribute("data-direction", currentMgwNext5SortAsc ? "asc" : "desc");
    } else {
      th.classList.remove("active-sort");
      th.removeAttribute("data-direction");
    }
  });

  const list = currentMultiGwData.next_5_gws_xp || [];
  const query = currentScoutingSearch;
  const posFilter = currentScoutingPosFilter;

  let filtered = list.filter(p => {
    // Exclude players who have been transferred out
    if (p.is_transferred_out || p.status === 'u' || p.status === 'n') return false;
    if (posFilter !== "ALL" && p.position !== posFilter) return false;
    if (query) {
      const matchName = (p.web_name || "").toLowerCase().includes(query) || (p.full_name || "").toLowerCase().includes(query);
      const matchTeam = (p.team_short || "").toLowerCase().includes(query) || (p.team_name || "").toLowerCase().includes(query);
      if (!matchName && !matchTeam) return false;
    }
    return true;
  });

  // Sort
  const posOrder = { GKP: 1, DEF: 2, MID: 3, FWD: 4 };
  filtered.sort((a, b) => {
    let res = 0;
    if (currentMgwNext5SortCol.startsWith("gw_")) {
      const idx = parseInt(currentMgwNext5SortCol.replace("gw_", ""), 10);
      const valA = (a.gw_xp && a.gw_xp[idx] !== undefined) ? a.gw_xp[idx] : 0;
      const valB = (b.gw_xp && b.gw_xp[idx] !== undefined) ? b.gw_xp[idx] : 0;
      res = valA - valB;
    } else if (currentMgwNext5SortCol === "total_5gw_xp") {
      res = (a.total_5gw_xp || 0) - (b.total_5gw_xp || 0);
    } else if (currentMgwNext5SortCol === "position") {
      const orderA = posOrder[a.position] || 99;
      const orderB = posOrder[b.position] || 99;
      res = orderA - orderB;
    } else if (currentMgwNext5SortCol === "team_short") {
      res = (a.team_short || "").localeCompare(b.team_short || "");
    } else if (currentMgwNext5SortCol === "web_name") {
      res = (a.web_name || "").localeCompare(b.web_name || "");
    } else if (currentMgwNext5SortCol === "cost") {
      const apA = allPlayers.find(x => Number(x.id) === Number(a.player_id || a.id));
      const apB = allPlayers.find(x => Number(x.id) === Number(b.player_id || b.id));
      const costA = a.cost !== undefined ? a.cost : (apA ? apA.cost : 0);
      const costB = b.cost !== undefined ? b.cost : (apB ? apB.cost : 0);
      res = costA - costB;
    } else if (currentMgwNext5SortCol === "total_points") {
      const apA = allPlayers.find(x => Number(x.id) === Number(a.player_id || a.id));
      const apB = allPlayers.find(x => Number(x.id) === Number(b.player_id || b.id));
      const ptsA = a.total_points !== undefined ? a.total_points : (apA ? apA.total_points : 0);
      const ptsB = b.total_points !== undefined ? b.total_points : (apB ? apB.total_points : 0);
      res = ptsA - ptsB;
    }
    return currentMgwNext5SortAsc ? res : -res;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;padding:2.5rem;color:var(--text-muted);">No players match the filter.</td></tr>`;
    renderMgwNext5Pagination(0, 0);
    return;
  }

  // Pagination (matching Player Pool 25 items / page)
  const totalPages = Math.ceil(filtered.length / MGW_NEXT5_PAGE_SIZE);
  if (mgwNext5CurrentPage > totalPages) mgwNext5CurrentPage = totalPages;
  if (mgwNext5CurrentPage < 1) mgwNext5CurrentPage = 1;

  const startIdx = (mgwNext5CurrentPage - 1) * MGW_NEXT5_PAGE_SIZE;
  const endIdx = Math.min(startIdx + MGW_NEXT5_PAGE_SIZE, filtered.length);
  const pageSlice = filtered.slice(startIdx, endIdx);

  tbody.innerHTML = pageSlice.map(p => {
    const ap = allPlayers.find(x => Number(x.id) === Number(p.player_id || p.id));
    const cost = p.cost !== undefined ? p.cost : (ap ? ap.cost : 0.0);
    const predDelta = p.predicted_price_change !== undefined ? p.predicted_price_change : (ap ? ap.predicted_price_change : 0.0);
    const predReason = p.price_change_reason || (ap ? ap.price_change_reason : '');
    const totalPts = p.total_points !== undefined ? p.total_points : (ap ? ap.total_points : 0);
    const injuryInfo = getPlayerInjuryInfo(ap || p);

    const gw0 = p.gw_xp && p.gw_xp[0] !== undefined ? p.gw_xp[0].toFixed(2) : "0.00";
    const gw1 = p.gw_xp && p.gw_xp[1] !== undefined ? p.gw_xp[1].toFixed(2) : "0.00";
    const gw2 = p.gw_xp && p.gw_xp[2] !== undefined ? p.gw_xp[2].toFixed(2) : "0.00";
    const gw3 = p.gw_xp && p.gw_xp[3] !== undefined ? p.gw_xp[3].toFixed(2) : "0.00";
    const gw4 = p.gw_xp && p.gw_xp[4] !== undefined ? p.gw_xp[4].toFixed(2) : "0.00";
    const total = (p.total_5gw_xp !== undefined ? p.total_5gw_xp : 0).toFixed(2);

    return `
      <tr>
        <td><span class="badge-team">${escapeHtml(p.team_short)}</span></td>
        <td><span class="badge-pos badge-${(p.position || '').toLowerCase()}">${p.position}</span></td>
        <td><strong style="color:#ffffff;">£${Number(cost).toFixed(1)}m</strong></td>
        <td><strong style="color:#ffffff;">${totalPts}</strong></td>
        <td class="td-player-name" style="cursor:pointer;" onclick="openPlayerModal(${p.player_id || p.id})">
          <strong>${escapeHtml(p.web_name)}</strong>
          ${injuryInfo.badgeHtml}
        </td>
        <td class="mgw-xp-cell">${gw0}</td>
        <td class="mgw-xp-cell">${gw1}</td>
        <td class="mgw-xp-cell">${gw2}</td>
        <td class="mgw-xp-cell">${gw3}</td>
        <td class="mgw-xp-cell">${gw4}</td>
        <td class="mgw-total-cell"><strong>${total}</strong></td>
      </tr>
    `;
  }).join('');

  renderMgwNext5Pagination(filtered.length, totalPages);
}

function renderMgwNext5Pagination(totalItems, totalPages) {
  const paginationEl = document.getElementById("mgwNext5Pagination");
  const pageNumsEl = document.getElementById("mgwNext5PageNumbers");
  const prevBtn = document.getElementById("mgwNext5PrevPage");
  const nextBtn = document.getElementById("mgwNext5NextPage");
  const infoEl = document.getElementById("mgwNext5PageInfo");

  if (!paginationEl) return;

  if (totalItems === 0 || totalPages <= 1) {
    paginationEl.style.display = totalItems === 0 ? "none" : "flex";
    if (pageNumsEl) pageNumsEl.innerHTML = "";
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    if (infoEl) infoEl.textContent = totalItems > 0 ? `${totalItems} players` : "";
    return;
  }

  paginationEl.style.display = "flex";

  // Prev / Next
  if (prevBtn) {
    prevBtn.disabled = mgwNext5CurrentPage <= 1;
    prevBtn.onclick = () => {
      if (mgwNext5CurrentPage > 1) {
        mgwNext5CurrentPage--;
        renderMultiGwNext5Table();
        scrollToMgwNext5Top();
      }
    };
  }
  if (nextBtn) {
    nextBtn.disabled = mgwNext5CurrentPage >= totalPages;
    nextBtn.onclick = () => {
      if (mgwNext5CurrentPage < totalPages) {
        mgwNext5CurrentPage++;
        renderMultiGwNext5Table();
        scrollToMgwNext5Top();
      }
    };
  }

  // Page number buttons with ellipsis
  if (pageNumsEl) {
    pageNumsEl.innerHTML = "";
    const pages = buildPageNumbers(mgwNext5CurrentPage, totalPages);
    pages.forEach(pg => {
      if (pg === "...") {
        const dot = document.createElement("span");
        dot.className = "pagination-ellipsis";
        dot.textContent = "…";
        pageNumsEl.appendChild(dot);
      } else {
        const btn = document.createElement("button");
        btn.className = "pagination-page-btn" + (pg === mgwNext5CurrentPage ? " active" : "");
        btn.textContent = pg;
        btn.addEventListener("click", () => {
          mgwNext5CurrentPage = pg;
          renderMultiGwNext5Table();
          scrollToMgwNext5Top();
        });
        pageNumsEl.appendChild(btn);
      }
    });
  }

  // Info text
  if (infoEl) {
    const start = (mgwNext5CurrentPage - 1) * MGW_NEXT5_PAGE_SIZE + 1;
    const end = Math.min(mgwNext5CurrentPage * MGW_NEXT5_PAGE_SIZE, totalItems);
    infoEl.textContent = `${start}–${end} of ${totalItems}`;
  }
}

function scrollToMgwNext5Top() {
  const el = document.getElementById("mgwNext5XpSection");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// 4. Render Pitch and Bench
function renderPitch() {
  const rowGkp = document.getElementById("rowGkp");
  const rowDef = document.getElementById("rowDef");
  const rowMid = document.getElementById("rowMid");
  const rowFwd = document.getElementById("rowFwd");
  const benchSlots = document.getElementById("benchSlots");

  rowGkp.innerHTML = "";
  rowDef.innerHTML = "";
  rowMid.innerHTML = "";
  rowFwd.innerHTML = "";
  benchSlots.innerHTML = "";

  let benchXpTotal = 0;

  // Starters categorized by position
  currentSquad.starters.forEach(pid => {
    const p = playersMap[pid];
    if (!p) return;

    const card = createPlayerCard(p, false);
    if (p.position === "GKP") rowGkp.appendChild(card);
    else if (p.position === "DEF") rowDef.appendChild(card);
    else if (p.position === "MID") rowMid.appendChild(card);
    else if (p.position === "FWD") rowFwd.appendChild(card);
  });

  // Bench slots
  currentSquad.bench.forEach(pid => {
    const p = playersMap[pid];
    if (!p) return;
    benchXpTotal += p.expected_points;
    const card = createPlayerCard(p, true);
    benchSlots.appendChild(card);
  });

  document.getElementById("benchXpDisplay").textContent = `${benchXpTotal.toFixed(1)} xP`;
  updateCaptainPills();
}

// Create single interactive player element
function createPlayerCard(p, isBench) {
  const el = document.createElement("div");
  el.className = "player-card";
  if (p.is_unavailable || p.is_doubtful) el.classList.add("is-injured");
  if (Number(p.id) === Number(currentSquad.captain)) el.classList.add("is-captain");
  if (Number(p.id) === Number(currentSquad.vice_captain)) el.classList.add("is-vice");

  const kitEmoji = p.position === "GKP" ? "🧤" : "👕";
  const flagBadge = p.is_unavailable ? "🔴" : (p.is_doubtful ? "🟡" : "");

  // Next upcoming fixture & FDR rating
  let fixHtml = "";
  if (p.upcoming_fixtures && p.upcoming_fixtures.length > 0) {
    const nextFix = p.upcoming_fixtures[0];
    const opp = nextFix.opponent || "OPP";
    const venue = nextFix.is_home ? "H" : "A";
    const diff = nextFix.difficulty || 3;
    fixHtml = `<div class="p-fix-badge fdr-${diff}" title="GW ${nextFix.event}: vs ${opp} (${venue}) • FDR ${diff}">${opp} (${venue})</div>`;
  }

  const pDelta = typeof p.predicted_price_change === 'number' ? p.predicted_price_change : 0.0;
  const trendIcon = pDelta > 0 ? `<span class="pitch-trend rise" title="Predicted rise: +£${pDelta.toFixed(1)}m">▲</span>` : (pDelta < 0 ? `<span class="pitch-trend fall" title="Predicted drop: -£${Math.abs(pDelta).toFixed(1)}m">▼</span>` : '');

  el.innerHTML = `
    <div class="p-kit-icon">
      ${kitEmoji}
      ${flagBadge ? `<span class="p-flag-badge">${flagBadge}</span>` : ""}
    </div>
    <div class="p-name" title="${p.full_name}">${p.web_name}</div>
    <div class="p-meta">
      <span>${p.team_short}</span>
      <span>•</span>
      <span>£${p.cost.toFixed(1)}</span>${trendIcon}
    </div>
    ${fixHtml}
    <div class="p-xp-pill">${p.expected_points.toFixed(1)} xP</div>
  `;

  el.addEventListener("click", () => {
    openPlayerModal(p);
  });

  return el;
}



function showBanner(msg) {
  const b = document.getElementById("statusBanner");
  document.getElementById("statusBannerText").textContent = msg;
  b.classList.remove("hidden");
  setTimeout(() => b.classList.add("hidden"), 3500);
}

// 6. Run Strategy Recommendation
async function runRecommendation() {
  const payload = {
    starters: currentSquad.starters,
    bench: currentSquad.bench,
    bank: parseFloat(currentSquad.bank || 0.5),
    free_transfers: currentSquad.free_transfers !== undefined ? Number(currentSquad.free_transfers) : 1,
    chips_used: {
      wildcard: document.getElementById("chkWildcard").checked,
      "3xc": document.getElementById("chk3xc").checked,
      bboost: document.getElementById("chkBBoost").checked,
      freehit: document.getElementById("chkFreeHit").checked
    }
  };

  try {
    const res = await fetch("/api/recommend?t=" + Date.now(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    renderRecommendationResult(data);

    const mgwSec = document.getElementById("multiGwView");
    if (mgwSec && !mgwSec.classList.contains("hidden")) {
      loadMultiGwStrategyOnDemand(true);
    } else {
      currentMultiGwData = null;
    }
  } catch (err) {
    console.error("Recommendation error:", err);
  }
}

// Transfer Suggestion Carousel State
let currentTransferSuggestions = [];
let currentTransferIndex = 0;
let currentRecData = null;

function populateTransferCard(t) {
  if (!t) return;
  const pOut = t.player_out;
  const pIn = t.player_in;
  const isStarter = t.is_starter;

  const tTypePill = document.getElementById("tTypePill");
  if (tTypePill) {
    if (isStarter) {
      tTypePill.className = "t-type-pill pill-starter";
      tTypePill.innerHTML = `⚡ STARTING XI UPGRADE`;
    } else {
      tTypePill.className = "t-type-pill pill-bench";
      tTypePill.innerHTML = `🛡️ SUBSTITUTE UPGRADE`;
    }
  }

  const tBankPill = document.getElementById("tBankRemainingPill");
  if (tBankPill && t.new_bank !== undefined) {
    tBankPill.textContent = `Bank Remaining: £${t.new_bank.toFixed(1)}m`;
  }

  document.getElementById("tOutName").textContent = pOut.web_name;
  document.getElementById("tOutTeamPos").textContent = `${pOut.team_short} • ${pOut.position}`;
  document.getElementById("tOutCost").textContent = `£${pOut.cost.toFixed(1)}m`;
  document.getElementById("tOutXp").textContent = `${pOut.expected_points.toFixed(1)}`;
  document.getElementById("tOutRating").textContent = `${pOut.fotmob_rating}`;
  document.getElementById("tOutReason").textContent = pOut.is_unavailable ? pOut.injury_reason : (isStarter ? "Starting XI asset" : "Sub / Bench asset");

  document.getElementById("tInName").textContent = pIn.web_name;
  document.getElementById("tInTeamPos").textContent = `${pIn.team_short} • ${pIn.position}`;
  document.getElementById("tInCost").textContent = `£${pIn.cost.toFixed(1)}m`;
  document.getElementById("tInXp").textContent = `${pIn.expected_points.toFixed(1)}`;
  document.getElementById("tInRating").textContent = `${pIn.fotmob_rating}`;
  document.getElementById("tInReason").textContent = `FDR ${pIn.avg_fdr} • xG/90: ${pIn.xg_90}`;

  document.getElementById("tGainBadge").textContent = `+${t.xp_gain.toFixed(1)} xP`;

  const hitBadge = document.getElementById("tHitBadge");
  if (hitBadge) {
    if (t.fpl_point_deduction < 0) {
      hitBadge.classList.remove("hidden");
    } else {
      hitBadge.classList.add("hidden");
    }
  }

  const btnCompare = document.getElementById("btnCompareTransfer");
  if (btnCompare) {
    if (pOut && pIn) {
      btnCompare.classList.remove("hidden");
      btnCompare.title = `Compare ${pOut.web_name} vs ${pIn.web_name} in Head-to-Head`;
    } else {
      btnCompare.classList.add("hidden");
    }
  }
}

function updateTransferCarouselUI() {
  const transferCardsContainer = document.getElementById("transferCardsContainer");
  const btnPrev = document.getElementById("btnPrevTransfer");
  const btnNext = document.getElementById("btnNextTransfer");
  const verdictHeadline = document.getElementById("verdictHeadline");

  if (!currentTransferSuggestions || currentTransferSuggestions.length === 0) {
    if (transferCardsContainer) transferCardsContainer.classList.add("hidden");
    return;
  }

  if (transferCardsContainer) transferCardsContainer.classList.remove("hidden");

  if (currentTransferSuggestions.length > 1) {
    if (btnPrev) btnPrev.classList.remove("hidden");
    if (btnNext) btnNext.classList.remove("hidden");
  } else {
    if (btnPrev) btnPrev.classList.add("hidden");
    if (btnNext) btnNext.classList.add("hidden");
  }

  if (currentTransferIndex < 0) currentTransferIndex = 0;
  if (currentTransferIndex >= currentTransferSuggestions.length) {
    currentTransferIndex = currentTransferSuggestions.length - 1;
  }

  const currentMove = currentTransferSuggestions[currentTransferIndex];
  populateTransferCard(currentMove);

  if (btnPrev) {
    btnPrev.disabled = currentTransferIndex <= 0;
  }
  if (btnNext) {
    btnNext.disabled = currentTransferIndex >= currentTransferSuggestions.length - 1;
  }

  if (verdictHeadline) {
    verdictHeadline.textContent = `Option ${currentTransferIndex + 1}`;
  }
}

// Render Recommendation Details
function renderRecommendationResult(rec) {
  currentRecData = rec;
  const verdictBadge = document.getElementById("verdictBadge");
  const verdictHeadline = document.getElementById("verdictHeadline");
  const verdictText = document.getElementById("verdictText");
  const transferCardsContainer = document.getElementById("transferCardsContainer");

  verdictHeadline.textContent = rec.verdict_title || "Evaluating Strategy...";

  // Helper to check if a transfer qualifies under user requirement:
  // "don't display a transfer unless it gets you at least 1m more in the bank or 1xp gain"
  function qualifiesTransfer(t) {
    if (!t || !t.player_out || !t.player_in) return false;
    const xpGain = (t.xp_gain !== undefined ? t.xp_gain : 0);
    const costOut = typeof t.player_out.cost === "number" ? t.player_out.cost : parseFloat(t.player_out.cost || 0);
    const costIn = typeof t.player_in.cost === "number" ? t.player_in.cost : parseFloat(t.player_in.cost || 0);
    const bankGain = Math.round((costOut - costIn) * 10) / 10;
    return (xpGain >= 0.999) || (bankGain >= 0.999 && xpGain >= 0.0);
  }

  // Build currentTransferSuggestions array, strictly filtering for >= 1m bank or >= 1.0 xP gain
  currentTransferSuggestions = [];
  if (rec.transfer && qualifiesTransfer(rec.transfer)) {
    currentTransferSuggestions.push(rec.transfer);
  }
  if (Array.isArray(rec.alternative_transfers)) {
    rec.alternative_transfers.forEach(alt => {
      if (alt && qualifiesTransfer(alt)) {
        const exists = currentTransferSuggestions.some(
          s => s.player_out && s.player_in &&
               s.player_out.id === alt.player_out.id &&
               s.player_in.id === alt.player_in.id
        );
        if (!exists) {
          currentTransferSuggestions.push(alt);
        }
      }
    });
  }

  // USER REQUIREMENT: Always display a random number of transfer suggestions between 15-25
  if (currentTransferSuggestions.length > 15) {
    const randomTarget = Math.floor(Math.random() * (25 - 15 + 1)) + 15; // 15 to 25 inclusive
    if (currentTransferSuggestions.length > randomTarget) {
      currentTransferSuggestions = currentTransferSuggestions.slice(0, randomTarget);
    }
  }

  currentTransferIndex = 0;

  // STRICT USER REQUIREMENT: Don't display transfer unless it gets you at least 1m more in the bank or 1xp gain
  const hasViableTransfer = !rec.is_no_transfer && currentTransferSuggestions.length > 0;

  // Retain exact original badge style and write "GAMEWEEK 6" (or target gameweek)
  const gwNum = rec.target_gameweek || rec.gameweek || (currentGwInfo && currentGwInfo.target_gameweek) || 6;
  if (verdictBadge) {
    verdictBadge.className = hasViableTransfer ? "badge badge-warning" : "badge badge-success";
    verdictBadge.textContent = `GAMEWEEK ${gwNum}`;
  }

  if (!hasViableTransfer) {
    const explanation = rec.verdict_explanation || "Starting lineup is healthy and optimal. No move provides >= 1.0 xP gain or frees up >= £1.0m in the bank. Roll free transfer for next gameweek.";
    verdictHeadline.textContent = rec.verdict_title || "Roll Free Transfer";
    verdictText.classList.remove("hidden");
    verdictText.textContent = explanation;
    // Strictly hide transfer cards carousel when holding, rolling, or no move meets >= 1m bank or >= 1 xP threshold!
    if (transferCardsContainer) {
      transferCardsContainer.classList.add("hidden");
    }
    currentTransferSuggestions = [];
  } else {
    if (rec.verdict_explanation) {
      verdictText.classList.remove("hidden");
      verdictText.textContent = rec.verdict_explanation;
    } else {
      verdictText.classList.add("hidden");
      verdictText.textContent = "";
    }
    if (transferCardsContainer) {
      transferCardsContainer.classList.remove("hidden");
    }
    updateTransferCarouselUI();
  }

  // Chips Advisor
  const chip = rec.chip_recommendation;
  document.getElementById("chipTitle").textContent = chip.title;
  document.getElementById("chipReason").textContent = chip.reason;
  if (chip.recommended) {
    document.getElementById("chipTitle").style.color = "#fbbf24";
  } else {
    document.getElementById("chipTitle").style.color = "#00ff87";
  }

  // Captaincy Recommendation (Displays recommendations without modifying the manager's official FPL lineup)
  if (rec.captain && rec.captain.player) {
    const c = rec.captain.player;
    document.getElementById("capName").textContent = c.web_name;
    document.getElementById("capConfidence").textContent = `${rec.captain.confidence}% Conf.`;
    document.getElementById("capFdr").textContent = `FDR ${c.avg_fdr}`;
    document.getElementById("capXp").textContent = `xP ${c.expected_points.toFixed(1)}`;
    document.getElementById("capReason").textContent = rec.captain.reason;
  }

  if (rec.vice_captain && rec.vice_captain.player) {
    const vc = rec.vice_captain.player;
    document.getElementById("vcName").textContent = vc.web_name;
    document.getElementById("vcConfidence").textContent = `${rec.vice_captain.confidence}% Conf.`;
    document.getElementById("vcFdr").textContent = `FDR ${vc.avg_fdr}`;
    document.getElementById("vcXp").textContent = `xP ${vc.expected_points.toFixed(1)}`;
    document.getElementById("vcReason").textContent = rec.vice_captain.reason;
  }

  renderPitch();
}

// 7. Transfers & Market (Player Pool) Tab Renderer
function populateMarketTeamFilter() {
  const select = document.getElementById("marketTeamFilter");
  if (!select || !allPlayers || allPlayers.length === 0) return;
  if (select.options.length > 1) return; // already populated

  const teamsMap = new Map();
  allPlayers.forEach(p => {
    if (p.team_short && !teamsMap.has(p.team_short)) {
      teamsMap.set(p.team_short, p.team_name || p.team_short);
    }
  });

  const sortedShorts = Array.from(teamsMap.keys()).sort();
  sortedShorts.forEach(short => {
    const opt = document.createElement("option");
    opt.value = short;
    opt.textContent = `${short} (${teamsMap.get(short)})`;
    select.appendChild(opt);
  });
}

function renderPriceTrendBadge(delta, reason = '') {
  if (typeof delta !== 'number' || delta === 0) return '';
  const sign = delta > 0 ? '+' : '-';
  const cls = delta > 0 ? 'rise' : 'fall';
  const arrow = delta > 0 ? '▲' : '▼';
  return `<span class="price-trend-badge ${cls}" title="${escapeHtml(reason || (delta > 0 ? 'Predicted Price Rise' : 'Predicted Price Drop'))}">${arrow}${sign}£${Math.abs(delta).toFixed(1)}m</span>`;
}

function renderMarketTable() {
  const tbody = document.getElementById("marketTableBody");
  if (!tbody) return;
  tbody.innerHTML = "";

  populateMarketTeamFilter();

  const posFilter = document.getElementById("marketPosFilter") ? document.getElementById("marketPosFilter").value : "ALL";
  const statusFilter = document.getElementById("marketStatusFilter") ? document.getElementById("marketStatusFilter").value : "ALL";
  const teamFilter = document.getElementById("marketTeamFilter") ? document.getElementById("marketTeamFilter").value : "ALL";
  const minPriceVal = document.getElementById("marketMinPrice") && document.getElementById("marketMinPrice").value !== "" 
    ? parseFloat(document.getElementById("marketMinPrice").value) 
    : NaN;
  const maxPriceVal = document.getElementById("marketMaxPrice") && document.getElementById("marketMaxPrice").value !== "" 
    ? parseFloat(document.getElementById("marketMaxPrice").value) 
    : NaN;
  const searchFilter = (document.getElementById("marketSearchInput") ? document.getElementById("marketSearchInput").value : "").toLowerCase().trim();

  let filtered = allPlayers.filter(p => {
    // STRICT USER REQUIREMENT: Exclude players who have been transferred out
    if (p.is_transferred_out || p.status === 'u' || p.status === 'n') return false;

    // 1. Position filter
    if (posFilter !== "ALL" && p.position !== posFilter) return false;

    // 1b. Status & Availability filter
    if (statusFilter === "RETURNING" && !p.is_recent_injury_returnee) return false;
    if (statusFilter === "FLAGGED" && !(p.is_unavailable || p.is_doubtful || p.is_unlikely_to_play)) return false;
    if (statusFilter === "FIT" && (p.is_unavailable || p.is_doubtful || p.is_unlikely_to_play)) return false;

    // 2. Club/Team filter
    if (teamFilter !== "ALL" && p.team_short !== teamFilter) return false;

    // 3. Min Price filter
    if (!isNaN(minPriceVal) && p.cost < minPriceVal) return false;

    // 4. Max Price filter
    if (!isNaN(maxPriceVal) && p.cost > maxPriceVal) return false;

    // 5. Search query
    if (searchFilter) {
      const matchName = (p.full_name || p.web_name || "").toLowerCase().includes(searchFilter);
      const matchTeam = (p.team_name || "").toLowerCase().includes(searchFilter) || (p.team_short || "").toLowerCase().includes(searchFilter);
      if (!matchName && !matchTeam) return false;
    }
    return true;
  });

  // Sort
  const posOrder = { GKP: 1, DEF: 2, MID: 3, FWD: 4 };
  filtered.sort((a, b) => {
    if (currentSortCol === "position") {
      const orderA = posOrder[a.position] || 99;
      const orderB = posOrder[b.position] || 99;
      return currentSortAsc ? orderA - orderB : orderB - orderA;
    }

    let valA = a[currentSortCol];
    let valB = b[currentSortCol];
    if (valA === undefined || valA === null) valA = 0;
    if (valB === undefined || valB === null) valB = 0;

    if (typeof valA === "string") {
      return currentSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    }
    return currentSortAsc ? valA - valB : valB - valA;
  });

  // Sync header active-sort indicators
  document.querySelectorAll(".market-table th[data-sort]").forEach(th => {
    const col = th.getAttribute("data-sort");
    if (col === currentSortCol) {
      th.classList.add("active-sort");
      th.setAttribute("data-direction", currentSortAsc ? "asc" : "desc");
    } else {
      th.classList.remove("active-sort");
      th.removeAttribute("data-direction");
    }
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;padding:2.5rem;color:var(--text-muted);font-size:0.95rem;">No players match the selected filters.</td></tr>`;
    renderMarketPagination(0, 0);
    return;
  }

  // Pagination
  const totalPages = Math.ceil(filtered.length / MARKET_PAGE_SIZE);
  if (marketCurrentPage > totalPages) marketCurrentPage = totalPages;
  if (marketCurrentPage < 1) marketCurrentPage = 1;

  const startIdx = (marketCurrentPage - 1) * MARKET_PAGE_SIZE;
  const endIdx = Math.min(startIdx + MARKET_PAGE_SIZE, filtered.length);
  const pageSlice = filtered.slice(startIdx, endIdx);

  pageSlice.forEach(p => {
    const tr = document.createElement("tr");
    const injuryInfo = getPlayerInjuryInfo(p);

    const ppm = (p.points_per_game !== undefined && p.points_per_game !== null && p.points_per_game > 0)
      ? Number(p.points_per_game).toFixed(1)
      : (p.current_starts > 0 || p.current_minutes > 0 ? (p.total_points / Math.max(1, p.current_starts || 1)).toFixed(1) : '0.0');

    tr.innerHTML = `
      <td><span class="tag tag-team">${escapeHtml(p.team_short)}</span></td>
      <td><span class="tag">${p.position}</span></td>
      <td><strong style="color:#ffffff;">£${p.cost.toFixed(1)}m</strong></td>
      <td><strong style="color:#ffffff;">${p.total_points}</strong></td>
      <td><span class="m-table-pname" data-id="${p.id}">${escapeHtml(p.web_name)}</span> ${injuryInfo.badgeHtml}</td>
      <td><span class="p-xp-pill">${p.expected_points.toFixed(1)}</span></td>
      <td>${p.fotmob_rating}</td>
      <td><strong style="color:#ffffff;">${p.goals !== undefined ? p.goals : (p.goals_scored || 0)}</strong></td>
      <td><strong style="color:#ffffff;">${p.assists !== undefined ? p.assists : 0}</strong></td>
      <td><span class="ppm-val" style="color:#ffffff;font-weight:600;">${ppm}</span></td>
      <td>
        <button class="btn btn-secondary btn-sm m-inspect-btn" data-id="${p.id}">Inspect</button>
      </td>
    `;

    tr.querySelector(".m-table-pname").addEventListener("click", () => openPlayerModal(p));
    tr.querySelector(".m-inspect-btn").addEventListener("click", () => openPlayerModal(p));
    tbody.appendChild(tr);
  });

  renderMarketPagination(filtered.length, totalPages);
}

function renderMarketPagination(totalItems, totalPages) {
  const paginationEl = document.getElementById("marketPagination");
  const pageNumsEl = document.getElementById("marketPageNumbers");
  const prevBtn = document.getElementById("marketPrevPage");
  const nextBtn = document.getElementById("marketNextPage");
  const infoEl = document.getElementById("marketPageInfo");

  if (!paginationEl) return;

  if (totalItems === 0 || totalPages <= 1) {
    paginationEl.style.display = totalItems === 0 ? "none" : "flex";
    if (pageNumsEl) pageNumsEl.innerHTML = "";
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    if (infoEl) infoEl.textContent = totalItems > 0 ? `${totalItems} players` : "";
    return;
  }

  paginationEl.style.display = "flex";

  // Prev / Next
  if (prevBtn) {
    prevBtn.disabled = marketCurrentPage <= 1;
    prevBtn.onclick = () => { if (marketCurrentPage > 1) { marketCurrentPage--; renderMarketTable(); scrollToMarketTop(); } };
  }
  if (nextBtn) {
    nextBtn.disabled = marketCurrentPage >= totalPages;
    nextBtn.onclick = () => { if (marketCurrentPage < totalPages) { marketCurrentPage++; renderMarketTable(); scrollToMarketTop(); } };
  }

  // Page number buttons with ellipsis
  if (pageNumsEl) {
    pageNumsEl.innerHTML = "";
    const pages = buildPageNumbers(marketCurrentPage, totalPages);
    pages.forEach(pg => {
      if (pg === "...") {
        const dot = document.createElement("span");
        dot.className = "pagination-ellipsis";
        dot.textContent = "…";
        pageNumsEl.appendChild(dot);
      } else {
        const btn = document.createElement("button");
        btn.className = "pagination-page-btn" + (pg === marketCurrentPage ? " active" : "");
        btn.textContent = pg;
        btn.addEventListener("click", () => {
          marketCurrentPage = pg;
          renderMarketTable();
          scrollToMarketTop();
        });
        pageNumsEl.appendChild(btn);
      }
    });
  }

  // Info text
  if (infoEl) {
    const start = (marketCurrentPage - 1) * MARKET_PAGE_SIZE + 1;
    const end = Math.min(marketCurrentPage * MARKET_PAGE_SIZE, totalItems);
    infoEl.textContent = `${start}–${end} of ${totalItems}`;
  }
}

function buildPageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = [];
  pages.push(1);
  if (current > 3) pages.push("...");
  for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
    pages.push(i);
  }
  if (current < total - 2) pages.push("...");
  pages.push(total);
  return pages;
}

function scrollToMarketTop() {
  const el = document.getElementById("transfersMarketView");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// 8. Compare Players Tab
let currentCompareIdA = null;
let currentCompareIdB = null;

function populateCompareDropdowns() {
  const sorted = [...allPlayers].sort((a, b) => b.expected_points - a.expected_points);
  if (sorted.length > 0 && !currentCompareIdA) {
    currentCompareIdA = sorted[0].id;
  }
  if (sorted.length > 1 && !currentCompareIdB) {
    currentCompareIdB = sorted[1].id;
  }

  // Set initial input text values
  const inputA = document.getElementById("compareSearchA");
  const inputB = document.getElementById("compareSearchB");
  if (inputA && currentCompareIdA && playersMap[currentCompareIdA]) {
    const pA = playersMap[currentCompareIdA];
    inputA.value = `${pA.full_name || pA.web_name} (${pA.team_short} • ${pA.position} • £${safeFixed(pA.cost, 1)}m)`;
  }
  if (inputB && currentCompareIdB && playersMap[currentCompareIdB]) {
    const pB = playersMap[currentCompareIdB];
    inputB.value = `${pB.full_name || pB.web_name} (${pB.team_short} • ${pB.position} • £${safeFixed(pB.cost, 1)}m)`;
  }

  renderComparison();
}

function setupCompareSearchListeners() {
  const inputA = document.getElementById("compareSearchA");
  const inputB = document.getElementById("compareSearchB");
  const boxA = document.getElementById("compareSuggestA");
  const boxB = document.getElementById("compareSuggestB");
  const clearA = document.getElementById("clearSearchA");
  const clearB = document.getElementById("clearSearchB");

  function attachSearch(inputEl, suggestBox, clearBtn, onSelect) {
    if (!inputEl || !suggestBox) return;

    inputEl.addEventListener("focus", () => {
      showSuggestions(inputEl.value, suggestBox, onSelect);
    });

    inputEl.addEventListener("input", (e) => {
      const val = e.target.value;
      if (clearBtn) {
        clearBtn.classList.toggle("hidden", !val);
      }
      showSuggestions(val, suggestBox, onSelect);
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        inputEl.value = "";
        clearBtn.classList.add("hidden");
        inputEl.focus();
        showSuggestions("", suggestBox, onSelect);
      });
    }

    // Close suggestion on outside click
    document.addEventListener("click", (e) => {
      if (!inputEl.contains(e.target) && !suggestBox.contains(e.target)) {
        suggestBox.classList.add("hidden");
      }
    });
  }

  function showSuggestions(term, box, onSelect) {
    const clean = (term || "").toLowerCase().trim();
    const matches = allPlayers.filter(p => {
      if (!clean) return true;
      return (p.web_name.toLowerCase().includes(clean) ||
              p.full_name.toLowerCase().includes(clean) ||
              p.team_short.toLowerCase().includes(clean) ||
              p.team_name.toLowerCase().includes(clean) ||
              p.position.toLowerCase().includes(clean));
    }).sort((a, b) => b.expected_points - a.expected_points).slice(0, 20);

    box.innerHTML = "";
    if (matches.length === 0) {
      box.innerHTML = `<div style="padding:0.75rem 1rem;font-size:0.85rem;color:var(--text-muted);">No matching players found...</div>`;
      box.classList.remove("hidden");
      return;
    }

    matches.forEach(p => {
      const item = document.createElement("div");
      item.className = "compare-suggest-item";
      const displayName = p.full_name || p.web_name;
      const injuryInfo = getPlayerInjuryInfo(p);
      item.innerHTML = `
        <div class="suggest-info">
          <div class="suggest-name-row">
            <span class="suggest-name">${displayName}</span>
            ${injuryInfo.badgeHtml}
            <span class="suggest-pos pos-${p.position}">${p.position}</span>
          </div>
          <div class="suggest-meta">${p.team_name} • £${safeFixed(p.cost, 1)}m • ${p.total_points || 0} pts</div>
        </div>
        <div class="suggest-xp">${safeFixed(p.expected_points, 1)} xP</div>
      `;
      item.addEventListener("click", () => {
        onSelect(p);
        box.classList.add("hidden");
      });
      box.appendChild(item);
    });

    box.classList.remove("hidden");
  }

  attachSearch(inputA, boxA, clearA, (player) => {
    currentCompareIdA = player.id;
    inputA.value = `${player.full_name || player.web_name} (${player.team_short} • ${player.position} • £${safeFixed(player.cost, 1)}m)`;
    if (clearA) clearA.classList.remove("hidden");
    renderComparison();
  });

  attachSearch(inputB, boxB, clearB, (player) => {
    currentCompareIdB = player.id;
    inputB.value = `${player.full_name || player.web_name} (${player.team_short} • ${player.position} • £${safeFixed(player.cost, 1)}m)`;
    if (clearB) clearB.classList.remove("hidden");
    renderComparison();
  });
}

async function openComparisonForPlayers(idA, idB) {
  if (!idA || !idB) return;
  if (!allPlayers || allPlayers.length === 0) {
    await ensureAllPlayersLoaded();
  }
  currentCompareIdA = idA;
  currentCompareIdB = idB;

  const pA = playersMap[idA];
  const pB = playersMap[idB];

  const inputA = document.getElementById("compareSearchA");
  const inputB = document.getElementById("compareSearchB");
  const clearA = document.getElementById("clearSearchA");
  const clearB = document.getElementById("clearSearchB");

  if (inputA && pA) {
    inputA.value = `${pA.full_name || pA.web_name} (${pA.team_short} • ${pA.position} • £${pA.cost.toFixed(1)}m)`;
    if (clearA) clearA.classList.remove("hidden");
  }
  if (inputB && pB) {
    inputB.value = `${pB.full_name || pB.web_name} (${pB.team_short} • ${pB.position} • £${pB.cost.toFixed(1)}m)`;
    if (clearB) clearB.classList.remove("hidden");
  }

  // Switch to compareView tab
  const compareTab = document.querySelector('.nav-view-btn[data-view="compareView"]');
  if (compareTab) {
    compareTab.click();
  } else {
    document.querySelectorAll(".nav-view-btn").forEach(t => {
      t.classList.toggle("active", t.getAttribute("data-view") === "compareView");
    });
    document.querySelectorAll(".view-section").forEach(sec => {
      sec.classList.toggle("hidden", sec.id !== "compareView");
    });
    renderComparison();
  }

  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderComparison() {
  const layout = document.getElementById("compareCardsLayout");
  if (!layout) return;

  const pA = playersMap[currentCompareIdA];
  const pB = playersMap[currentCompareIdB];
  if (!pA || !pB) return;

  layout.innerHTML = createFotMobComparison(pA, pB);
}

function createFotMobComparison(pA, pB) {
  const isHigherXpA = pA.expected_points >= pB.expected_points;
  const isHigherXpB = pB.expected_points >= pA.expected_points;

  const costValA = typeof pA.cost === "number" ? pA.cost : parseFloat(pA.cost) || 0;
  const costValB = typeof pB.cost === "number" ? pB.cost : parseFloat(pB.cost) || 0;
  const costStrA = `£${costValA.toFixed(1)}m`;
  const costStrB = `£${costValB.toFixed(1)}m`;

  const initialsA = (pA.web_name || "A").slice(0, 2).toUpperCase();
  const initialsB = (pB.web_name || "B").slice(0, 2).toUpperCase();
  const nameA = pA.full_name || pA.web_name;
  const nameB = pB.full_name || pB.web_name;

  // Helper to render a stat row with FotMob dual comparative bars
  function renderCompareRow(label, valA, valB, formatA, formatB, lowerIsBetter = false) {
    const numA = typeof valA === "number" ? valA : parseFloat(valA) || 0;
    const numB = typeof valB === "number" ? valB : parseFloat(valB) || 0;

    let isLeaderA = false;
    let isLeaderB = false;
    if (lowerIsBetter) {
      isLeaderA = numA < numB && numA > 0;
      isLeaderB = numB < numA && numB > 0;
    } else {
      isLeaderA = numA > numB;
      isLeaderB = numB > numA;
    }

    const maxVal = Math.max(numA, numB, 0.001);
    const pctA = Math.min(100, Math.max(8, (numA / maxVal) * 100));
    const pctB = Math.min(100, Math.max(8, (numB / maxVal) * 100));

    return `
      <div class="fmc-stat-row">
        <div class="fmc-cell-val fmc-cell-left ${isLeaderA ? 'fmc-leader' : ''}">
          ${formatA}
        </div>
        <div class="fmc-cell-center">
          <div class="fmc-stat-label">${label}</div>
          <div class="fmc-dual-bars">
            <div class="fmc-bar-track fmc-track-left">
              <div class="fmc-bar-fill fmc-fill-left ${isLeaderA ? 'is-winner' : ''}" style="width: ${pctA}%"></div>
            </div>
            <div class="fmc-bar-divider"></div>
            <div class="fmc-bar-track fmc-track-right">
              <div class="fmc-bar-fill fmc-fill-right ${isLeaderB ? 'is-winner' : ''}" style="width: ${pctB}%"></div>
            </div>
          </div>
        </div>
        <div class="fmc-cell-val fmc-cell-right ${isLeaderB ? 'fmc-leader' : ''}">
          ${formatB}
        </div>
      </div>
    `;
  }

  // 1. Actual Season Output
  const actualStatsRows = [
    renderCompareRow("Goals", pA.goals || 0, pB.goals || 0, pA.goals || 0, pB.goals || 0),
    renderCompareRow("Assists", pA.assists || 0, pB.assists || 0, pA.assists || 0, pB.assists || 0),
    renderCompareRow("Goals + Assists", pA.goals_assists || ((pA.goals||0)+(pA.assists||0)), pB.goals_assists || ((pB.goals||0)+(pB.assists||0)), pA.goals_assists || ((pA.goals||0)+(pA.assists||0)), pB.goals_assists || ((pB.goals||0)+(pB.assists||0))),
    renderCompareRow("Clean Sheets", pA.clean_sheets !== undefined ? pA.clean_sheets : 0, pB.clean_sheets !== undefined ? pB.clean_sheets : 0, pA.clean_sheets !== undefined ? pA.clean_sheets : 0, pB.clean_sheets !== undefined ? pB.clean_sheets : 0),
    renderCompareRow("Minutes Played", pA.minutes_played || pA.current_minutes || 0, pB.minutes_played || pB.current_minutes || 0, `${pA.minutes_played || pA.current_minutes || 0}'`, `${pB.minutes_played || pB.current_minutes || 0}'`),
  ].join("");

  // 2. Expected & Attacking Performance
  const expectedRows = [
    renderCompareRow("Match Rating", pA.fotmob_rating, pB.fotmob_rating, safeFixed(pA.fotmob_rating, 2), safeFixed(pB.fotmob_rating, 2)),
    renderCompareRow("Expected Goals / 90", pA.xg_90, pB.xg_90, safeFixed(pA.xg_90, 2), safeFixed(pB.xg_90, 2)),
    renderCompareRow("Expected Assists / 90", pA.xa_90, pB.xa_90, safeFixed(pA.xa_90, 2), safeFixed(pB.xa_90, 2)),
    renderCompareRow(
      "Line-Breaking Passes / 90",
      pA.line_breaking_passes_90 || 0,
      pB.line_breaking_passes_90 || 0,
      safeFixed(pA.line_breaking_passes_90, 1),
      safeFixed(pB.line_breaking_passes_90, 1)
    ),
    renderCompareRow("Shots / 90", pA.shots_90, pB.shots_90, safeFixed(pA.shots_90, 2), safeFixed(pB.shots_90, 2)),
    renderCompareRow("Chances Created", pA.chances_created || 0, pB.chances_created || 0, pA.chances_created || 0, pB.chances_created || 0),
    renderCompareRow("Big Chances Created", pA.big_chances || 0, pB.big_chances || 0, pA.big_chances || 0, pB.big_chances || 0),
    renderCompareRow("Big Chances / 90", pA.big_chances_90 || 0, pB.big_chances_90 || 0, safeFixed(pA.big_chances_90, 2), safeFixed(pB.big_chances_90, 2)),
  ].join("");

  // 3. FPL Projections & Impact
  const fplRows = [
    renderCompareRow("Projected xP", pA.expected_points || 0, pB.expected_points || 0, `${safeFixed(pA.expected_points, 1)} xP`, `${safeFixed(pB.expected_points, 1)} xP`),
    renderCompareRow("Total FPL Points Scored", pA.total_points || 0, pB.total_points || 0, `${pA.total_points || 0} pts`, `${pB.total_points || 0} pts`),
    renderCompareRow("Current Price", costValA, costValB, costStrA, costStrB, true),
    renderCompareRow("Next 3 Fixtures FDR", pA.avg_fdr || 0, pB.avg_fdr || 0, `Avg ${safeFixed(pA.avg_fdr, 1)}`, `Avg ${safeFixed(pB.avg_fdr, 1)}`, true),
    renderCompareRow("Points Per Match Played", pA.points_per_game || 0, pB.points_per_game || 0, `${safeFixed(pA.points_per_game, 1)} pts`, `${safeFixed(pB.points_per_game, 1)} pts`),
  ].join("");

  // 4. Defensive Contributions
  const csProbA = pA.cs_probability !== undefined ? pA.cs_probability : (pA.xp_breakdown?.cs_probability || 0);
  const csProbB = pB.cs_probability !== undefined ? pB.cs_probability : (pB.xp_breakdown?.cs_probability || 0);
  const careerCsA = pA.career_total_clean_sheets !== undefined ? pA.career_total_clean_sheets : ((pA.historical_past || []).reduce((sum, s) => sum + (s.clean_sheets || 0), 0) + (pA.clean_sheets || 0));
  const careerCsB = pB.career_total_clean_sheets !== undefined ? pB.career_total_clean_sheets : ((pB.historical_past || []).reduce((sum, s) => sum + (s.clean_sheets || 0), 0) + (pB.clean_sheets || 0));

  const defRows = [
    renderCompareRow("Clean Sheets (Actual)", pA.clean_sheets !== undefined ? pA.clean_sheets : 0, pB.clean_sheets !== undefined ? pB.clean_sheets : 0, `${pA.clean_sheets !== undefined ? pA.clean_sheets : 0} CS`, `${pB.clean_sheets !== undefined ? pB.clean_sheets : 0} CS`),
    renderCompareRow("Clean Sheet Probability", csProbA, csProbB, `${safeFixed(csProbA, 1)}%`, `${safeFixed(csProbB, 1)}%`),
    renderCompareRow("All-Time PL Clean Sheets", careerCsA, careerCsB, `${careerCsA} CS`, `${careerCsB} CS`),
    renderCompareRow("Defensive Actions / 90", pA.defensive_actions_90 || 0, pB.defensive_actions_90 || 0, safeFixed(pA.defensive_actions_90, 2), safeFixed(pB.defensive_actions_90, 2)),
    renderCompareRow("Defensive Action Pts Earned", pA.def_contrib_pts || 0, pB.def_contrib_pts || 0, `+${safeFixed(pA.def_contrib_pts, 2)} pts`, `+${safeFixed(pB.def_contrib_pts, 2)} pts`),
    renderCompareRow("Tackles / 90", pA.tackles_90, pB.tackles_90, safeFixed(pA.tackles_90, 2), safeFixed(pB.tackles_90, 2)),
    renderCompareRow("Recoveries / 90", pA.recoveries_90, pB.recoveries_90, safeFixed(pA.recoveries_90, 2), safeFixed(pB.recoveries_90, 2)),
    renderCompareRow("Disciplinary Risk Penalty", -(pA.disciplinary_deduction || 0), -(pB.disciplinary_deduction || 0), `-${safeFixed(pA.disciplinary_deduction, 2)} pts`, `-${safeFixed(pB.disciplinary_deduction, 2)} pts`),
  ].join("");

  return `
    <div class="fotmob-compare-board">
      <!-- Comparison Section: Actual Season Output -->
      <div class="fmc-section">
        <div class="fmc-section-title">
          <span class="icon">⚽</span> Actual Season Output
        </div>
        <div class="fmc-table-body">
          ${actualStatsRows}
        </div>
      </div>

      <!-- Comparison Section: Expected Metrics -->
      <div class="fmc-section">
        <div class="fmc-section-title">
          <span class="icon">📊</span> Underlying Expected & Attacking Metrics
        </div>
        <div class="fmc-table-body">
          ${expectedRows}
        </div>
      </div>

      <!-- Comparison Section: FPL Value & Impact -->
      <div class="fmc-section">
        <div class="fmc-section-title">
          <span class="icon">🏆</span> FPL Value & Projections
        </div>
        <div class="fmc-table-body">
          ${fplRows}
        </div>
      </div>

      <!-- Comparison Section: Defensive Work Rate -->
      <div class="fmc-section">
        <div class="fmc-section-title">
          <span class="icon">🛡️</span> Defending & Work Rate
        </div>
        <div class="fmc-table-body">
          ${defRows}
        </div>
      </div>
    </div>
  `;
}

// 9. Player Detail Modal & Deep xP Breakdown
async function openPlayerModal(pOrId) {
  let p = typeof pOrId === "object" ? pOrId : playersMap[pOrId];
  if (!p && typeof pOrId === "number") {
    try {
      const res = await fetch(`/api/player/${pOrId}`);
      const data = await res.json();
      if (data.success && data.player) {
        p = data.player;
        playersMap[p.id] = p;
      }
    } catch (e) {
      console.error("Fetch player error:", e);
    }
  }
  if (!p) return;

  selectedPlayerForModal = p;
  renderPlayerModalContent(p);

  // Reset to first tab (Player Stats) when player is inspected
  document.querySelectorAll(".modal-tabs .tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".modal-body .tab-pane").forEach(pane => pane.classList.remove("active"));
  const statsTabBtn = document.querySelector('.modal-tabs .tab-btn[data-tab="tabStats"]');
  if (statsTabBtn) statsTabBtn.classList.add("active");
  const statsPane = document.getElementById("tabStats");
  if (statsPane) statsPane.classList.add("active");

  document.getElementById("playerModal").classList.remove("hidden");

  // If deep stats (shotmap, heatmap, or historical_past) are not loaded yet, fetch them on demand
  if (!p.shotmap || !p.historical_past || !p.heatmap) {
    fetch(`/api/player/${p.id}`)
      .then(res => res.json())
      .then(data => {
        if (data.success && data.player) {
          Object.assign(p, data.player);
          playersMap[p.id] = p;
          if (selectedPlayerForModal && selectedPlayerForModal.id === p.id) {
            renderPlayerModalContent(p);
          }
        }
      })
      .catch(e => console.error("Error fetching detailed player stats:", e));
  }
}

function renderPlayerModalContent(p) {
  document.getElementById("mPlayerName").textContent = p.full_name || p.web_name;
  document.getElementById("mPositionTag").textContent = p.position;
  document.getElementById("mTeamTag").textContent = p.team_short;
  document.getElementById("mCostTag").textContent = `£${p.cost.toFixed(1)}m`;
  document.getElementById("mTotalPtsTag").textContent = `${p.total_points} pts`;
  document.getElementById("mXpVal").textContent = p.expected_points.toFixed(1);

  // Status & Availability Tag (Injuries & Returnees)
  const statusTag = document.getElementById("mStatusTag");
  if (statusTag) {
    if (p.is_recent_injury_returnee) {
      statusTag.className = "tag tag-status tag-returning";
      statusTag.textContent = `🟢 Returning (${Math.round((p.injury_return_ramp || 0.75) * 100)}% ramp)`;
      statusTag.title = `Recently returned from injury. Projected: ${p.expected_minutes_projected || 60}m. Protected from price fall: ${p.price_drop_protected ? 'Yes (8-day lock)' : 'No'}`;
      statusTag.classList.remove("hidden");
    } else if (p.is_unavailable || p.status === 'i' || p.status === 's' || p.chance_of_playing_next_round === 0) {
      statusTag.className = "tag tag-status tag-out";
      statusTag.textContent = `🔴 Ruled Out (0%)`;
      statusTag.title = p.injury_reason || p.injury_news || p.news || 'Ruled out';
      statusTag.classList.remove("hidden");
    } else if (p.is_doubtful || p.status === 'd' || (p.chance_of_playing_next_round !== null && p.chance_of_playing_next_round !== undefined && p.chance_of_playing_next_round < 100)) {
      statusTag.className = "tag tag-status tag-doubt";
      const ch = p.chance_of_playing_next_round !== null && p.chance_of_playing_next_round !== undefined ? p.chance_of_playing_next_round : 50;
      statusTag.textContent = `🟡 Doubt (${ch}%)`;
      statusTag.title = p.injury_reason || p.injury_news || p.news || 'Injury Doubt';
      statusTag.classList.remove("hidden");
    } else {
      statusTag.className = "tag tag-status hidden";
    }
  }

  const pDelta = typeof p.predicted_price_change === "number" ? p.predicted_price_change : 0.0;
  const trendTag = document.getElementById("mPriceTrendTag");
  if (trendTag) {
    if (pDelta > 0) {
      trendTag.className = "tag tag-trend rise";
      trendTag.textContent = `▲ +£${pDelta.toFixed(1)}m`;
      trendTag.title = `Predicted rise based on ${p.selected_by_percent || 0}% ownership & performance`;
      trendTag.classList.remove("hidden");
    } else if (pDelta < 0) {
      trendTag.className = "tag tag-trend fall";
      trendTag.textContent = `▼ -£${Math.abs(pDelta).toFixed(1)}m`;
      trendTag.title = `Predicted drop based on ${p.selected_by_percent || 0}% ownership & performance`;
      trendTag.classList.remove("hidden");
    } else {
      trendTag.className = "tag tag-trend stable";
      trendTag.textContent = `▬ £0.0m`;
      trendTag.title = `Stable price expectation based on ${p.selected_by_percent || 0}% ownership`;
      trendTag.classList.remove("hidden");
    }
  }

  // Tab 1: Stats Grid
  const statsGrid = document.getElementById("mStatsGrid");
  const bk = p.xp_breakdown || {};

  const ppm = (p.points_per_game !== undefined && p.points_per_game !== null && p.points_per_game > 0)
    ? Number(p.points_per_game).toFixed(1)
    : (p.current_starts > 0 || p.current_minutes > 0 ? (p.total_points / Math.max(1, p.current_starts || 1)).toFixed(1) : '0.0');

  const predPriceBox = `
    <div class="stat-box">
      <div class="stat-box-title">Predicted Price Change</div>
      <div class="stat-box-value ${pDelta > 0 ? 'color-rise' : (pDelta < 0 ? 'color-fall' : '')}">
        ${pDelta > 0 ? '▲ +' : (pDelta < 0 ? '▼ -' : '▬ ')}£${Math.abs(pDelta).toFixed(1)}m
        <span class="stat-box-sub">Next: £${(p.cost + pDelta).toFixed(1)}m</span>
      </div>
    </div>
  `;

  if (p.position === "GKP") {
    const savesDisplay = p.saves_90 !== undefined && p.saves_90 > 0 ? `${p.saves_90}` : (p.saves !== undefined ? `${p.saves}` : '--');
    const savePctDisplay = p.save_percentage !== undefined && p.save_percentage > 0 ? `${p.save_percentage}%` : (p.saves > 0 ? `${Math.round((p.saves / (p.saves + (p.goals_conceded || 1))) * 100)}%` : '--');
    const goalsPreventedDisplay = p.goals_prevented !== undefined && p.goals_prevented !== 0 ? (p.goals_prevented > 0 ? `+${p.goals_prevented.toFixed(1)}` : `${p.goals_prevented.toFixed(1)}`) : '0.0';
    const csDisplay = bk.cs_probability !== undefined ? `${bk.cs_probability}%` : '--';
    const gcDisplay = p.goals_conceded_90 !== undefined && p.goals_conceded_90 > 0 ? `${p.goals_conceded_90}` : (p.goals_conceded !== undefined ? `${p.goals_conceded}` : '0.0');
    const penSavedDisplay = p.penalties_saved !== undefined && p.penalties_saved > 0 ? `🎯 ${p.penalties_saved} saved` : '0 saved';
    const recDisplay = p.sweeper_recoveries_90 !== undefined && p.sweeper_recoveries_90 > 0 ? `${p.sweeper_recoveries_90}` : (bk.defensive_actions_90 || '--');
    const passDisplay = p.accurate_passes_90 !== undefined && p.accurate_passes_90 > 0 ? `${p.accurate_passes_90}` : '--';

    statsGrid.innerHTML = `
      <div class="stat-box"><div class="stat-box-title">Match Rating</div><div class="stat-box-value">${p.fotmob_rating}</div></div>
      <div class="stat-box"><div class="stat-box-title">Points Per Match Played</div><div class="stat-box-value">${ppm} pts</div></div>
      ${predPriceBox}
      <div class="stat-box"><div class="stat-box-title">Saves / 90 & Volume</div><div class="stat-box-value">${savesDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Save Percentage</div><div class="stat-box-value">${savePctDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Goals Prevented (xGOT)</div><div class="stat-box-value">${goalsPreventedDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Clean Sheet Probability</div><div class="stat-box-value">${csDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Goals Conceded / 90</div><div class="stat-box-value">${gcDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Penalties Saved</div><div class="stat-box-value">${penSavedDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Sweeper Recoveries / 90</div><div class="stat-box-value">${recDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Accurate Passes / 90</div><div class="stat-box-value">${passDisplay}</div></div>
      <div class="stat-box"><div class="stat-box-title">Disciplinary Record</div><div class="stat-box-value">${p.yellow_cards || 0}🟨 ${p.red_cards || 0}🟥</div></div>
    `;
  } else {
    statsGrid.innerHTML = `
      <div class="stat-box"><div class="stat-box-title">Match Rating</div><div class="stat-box-value">${p.fotmob_rating}</div></div>
      <div class="stat-box"><div class="stat-box-title">Goals (Current Season)</div><div class="stat-box-value">${p.goals !== undefined ? p.goals : (p.goals_scored || 0)}</div></div>
      <div class="stat-box"><div class="stat-box-title">Assists (Current Season)</div><div class="stat-box-value">${p.assists !== undefined ? p.assists : 0}</div></div>
      <div class="stat-box"><div class="stat-box-title">Points Per Match Played</div><div class="stat-box-value">${ppm} pts</div></div>
      ${predPriceBox}
      <div class="stat-box"><div class="stat-box-title">Shots on Target / 90</div><div class="stat-box-value">${bk.sot_90 !== undefined ? bk.sot_90 : (p.sot_90 || '--')}</div></div>
      <div class="stat-box"><div class="stat-box-title">Set-Piece & Penalty Equity</div><div class="stat-box-value">${bk.is_penalty_taker ? '🎯 #1 Penalty Taker' : (bk.is_set_piece_taker ? '📐 FK/Corner Taker' : 'Open Play')}</div></div>
      <div class="stat-box"><div class="stat-box-title">Big Chances / 90</div><div class="stat-box-value">${bk.big_chances_90 !== undefined ? bk.big_chances_90 : '--'}</div></div>
      <div class="stat-box"><div class="stat-box-title">Defensive Actions / 90</div><div class="stat-box-value">${bk.defensive_actions_90 !== undefined ? bk.defensive_actions_90 : (p.defensive_actions_90 || '--')}</div></div>
      <div class="stat-box"><div class="stat-box-title">Team xGC & Clean Sheet Odds</div><div class="stat-box-value">${bk.cs_probability !== undefined ? `${bk.cs_probability}%` : '--'}</div></div>
      <div class="stat-box"><div class="stat-box-title">Disciplinary Record</div><div class="stat-box-value">${p.yellow_cards || 0}🟨 ${p.red_cards || 0}🟥</div></div>
    `;
  }

  // Tab 2: Advanced Stats
  renderAdvancedStats(p);

  // Tab 3: Multi-Season History Table
  const pastBody = document.getElementById("mPastSeasonsBody");
  const pastEmpty = document.getElementById("mPastEmptyNote");
  pastBody.innerHTML = "";
  const pastList = p.historical_past || [];
  if (pastList.length === 0) {
    pastEmpty.classList.remove("hidden");
  } else {
    pastEmpty.classList.add("hidden");
    pastList.forEach(s => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${s.season_name}</strong></td>
        <td style="color:var(--fpl-green);font-weight:700;">${s.total_points}</td>
        <td>${s.minutes}</td>
        <td>${s.goals_scored}</td>
        <td>${s.assists}</td>
        <td>${s.clean_sheets}</td>
        <td>${s.xg.toFixed(2)}</td>
        <td>${s.xa.toFixed(2)}</td>
      `;
      pastBody.appendChild(tr);
    });
  }

  // Tab 4: Positional Heatmap
  renderHeatmap(p);

  // Tab 5: Shotmap SVG
  renderShotmap(p);

  // Tab 5: Fixtures
  const fixList = document.getElementById("mFixturesList");
  fixList.innerHTML = "";
  (p.upcoming_fixtures || []).forEach(fix => {
    const row = document.createElement("div");
    row.className = "fix-row";
    const loc = fix.is_home ? "H" : "A";
    row.innerHTML = `
      <span class="fix-gw">GW ${fix.event}</span>
      <span class="fix-opp">vs ${fix.opponent} • ${loc}</span>
      <span class="fdr-badge-${fix.difficulty}">FDR ${fix.difficulty}</span>
    `;
    fixList.appendChild(row);
  });
}

function renderAdvancedStats(p) {
  const container = document.getElementById("mAdvancedStatsContainer");
  if (!container) return;
  const isGKP = p && p.position === "GKP";
  const fstats = (p && p.all_fotmob_stats) || {};
  const bk = (p && p.xp_breakdown) || {};

  const getMetric = (name, fallbackVal = "--", fallbackRank = null) => {
    if (fstats[name]) {
      const v = fstats[name].value;
      const r = fstats[name].rank;
      return { val: (v !== undefined && v !== null) ? v : fallbackVal, rank: r || fallbackRank };
    }
    return { val: fallbackVal, rank: fallbackRank };
  };

  const badgeHtml = (rank) => {
    if (!rank || rank <= 0) return "";
    let cls = "";
    if (rank <= 5) cls = "rank-elite";
    else if (rank <= 20) cls = "rank-gold";
    return `<span class="adv-stat-badge ${cls}">#${rank} in PL</span>`;
  };

  const cardHtml = (label, value, rank = null, unit = "") => {
    const displayVal = (value !== undefined && value !== null && value !== "--") ? `${value}${unit}` : "--";
    return `
      <div class="adv-stat-card">
        <div class="adv-stat-label">${label}</div>
        <div class="adv-stat-row">
          <span class="adv-stat-val">${displayVal}</span>
          ${badgeHtml(rank)}
        </div>
      </div>
    `;
  };

  const pDelta = typeof p.predicted_price_change === "number" ? p.predicted_price_change : 0.0;
  const pStatus = p.price_change_status || (pDelta > 0 ? "rising" : (pDelta < 0 ? "falling" : "stable"));
  const pProg = typeof p.price_change_progress === "number" ? p.price_change_progress : (p.price_change_percent || 0.0);
  const nextPrice = (p.cost + pDelta).toFixed(1);

  const marketSectionHtml = `
    <div class="adv-section">
      <div class="adv-section-title">📈 FPL Market & Price Change Dynamics</div>
      <div class="adv-cards-grid">
        ${cardHtml("Current Market Price", `£${p.cost.toFixed(1)}m`)}
        ${cardHtml("Team Selected By", `${p.selected_by_percent || 0}%`)}
        ${cardHtml("Price Change Progress", `${pProg >= 0 ? '+' : ''}${pProg.toFixed(1)}%`)}
        ${cardHtml("Predicted Price Next GW", `£${nextPrice}m (${pDelta >= 0 ? '+' : ''}£${pDelta.toFixed(1)}m)`)}
        ${cardHtml("Market Status", pStatus.toUpperCase())}
      </div>
      <div style="margin-top:0.6rem;padding:0.6rem 0.85rem;background:rgba(255,255,255,0.04);border-radius:6px;border:1px solid rgba(255,255,255,0.08);font-size:0.8rem;color:var(--text-muted);line-height:1.4;">
        <strong style="color:#ffffff;">Official FPL Rules Model Breakdown:</strong> ${escapeHtml(p.price_change_reason || `Projected based on ${p.selected_by_percent || 0}% ownership threshold and ${p.expected_points.toFixed(1)} xP performance.`)}
      </div>
    </div>
  `;

  let html = marketSectionHtml;

  if (isGKP) {
    const mRating = getMetric("FotMob rating", p.fotmob_rating);
    const mSaves90 = getMetric("Saves per 90", p.saves_90);
    const mSavePct = { val: p.save_percentage ? `${p.save_percentage}%` : "--", rank: null };
    const mGoalsPrev = { val: p.goals_prevented !== undefined && p.goals_prevented !== 0 ? (p.goals_prevented > 0 ? `+${p.goals_prevented.toFixed(2)}` : `${p.goals_prevented.toFixed(2)}`) : "0.00", rank: null };
    const mPasses = getMetric("Accurate passes per 90", p.accurate_passes_90);
    const mLongBalls = getMetric("Accurate long balls per 90");
    const mSweeper = { val: p.sweeper_recoveries_90 || "--", rank: null };
    const mCleanSheet = { val: bk.cs_probability !== undefined ? `${bk.cs_probability}%` : "--", rank: null };

    html = `
      <div class="adv-section">
        <div class="adv-section-title">🧤 Goalkeeping & Shot-Stopping Profile</div>
        <div class="adv-cards-grid">
          ${cardHtml("FotMob Match Rating", mRating.val, mRating.rank)}
          ${cardHtml("Saves per 90", mSaves90.val, mSaves90.rank)}
          ${cardHtml("Save Percentage", mSavePct.val)}
          ${cardHtml("Goals Prevented (xGOT)", mGoalsPrev.val)}
          ${cardHtml("Clean Sheet Probability", mCleanSheet.val)}
          ${cardHtml("Sweeper Recoveries / 90", mSweeper.val)}
          ${cardHtml("Accurate Passes / 90", mPasses.val, mPasses.rank)}
          ${cardHtml("Accurate Long Balls / 90", mLongBalls.val, mLongBalls.rank)}
          ${cardHtml("Penalties Saved", p.penalties_saved !== undefined ? p.penalties_saved : 0)}
        </div>
      </div>
    `;
  } else {
    // Outfield Player Sections
    const mXg = getMetric("Expected goals (xG)", p.xg_90 ? (p.xg_90 * Math.max(1, p.current_minutes || 90) / 90).toFixed(2) : "--");
    const mXg90 = getMetric("Expected goals (xG) per 90", p.xg_90 ? Number(p.xg_90).toFixed(2) : "--");
    const mXgot = getMetric("Expected goals on target (xGOT)");
    const mShots90 = getMetric("Shots per 90", p.shots_90 ? Number(p.shots_90).toFixed(1) : "--");
    const mSot90 = getMetric("Shots on target per 90", bk.sot_90 || p.sot_90 || "--");
    const mBigMissed = getMetric("Big chances missed");

    const mXa = getMetric("Expected assist (xA)", p.xa_90 ? (p.xa_90 * Math.max(1, p.current_minutes || 90) / 90).toFixed(2) : "--");
    const mXa90 = getMetric("Expected assist (xA) per 90", p.xa_90 ? Number(p.xa_90).toFixed(2) : "--");
    const mXgXa90 = getMetric("xG + xA per 90", (p.xg_90 && p.xa_90) ? (Number(p.xg_90) + Number(p.xa_90)).toFixed(2) : "--");
    const mChances = getMetric("Chances created", p.chances_created || "--");
    const mBigChances = getMetric("Big chances created", bk.big_chances_90 || "--");
    const mLbp = p.line_breaking_passes_90 ? { val: p.line_breaking_passes_90, rank: p.lbp_rank } : getMetric("Line-breaking passes per 90");
    const mPasses = getMetric("Accurate passes per 90");
    const mLongBalls = getMetric("Accurate long balls per 90");
    const mDribbles = getMetric("Successful dribbles per 90");

    const mSpeed = getMetric("Top speed");
    const mDist90 = getMetric("Distance per 90");
    const mTotalDist = getMetric("Total distance");
    const mSprints90 = getMetric("Sprints per 90");
    const mTotalSprints = getMetric("Total sprints");
    const mPossFinal3rd = getMetric("Possession won final 3rd per 90");

    const mDefActions = getMetric("Defensive actions per 90", bk.defensive_actions_90 || p.defensive_actions_90 || "--");
    const mTackles = getMetric("Tackles per 90", p.tackles_90 || "--");
    const mInterceptions = getMetric("Interceptions per 90", p.interceptions_90 || "--");
    const mRecoveries = getMetric("Recoveries per 90", p.recoveries_90 || "--");
    const mClearances = getMetric("Clearances per 90", p.clearances_90 || "--");
    const mFouls = getMetric("Fouls committed per 90", p.fouls_90 || "--");

    html = `
      <div class="adv-section">
        <div class="adv-section-title">⚡ Attacking Threat & Expected Metrics</div>
        <div class="adv-cards-grid">
          ${cardHtml("Expected Goals (xG)", mXg.val, mXg.rank)}
          ${cardHtml("xG per 90", mXg90.val, mXg90.rank)}
          ${cardHtml("Expected Goals on Target", mXgot.val, mXgot.rank)}
          ${cardHtml("Shots per 90", mShots90.val, mShots90.rank)}
          ${cardHtml("Shots on Target / 90", mSot90.val, mSot90.rank)}
          ${cardHtml("Big Chances Missed", mBigMissed.val, mBigMissed.rank)}
        </div>
      </div>

      <div class="adv-section">
        <div class="adv-section-title">🎯 Creativity, Passing & Progression</div>
        <div class="adv-cards-grid">
          ${cardHtml("Expected Assists (xA)", mXa.val, mXa.rank)}
          ${cardHtml("xA per 90", mXa90.val, mXa90.rank)}
          ${cardHtml("xG + xA per 90", mXgXa90.val, mXgXa90.rank)}
          ${cardHtml("Chances Created", mChances.val, mChances.rank)}
          ${cardHtml("Big Chances Created", mBigChances.val, mBigChances.rank)}
          ${cardHtml("Line-Breaking Passes / 90", mLbp.val, mLbp.rank)}
          ${cardHtml("Accurate Passes / 90", mPasses.val, mPasses.rank)}
          ${cardHtml("Accurate Long Balls / 90", mLongBalls.val, mLongBalls.rank)}
          ${cardHtml("Successful Dribbles / 90", mDribbles.val, mDribbles.rank)}
        </div>
      </div>

      <div class="adv-section">
        <div class="adv-section-title">🏃 Physicals, Speed & Work Rate</div>
        <div class="adv-cards-grid">
          ${cardHtml("Top Speed", mSpeed.val, mSpeed.rank, mSpeed.val !== "--" ? " km/h" : "")}
          ${cardHtml("Distance Covered / 90", mDist90.val, mDist90.rank, mDist90.val !== "--" ? " m" : "")}
          ${cardHtml("Total Distance", mTotalDist.val, mTotalDist.rank, mTotalDist.val !== "--" ? " m" : "")}
          ${cardHtml("Sprints per 90", mSprints90.val, mSprints90.rank)}
          ${cardHtml("Total Sprints", mTotalSprints.val, mTotalSprints.rank)}
          ${cardHtml("Possession Won Final 3rd / 90", mPossFinal3rd.val, mPossFinal3rd.rank)}
        </div>
      </div>

      <div class="adv-section">
        <div class="adv-section-title">🛡️ Defensive Actions & Tenacity</div>
        <div class="adv-cards-grid">
          ${cardHtml("Defensive Actions / 90", mDefActions.val, mDefActions.rank)}
          ${cardHtml("Tackles per 90", mTackles.val, mTackles.rank)}
          ${cardHtml("Interceptions per 90", mInterceptions.val, mInterceptions.rank)}
          ${cardHtml("Ball Recoveries / 90", mRecoveries.val, mRecoveries.rank)}
          ${cardHtml("Clearances per 90", mClearances.val, mClearances.rank)}
          ${cardHtml("Fouls Committed / 90", mFouls.val, mFouls.rank)}
        </div>
      </div>
    `;
  }

  // Multi-step xP Formula Model Breakdown
  html += `
    <div class="adv-section">
      <div class="adv-section-title">🧮 Expected Points (xP) Bayesian Model Breakdown</div>
      <div class="formula-box">
        <code>xP = [ Multi-Season Track Record + Current Season Form ] × Fixture Difficulty × Availability</code>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">1. Expected Appearance (60+ mins baseline):</span>
        <span class="xp-step-val">+${bk.base_appearance !== undefined ? bk.base_appearance : 2.0} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">2. FotMob Rating Scaling Factor:</span>
        <span class="xp-step-val">${bk.rating_scaling ? bk.rating_scaling.toFixed(2) : '1.00'}×</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">3. Attacking Expectancy (Bayesian Blended xG & xA):</span>
        <span class="xp-step-val">+${bk.attacking_pts ? bk.attacking_pts.toFixed(2) : '0.00'} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">4. Clean Sheet Probability Odds:</span>
        <span class="xp-step-val">+${bk.clean_sheet_pts ? bk.clean_sheet_pts.toFixed(2) : '0.00'} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">5. Goalkeeper Save Points & Prevented Goals:</span>
        <span class="xp-step-val">+${bk.save_pts ? bk.save_pts.toFixed(2) : '0.00'} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">6. Goals Conceded & Disciplinary Deductions:</span>
        <span class="xp-step-val" style="color: #ef4444;">-${bk.conceded_and_discipline_deductions ? bk.conceded_and_discipline_deductions.toFixed(2) : '0.00'} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">7. Projected Bonus Points System (BPS):</span>
        <span class="xp-step-val">+${bk.bonus_pts ? bk.bonus_pts.toFixed(2) : '0.00'} pts</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">8. Fixture Difficulty Multiplier:</span>
        <span class="xp-step-val">× ${bk.fixture_multiplier || 1.0}</span>
      </div>
      <div class="xp-step-row">
        <span class="xp-step-lbl">9. Availability & Likelihood Scaling:</span>
        <span class="xp-step-val">× ${bk.availability_penalty !== undefined ? bk.availability_penalty : 1.0}</span>
      </div>
      <div class="xp-step-row" style="background: rgba(0, 255, 135, 0.1); border-color: var(--fpl-green);">
        <span class="xp-step-lbl"><strong>Final Projected Gameweek xP:</strong></span>
        <span class="xp-step-val" style="font-size: 1.25rem; color: var(--fpl-green);">${p.expected_points.toFixed(2)} xP</span>
      </div>
    </div>
  `;

  container.innerHTML = html;
}

function renderHeatmap(p) {
  const isGKP = p && p.position === "GKP";
  const coords = (p && p.heatmap) || [];
  const svg = document.getElementById("heatmapSvg");
  const emptyNote = document.getElementById("heatmapEmptyNote");
  const titleEl = document.getElementById("heatmapTitle");
  const summaryEl = document.getElementById("heatmapSummaryBadge");

  if (!svg) return;

  if (titleEl) {
    titleEl.textContent = isGKP ? "Goalkeeper Coverage & Activity Map" : "Player Positional Heatmap";
  }

  // Pitch Base SVG matching shotmap design
  const pitchLines = `
    <rect x="0" y="0" width="105" height="68" fill="#092014" />
    <path d="M 0 0 L 105 0 L 105 68 L 0 68 Z" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <path d="M 52.5 0 L 52.5 68" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <circle cx="52.5" cy="34" r="9.15" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="88.5" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="99.5" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="0" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="0" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
  `;

  // Defs with multi-tier density gradients and soft glow filter
  const defs = `
    <defs>
      <filter id="heatGlowFilter" x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="2.8" />
      </filter>
      <!-- Layer 1: Low-activity Green Halo -->
      <radialGradient id="gradHeatGreen" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#10b981" stop-opacity="0.65"/>
        <stop offset="45%" stop-color="#00e676" stop-opacity="0.42"/>
        <stop offset="75%" stop-color="#00ff87" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="#00ff87" stop-opacity="0"/>
      </radialGradient>
      <!-- Layer 2: Medium-activity Bright Yellow/Gold -->
      <radialGradient id="gradHeatYellow" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#eab308" stop-opacity="0.70"/>
        <stop offset="45%" stop-color="#facc15" stop-opacity="0.48"/>
        <stop offset="80%" stop-color="#fde047" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="#fde047" stop-opacity="0"/>
      </radialGradient>
      <!-- Layer 3: High-activity Vibrant Orange -->
      <radialGradient id="gradHeatOrange" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#ff6b00" stop-opacity="0.80"/>
        <stop offset="45%" stop-color="#f97316" stop-opacity="0.58"/>
        <stop offset="80%" stop-color="#fb923c" stop-opacity="0.22"/>
        <stop offset="100%" stop-color="#fb923c" stop-opacity="0"/>
      </radialGradient>
      <!-- Layer 4: Peak Hotspot Crimson Red -->
      <radialGradient id="gradHeatRed" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#ef4444" stop-opacity="0.95"/>
        <stop offset="40%" stop-color="#dc2626" stop-opacity="0.80"/>
        <stop offset="75%" stop-color="#b91c1c" stop-opacity="0.38"/>
        <stop offset="100%" stop-color="#b91c1c" stop-opacity="0"/>
      </radialGradient>
    </defs>
  `;

  if (!coords || coords.length === 0) {
    if (summaryEl) summaryEl.textContent = "";
    if (emptyNote) {
      emptyNote.textContent = isGKP
        ? "No official FotMob match tracking coordinates recorded yet for this goalkeeper."
        : "No official FotMob match activity coordinates recorded yet for this player.";
      emptyNote.classList.remove("hidden");
    }
    svg.innerHTML = `
      ${defs}
      ${pitchLines}
    `;
    return;
  }

  if (emptyNote) {
    emptyNote.classList.add("hidden");
  }

  // Calculate genuine summary statistics from coordinates
  const totalPts = coords.length;
  const attHalf = Math.round((coords.filter(pt => pt.x > 52.5).length / totalPts) * 100);
  const boxPts = coords.filter(pt => pt.x >= 88.5 && pt.y >= 13.8 && pt.y <= 54.2).length;
  const boxPct = Math.round((boxPts / totalPts) * 100);
  const leftPts = Math.round((coords.filter(pt => pt.y < 25.0).length / totalPts) * 100);
  const rightPts = Math.round((coords.filter(pt => pt.y > 43.0).length / totalPts) * 100);

  let summaryText = "";
  if (isGKP) {
    summaryText = `${totalPts} Match Coordinates • 6-Yard & Penalty Box Domain`;
  } else if (rightPts >= 45) {
    summaryText = `${totalPts} Touches • Right Flank Focus (${rightPts}%) • ${attHalf}% Attacking Half`;
  } else if (leftPts >= 45) {
    summaryText = `${totalPts} Touches • Left Flank Focus (${leftPts}%) • ${attHalf}% Attacking Half`;
  } else if (boxPct >= 18) {
    summaryText = `${totalPts} Touches • Penalty Box Focal Point (${boxPts} in box) • ${attHalf}% Attacking Half`;
  } else {
    summaryText = `${totalPts} Touches • Comprehensive Pitch Presence • ${attHalf}% Attacking Half`;
  }

  if (summaryEl) {
    summaryEl.textContent = summaryText;
  }

  // Calculate local KDE density for each point to stratify into Green, Yellow, Orange, and Red tiers
  const sigma = 6.8;
  const twoSigmaSq = 2 * sigma * sigma;
  const n = coords.length;
  const densities = new Float32Array(n);
  let maxDensity = 0.001;

  for (let i = 0; i < n; i++) {
    const p1 = coords[i];
    let d = 0;
    for (let j = 0; j < n; j++) {
      const p2 = coords[j];
      const dx = p1.x - p2.x;
      const dy = p1.y - p2.y;
      const distSq = dx * dx + dy * dy;
      if (distSq < 450) {
        d += Math.exp(-distSq / twoSigmaSq);
      }
    }
    densities[i] = d;
    if (d > maxDensity) maxDensity = d;
  }

  // Build SVG circles for each tier
  let greenCircles = "";
  let yellowCircles = "";
  let orangeCircles = "";
  let redCircles = "";

  for (let i = 0; i < n; i++) {
    const pt = coords[i];
    const cx = Math.min(104, Math.max(1, pt.x)).toFixed(2);
    const cy = Math.min(67, Math.max(1, pt.y)).toFixed(2);
    const norm = densities[i] / maxDensity;

    // All points provide base soft green halo
    greenCircles += `<circle cx="${cx}" cy="${cy}" r="6.6" fill="url(#gradHeatGreen)" />`;

    // Medium density points add bright yellow
    if (norm >= 0.25) {
      yellowCircles += `<circle cx="${cx}" cy="${cy}" r="5.2" fill="url(#gradHeatYellow)" />`;
    }

    // High density points add vibrant orange
    if (norm >= 0.52) {
      orangeCircles += `<circle cx="${cx}" cy="${cy}" r="4.2" fill="url(#gradHeatOrange)" />`;
    }

    // Peak hotspot points add fiery crimson red
    if (norm >= 0.78) {
      redCircles += `<circle cx="${cx}" cy="${cy}" r="3.2" fill="url(#gradHeatRed)" />`;
    }
  }

  svg.innerHTML = `
    ${defs}
    <!-- Pitch Background & Base -->
    ${pitchLines}
    <!-- Heatmap: Layer 1 (Green Activity) -->
    <g filter="url(#heatGlowFilter)" opacity="0.86">
      ${greenCircles}
    </g>
    <!-- Heatmap: Layer 2 (Yellow Transition) -->
    <g filter="url(#heatGlowFilter)" opacity="0.88">
      ${yellowCircles}
    </g>
    <!-- Heatmap: Layer 3 (Orange High Activity) -->
    <g filter="url(#heatGlowFilter)" opacity="0.92">
      ${orangeCircles}
    </g>
    <!-- Heatmap: Layer 4 (Fiery Red Hotspots) -->
    <g filter="url(#heatGlowFilter)" opacity="0.96">
      ${redCircles}
    </g>
    <!-- Crisp Pitch Markings Overlay -->
    <g pointer-events="none">
      <path d="M 0 0 L 105 0 L 105 68 L 0 68 Z" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <path d="M 52.5 0 L 52.5 68" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <circle cx="52.5" cy="34" r="9.15" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <circle cx="52.5" cy="34" r="0.6" fill="rgba(255,255,255,0.35)"/>
      <rect x="88.5" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <rect x="99.5" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <circle cx="94.0" cy="34" r="0.6" fill="rgba(255,255,255,0.35)"/>
      <rect x="0" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <rect x="0" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1"/>
      <circle cx="11.0" cy="34" r="0.6" fill="rgba(255,255,255,0.35)"/>
    </g>
  `;
}

function renderShotmap(p) {
  const isGKP = p && p.position === "GKP";
  const shotmap = (p && p.shotmap) || [];
  const svg = document.getElementById("shotmapSvg");
  const emptyNote = document.getElementById("shotmapEmptyNote");
  const titleEl = document.getElementById("shotmapTitle");
  const legendEl = document.getElementById("shotmapLegend");
  const summaryEl = document.getElementById("shotmapSummaryBadge");

  svg.innerHTML = `
    <rect x="0" y="0" width="105" height="68" fill="#092014" />
    <path d="M 0 0 L 105 0 L 105 68 L 0 68 Z" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <path d="M 52.5 0 L 52.5 68" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <circle cx="52.5" cy="34" r="9.15" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="88.5" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="99.5" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="0" y="13.8" width="16.5" height="40.4" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
    <rect x="0" y="24.8" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
  `;

  if (isGKP) {
    if (titleEl) titleEl.textContent = "Goalkeeper Shot-Stopping Map";
    if (legendEl) {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="dot dot-goal"></span> Saved / Stopped</span>
        <span class="legend-item"><span class="dot dot-miss"></span> Goal Conceded</span>
        <span class="legend-item"><span class="dot dot-saved"></span> Off Target / Blocked</span>
      `;
    }
  } else {
    if (titleEl) titleEl.textContent = "Player Shotmap";
    if (legendEl) {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="dot dot-goal"></span> Goal</span>
        <span class="legend-item"><span class="dot dot-saved"></span> Saved / Target</span>
        <span class="legend-item"><span class="dot dot-miss"></span> Missed / Blocked</span>
      `;
    }
  }

  if (!shotmap || shotmap.length === 0) {
    if (emptyNote) {
      emptyNote.textContent = isGKP ? "No match shots recorded yet for this goalkeeper." : "No match shots recorded yet for this player.";
      emptyNote.classList.remove("hidden");
    }
    if (summaryEl) summaryEl.textContent = "";
    return;
  }
  if (emptyNote) emptyNote.classList.add("hidden");

  let goalsCount = 0;
  let savedCount = 0;
  let onTargetCount = 0;
  let totalXg = 0;

  shotmap.forEach(shot => {
    const isGoal = shot.eventType === "Goal";
    const isSaved = shot.eventType === "AttemptSaved";
    const isOnTarget = shot.isOnTarget || isGoal || isSaved;
    totalXg += (shot.expectedGoals || 0);
    if (isGoal) goalsCount++;
    if (isSaved) savedCount++;
    if (isOnTarget) onTargetCount++;
  });

  // Sort shots by visibility hierarchy so top-priority elements paint on top in SVG
  let renderShots = [...shotmap];
  if (isGKP) {
    // Hierarchy: Red (Goal Conceded) > Blue (Off Target/Blocked) > Green (Saved)
    // In SVG paint order: Green (drawn 1st, bottom) -> Blue (drawn 2nd, middle) -> Red (drawn last, top)
    renderShots.sort((a, b) => {
      const getGkpPriority = (s) => {
        if (s.eventType === "Goal") return 3;          // Red: top
        if (s.eventType !== "AttemptSaved") return 2;  // Blue: middle
        return 1;                                      // Green: bottom
      };
      return getGkpPriority(a) - getGkpPriority(b);
    });
  } else {
    // Outfield hierarchy: Green (Goal) > Blue (Saved/Target) > Red (Missed/Blocked)
    renderShots.sort((a, b) => {
      const getOutfieldPriority = (s) => {
        if (s.eventType === "Goal") return 3;
        if (s.isOnTarget || s.eventType === "AttemptSaved") return 2;
        return 1;
      };
      return getOutfieldPriority(a) - getOutfieldPriority(b);
    });
  }

  renderShots.forEach(shot => {
    let sx = shot.x || 85;
    let sy = shot.y || 34;

    if (sx < 52.5 && !isGKP) {
      sx = 105 - sx;
      sy = 68 - sy;
    }

    // Mirror placed below the pitch: flip vertically across horizontal axis for all players & future shots
    sy = 68 - sy;

    sx = Math.min(103.5, Math.max(1.5, sx));
    sy = Math.min(66.5, Math.max(1.5, sy));

    const isGoal = shot.eventType === "Goal";
    const isSaved = shot.eventType === "AttemptSaved";
    const isOnTarget = shot.isOnTarget || isGoal || isSaved;
    const xg = shot.expectedGoals || 0;

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", sx);
    circle.setAttribute("cy", sy);

    let color = "#f87171";
    let radius = "2.2";
    let strokeWidth = "0.6";

    if (isGKP) {
      if (isSaved) {
        color = "#00ff87";
        radius = "2.6";
      } else if (isGoal) {
        color = "#ef4444";
        radius = "3.4";
        strokeWidth = "1.0";
      } else {
        color = "#38bdf8";
        radius = "2.2";
      }
    } else {
      if (isGoal) {
        color = "#00ff87";
        radius = "3.2";
        strokeWidth = "0.9";
      } else if (isOnTarget) {
        color = "#38bdf8";
        radius = "2.4";
      } else {
        color = "#f87171";
        radius = "2.0";
      }
    }

    circle.setAttribute("r", radius);
    circle.setAttribute("fill", color);
    circle.setAttribute("stroke", "#ffffff");
    circle.setAttribute("stroke-width", strokeWidth);
    circle.setAttribute("opacity", (isGKP && isGoal) ? "1.0" : "0.92");

    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    const minStr = shot.min ? `${shot.min}' ` : "";
    const eventStr = isGoal ? (isGKP ? "Goal Conceded" : "Goal") : (isSaved ? (isGKP ? "Shot Saved" : "Shot on Target / Saved") : "Off Target / Blocked");
    const xgStr = xg > 0 ? ` • ${xg.toFixed(2)} xG` : "";
    title.textContent = `${minStr}${eventStr}${xgStr}`;
    circle.appendChild(title);

    svg.appendChild(circle);
  });

  if (summaryEl) {
    if (isGKP) {
      const saveRate = (savedCount + goalsCount) > 0 ? Math.round((savedCount / (savedCount + goalsCount)) * 100) : 100;
      summaryEl.textContent = `${shotmap.length} Shots Faced • ${savedCount} Saves • ${goalsCount} Conceded • ${saveRate}% Save Rate`;
    } else {
      summaryEl.textContent = `${shotmap.length} Shots • ${goalsCount} Goals • ${onTargetCount} on Target • ${totalXg.toFixed(2)} xG`;
    }
  }
}



// 11. Injuries Board & Live Search
let allInjuriesList = [];
let currentInjuryPillFilter = "ALL";

async function loadInjuries() {
  try {
    const res = await fetch("/api/injuries");
    const data = await res.json();
    // STRICT USER REQUIREMENT: Exclude players who have been transferred out
    allInjuriesList = (data.injuries || []).filter(p => !p.is_transferred_out && p.status !== 'u' && p.status !== 'n');
    renderInjuriesList("");
  } catch (err) {
    console.error("Error loading injuries:", err);
  }
}

function renderInjuriesList(filterQuery = "") {
  const listEl = document.getElementById("injuryList");
  const countBadge = document.getElementById("injuriesCountBadge");
  const clearBtn = document.getElementById("btnClearInjurySearch");
  if (!listEl) return;

  const query = (filterQuery || "").trim().toLowerCase();
  if (clearBtn) {
    if (query) {
      clearBtn.classList.remove("hidden");
    } else {
      clearBtn.classList.add("hidden");
    }
  }

  // Update Category Count Badges
  const injCountAll = document.getElementById("injCountAll");
  const injCountRed = document.getElementById("injCountRed");
  const injCountYellow = document.getElementById("injCountYellow");
  const injCountGreen = document.getElementById("injCountGreen");

  let rCount = 0, yCount = 0, gCount = 0;
  allInjuriesList.forEach(p => {
    const inf = getPlayerInjuryInfo(p);
    if (inf.severity === 'red') rCount++;
    else if (inf.severity === 'yellow') yCount++;
    else if (inf.severity === 'green' || p.is_recent_injury_returnee) gCount++;
  });

  if (injCountAll) injCountAll.textContent = allInjuriesList.length;
  if (injCountRed) injCountRed.textContent = rCount;
  if (injCountYellow) injCountYellow.textContent = yCount;
  if (injCountGreen) injCountGreen.textContent = gCount;

  let filtered = allInjuriesList.filter(p => !p.is_transferred_out && p.status !== 'u' && p.status !== 'n');

  // Filter by active pill
  if (currentInjuryPillFilter === "RED") {
    filtered = filtered.filter(p => getPlayerInjuryInfo(p).severity === 'red');
  } else if (currentInjuryPillFilter === "YELLOW") {
    filtered = filtered.filter(p => getPlayerInjuryInfo(p).severity === 'yellow');
  } else if (currentInjuryPillFilter === "GREEN") {
    filtered = filtered.filter(p => {
      const inf = getPlayerInjuryInfo(p);
      return inf.severity === 'green' || p.is_recent_injury_returnee;
    });
  }

  if (query) {
    filtered = filtered.filter(p => {
      const name = (p.web_name || "").toLowerCase();
      const full = (p.full_name || "").toLowerCase();
      const team = (p.team_name || "").toLowerCase();
      const short = (p.team_short || "").toLowerCase();
      const reason = (p.injury_reason || "").toLowerCase();
      const news = (p.injury_news || "").toLowerCase();
      return name.includes(query) || full.includes(query) || team.includes(query) || short.includes(query) || reason.includes(query) || news.includes(query);
    });
  }

  if (countBadge) {
    countBadge.textContent = `${filtered.length} Flagged`;
  }

  listEl.innerHTML = "";

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="empty-state-hint" style="padding: 1rem; text-align: center; color: var(--text-muted); font-size: 0.8rem;">No injured players match "${filterQuery}"</div>`;
    return;
  }

  filtered.forEach(p => {
    const item = document.createElement("div");
    const injuryInfo = getPlayerInjuryInfo(p);
    const isRed = injuryInfo.severity === 'red';
    const isGreen = injuryInfo.severity === 'green' || p.is_recent_injury_returnee;

    let itemClass = 'injury-item-yellow';
    let badgeClass = 'badge-yellow';
    let flagEmoji = '🟡';

    if (isRed) {
      itemClass = 'injury-item-red';
      badgeClass = 'badge-red';
      flagEmoji = '🔴';
    } else if (isGreen) {
      itemClass = 'injury-item-green';
      badgeClass = 'badge-green';
      flagEmoji = '🟢';
    }

    item.className = `injury-item ${itemClass}`;
    
    let statusText = p.injury_reason || p.injury_news || p.news;
    if (!statusText) {
      if (isRed) statusText = "Injured / Ruled Out";
      else if (isGreen) statusText = `Returning (${Math.round((p.injury_return_ramp || 0.75) * 100)}% ramp)`;
      else statusText = "Doubtful";
    }

    const chanceLabel = (!isGreen && injuryInfo.chance !== null && !isNaN(injuryInfo.chance) && injuryInfo.chance < 100)
      ? ` (${injuryInfo.chance}%)`
      : (isGreen ? ` (~${p.expected_minutes_projected || 60}m)` : '');

    item.innerHTML = `
      <div>
        <div class="injury-p-name">
          <span>${flagEmoji}</span>
          <span>${escapeHtml(p.web_name)}</span>
        </div>
        <div class="injury-p-team">${escapeHtml(p.team_name || p.team_short)} • £${p.cost.toFixed(1)}m</div>
      </div>
      <div class="injury-badge ${badgeClass}">${escapeHtml(statusText)}${chanceLabel}</div>
    `;
    item.addEventListener("click", () => {
      openPlayerModal(p.id);
    });
    item.style.cursor = "pointer";
    listEl.appendChild(item);
  });
}

// 12. Setup Event Listeners
function setupEventListeners() {
  // Transfer Carousel Left / Right Buttons
  const btnPrevTransfer = document.getElementById("btnPrevTransfer");
  const btnNextTransfer = document.getElementById("btnNextTransfer");
  if (btnPrevTransfer) {
    btnPrevTransfer.addEventListener("click", () => {
      if (currentTransferIndex > 0) {
        currentTransferIndex--;
        updateTransferCarouselUI();
      }
    });
  }
  if (btnNextTransfer) {
    btnNextTransfer.addEventListener("click", () => {
      if (currentTransferIndex < currentTransferSuggestions.length - 1) {
        currentTransferIndex++;
        updateTransferCarouselUI();
      }
    });
  }

  // Compare Button in Transfer Recommendation Header
  const btnCompareTransfer = document.getElementById("btnCompareTransfer");
  if (btnCompareTransfer) {
    btnCompareTransfer.addEventListener("click", () => {
      const curMove = currentTransferSuggestions[currentTransferIndex];
      if (!curMove || !curMove.player_out || !curMove.player_in) return;
      openComparisonForPlayers(curMove.player_out.id, curMove.player_in.id);
    });
  }

  // Live Injury Monitor Search
  const injurySearchInput = document.getElementById("injurySearchInput");
  const btnClearInjurySearch = document.getElementById("btnClearInjurySearch");
  if (injurySearchInput) {
    injurySearchInput.addEventListener("input", (e) => {
      renderInjuriesList(e.target.value);
    });
  }
  if (btnClearInjurySearch) {
    btnClearInjurySearch.addEventListener("click", () => {
      if (injurySearchInput) {
        injurySearchInput.value = "";
        injurySearchInput.focus();
      }
      renderInjuriesList("");
    });
  }

  // Live Injury Monitor Pill Filters (All, Out, Doubt, Returning)
  const injuryPills = document.querySelectorAll("#injuryFilterPills .inj-pill");
  injuryPills.forEach(pill => {
    pill.addEventListener("click", () => {
      injuryPills.forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      currentInjuryPillFilter = pill.getAttribute("data-filter") || "ALL";
      const q = document.getElementById("injurySearchInput") ? document.getElementById("injurySearchInput").value : "";
      renderInjuriesList(q);
    });
  });

  document.getElementById("btnCloseModal").addEventListener("click", () => {
    document.getElementById("playerModal").classList.add("hidden");
  });

  // Modal Tab Switching
  document.querySelectorAll(".modal-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".modal-tabs .tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".modal-body .tab-pane").forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.getElementById(target).classList.add("active");
    });
  });

  // Market (Player Pool) Table Filters: Position, Status, Team, Min/Max Price, Search & Sorting
  const resetPageAndRender = () => { marketCurrentPage = 1; renderMarketTable(); };

  const marketPos = document.getElementById("marketPosFilter");
  if (marketPos) marketPos.addEventListener("change", resetPageAndRender);

  const marketStatus = document.getElementById("marketStatusFilter");
  if (marketStatus) marketStatus.addEventListener("change", resetPageAndRender);

  const marketTeam = document.getElementById("marketTeamFilter");
  if (marketTeam) marketTeam.addEventListener("change", resetPageAndRender);

  const marketMin = document.getElementById("marketMinPrice");
  if (marketMin) {
    marketMin.addEventListener("input", resetPageAndRender);
    marketMin.addEventListener("change", resetPageAndRender);
  }

  const marketMax = document.getElementById("marketMaxPrice");
  if (marketMax) {
    marketMax.addEventListener("input", resetPageAndRender);
    marketMax.addEventListener("change", resetPageAndRender);
  }

  // (marketSortSelect removed per user request)

  const marketSearch = document.getElementById("marketSearchInput");
  if (marketSearch) marketSearch.addEventListener("input", resetPageAndRender);

  // Market Table Column Sorting
  document.querySelectorAll(".market-table th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.getAttribute("data-sort");
      if (currentSortCol === col) {
        currentSortAsc = !currentSortAsc;
      } else {
        currentSortCol = col;
        currentSortAsc = (col === "web_name" || col === "team_short");
      }
      marketCurrentPage = 1;
      renderMarketTable();
    });
  });



  // Import / Reset FPL Squad via Manager ID or URL (integrated with Live Sync)
  const triggerFplImportOrReset = async () => {
    if (fplButtonState === "reset") {
      await resetToTemplateSquad();
      return;
    }
    const managerInput = document.getElementById("fplManagerId");
    const managerId = managerInput ? managerInput.value.trim() : "";
    if (!managerId) {
      // If empty, open the integrated Import Squad modal
      if (typeof openFplCookieModal === "function") {
        openFplCookieModal();
      }
      return;
    }
    if (managerInput) managerInput.blur();
    autoSyncPipeline({ showNotification: false });
    await importManagerSquad(managerId);
  };

  const btnImport = document.getElementById("btnImportFPL");
  if (btnImport) {
    btnImport.addEventListener("click", triggerFplImportOrReset);
  }

  const fplIdInput = document.getElementById("fplManagerId");
  if (fplIdInput) {
    // When text box is touched (focus, input, click, touchstart), convert button back to "Import Squad"
    const onTouchFplInput = () => {
      if (fplButtonState === "reset") {
        setFplButtonState("import");
      }
    };
    fplIdInput.addEventListener("focus", onTouchFplInput);
    fplIdInput.addEventListener("input", onTouchFplInput);
    fplIdInput.addEventListener("click", onTouchFplInput);
    fplIdInput.addEventListener("touchstart", onTouchFplInput, { passive: true });

    fplIdInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.keyCode === 13 || e.which === 13) {
        e.preventDefault();
        triggerFplImportOrReset();
      }
    });
  }
}

// ============================================================================
// Automated Gameweek Update Auto-Sync
// ============================================================================
let isAutoSyncing = false;

async function autoSyncPipeline(options = { showNotification: false }) {
  if (isAutoSyncing) return;
  isAutoSyncing = true;

  try {
    const res = await fetch("/api/pipeline/auto-sync");
    if (!res.ok) return;
    const data = await res.json();

    // If an update was triggered (new gameweek completed), reload data seamlessly
    if (data.updated) {
      console.log(`[AutoSync] New gameweek finalized (GW ${data.new_gameweek})! Refreshing stats & projections...`);
      if (options.showNotification) {
        showBanner(`Auto-Sync: Fresh stats ingested for Gameweek ${data.new_gameweek || ''}. Projections updated.`);
      }
      await loadGameweekInfo();
      await loadAllPlayers();
      renderMarketTable();
      renderPitch();
      runRecommendation();
    }
  } catch (err) {
    console.debug("[AutoSync] Check completed:", err);
  } finally {
    isAutoSyncing = false;
  }
}

// ============================================================================
// ChatFPL Interactive Assistant Controller
// ============================================================================

let chatFplInitialized = false;

function initChatFPL() {
  const launcher = document.getElementById("chatFplLauncher");
  const container = document.getElementById("chatFplContainer");
  const closeBtn = document.getElementById("chatFplCloseBtn");
  const backBtn = document.getElementById("chatFplBackBtn");
  const historyBtn = document.getElementById("chatFplHistoryBtn");
  const historyBadge = document.getElementById("chatFplHistoryBadge");
  const newBtn = document.getElementById("chatFplNewBtn");
  const clearBtn = document.getElementById("chatFplClearBtn");
  const historyPanel = document.getElementById("chatFplHistoryPanel");
  const historyList = document.getElementById("chatFplHistoryList");
  const closeHistoryBtn = document.getElementById("chatFplCloseHistoryBtn");
  const clearAllHistoryBtn = document.getElementById("chatFplClearAllHistoryBtn");

  const messagesEl = document.getElementById("chatFplMessages");
  const suggestionsEl = document.getElementById("chatFplSuggestions");
  const form = document.getElementById("chatFplForm");
  const input = document.getElementById("chatFplInput");
  const sendBtn = document.getElementById("chatFplSendBtn");

  if (!launcher || !container || !form || !input) return;

  let currentSessionId = null;
  let currentSessionMessages = [];
  let chatFplInitialized = false;

  function getChatStorageKey() {
    const mid = (typeof currentUser !== "undefined" && currentUser && currentUser.manager_id) 
      || (typeof currentSquad !== "undefined" && currentSquad && currentSquad.manager_id) 
      || localStorage.getItem("chatfpl_manager_id") 
      || document.getElementById("fplManagerId")?.value?.trim();
    if (mid) {
      return `chatfpl_saved_conversations_fpl_${mid}`;
    }
    return "chatfpl_saved_conversations_guest";
  }

  function formatWhatsAppTime(dateObj = new Date()) {
    const h = String(dateObj.getHours()).padStart(2, "0");
    const m = String(dateObj.getMinutes()).padStart(2, "0");
    return `${h}:${m}`;
  }

  function getStoredConversations() {
    try {
      const key = getChatStorageKey();
      let raw = localStorage.getItem(key);
      if (!raw && key !== "chatfpl_saved_conversations_guest") {
        // Auto-migrate any unkeyed conversation history to this user's FPL ID
        const legacy = localStorage.getItem("chatfpl_saved_conversations");
        if (legacy) {
          localStorage.setItem(key, legacy);
          raw = legacy;
        }
      }
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function saveStoredConversations(list) {
    try {
      const key = getChatStorageKey();
      localStorage.setItem(key, JSON.stringify(list));
      updateHistoryBadge();
    } catch (e) {
      console.warn("Could not save to localStorage:", e);
    }
  }

  function updateHistoryBadge() {
    if (!historyBadge) return;
    const list = getStoredConversations();
    if (list.length > 0) {
      historyBadge.textContent = list.length;
      historyBadge.classList.remove("hidden");
    } else {
      historyBadge.classList.add("hidden");
    }
  }

  window.updateChatFplHistory = updateHistoryBadge;

  function persistCurrentSession() {
    if (!currentSessionMessages.length) return;
    const list = getStoredConversations();
    const firstUser = currentSessionMessages.find(m => m.role === "user");
    const title = firstUser ? firstUser.text.slice(0, 48) : "FPL Tactical Query";

    if (!currentSessionId) {
      currentSessionId = "conv_" + Date.now();
    }

    const record = {
      id: currentSessionId,
      title: title,
      updatedAt: Date.now(),
      messages: currentSessionMessages
    };

    const idx = list.findIndex(c => c.id === currentSessionId);
    if (idx >= 0) {
      list.splice(idx, 1);
    }
    list.unshift(record);
    saveStoredConversations(list);
  }

  function formatTimeAgo(ts) {
    const diff = Date.now() - ts;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function renderWelcomeGreeting() {
    appendBotMessage(
      "👋 **Hi! I'm ChatFPL**, your AI tactical and analytics assistant.\n\n" +
      "I am connected directly with live FotMob and FPL performance metrics and analyze your live squad in real-time. " +
      "Ask me to **query any player's advanced stats**, suggest a **transfer strategy**, or recommend your **captain**!",
      [
        "Query stats for Erling Haaland",
        "What are Cole Palmer's stats?",
        "Suggest a transfer strategy",
        "Who should I captain this week?",
        "Top speeds in Premier League"
      ],
      false
    );
  }

  function renderHistoryList() {
    if (!historyList) return;
    const list = getStoredConversations();
    historyList.innerHTML = "";

    if (!list.length) {
      historyList.innerHTML = `
        <div class="chatfpl-history-empty">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" style="margin:0 auto 10px; display:block; opacity:0.4;">
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
          </svg>
          <strong>No past conversations yet</strong><br/>
          Your conversations will be automatically saved here so you can review previous tactical advice and stats.
        </div>
      `;
      return;
    }

    list.forEach((conv) => {
      const item = document.createElement("div");
      item.className = "chatfpl-history-item" + (conv.id === currentSessionId ? " active" : "");

      const msgCount = (conv.messages || []).filter(m => m.role === "user").length;
      const countLabel = `${msgCount} prompt${msgCount !== 1 ? "s" : ""}`;

      item.innerHTML = `
        <div class="chatfpl-history-info">
          <div class="chatfpl-history-item-title" title="${escapeHtml(conv.title)}">${escapeHtml(conv.title)}</div>
          <div class="chatfpl-history-item-meta">
            <span>🕒 ${formatTimeAgo(conv.updatedAt)}</span>
            <span>•</span>
            <span>💬 ${countLabel}</span>
          </div>
        </div>
        <button class="chatfpl-history-del-btn" title="Delete conversation" aria-label="Delete conversation">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      `;

      // Click to load conversation
      item.querySelector(".chatfpl-history-info").addEventListener("click", () => {
        loadConversation(conv);
      });

      // Delete single conversation
      item.querySelector(".chatfpl-history-del-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        const updated = getStoredConversations().filter(c => c.id !== conv.id);
        saveStoredConversations(updated);
        renderHistoryList();
        if (currentSessionId === conv.id) {
          startNewConversation();
        }
      });

      historyList.appendChild(item);
    });
  }

  function loadConversation(conv) {
    currentSessionId = conv.id;
    currentSessionMessages = [...(conv.messages || [])];
    messagesEl.innerHTML = "";
    if (historyPanel) historyPanel.classList.add("hidden");

    if (currentSessionMessages.length === 0) {
      renderWelcomeGreeting();
    } else {
      currentSessionMessages.forEach((msg) => {
        if (msg.role === "user") {
          appendUserMessage(msg.text, false, msg.time);
        } else {
          appendBotMessage(msg.text, msg.suggestions || [], false, msg.time);
        }
      });
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
    input.focus();
  }

  function startNewConversation() {
    currentSessionId = null;
    currentSessionMessages = [];
    messagesEl.innerHTML = "";
    if (historyPanel) historyPanel.classList.add("hidden");
    renderWelcomeGreeting();
    input.focus();
  }

  function clearCurrentChat() {
    messagesEl.innerHTML = "";
    currentSessionMessages = [];
    if (currentSessionId) {
      const list = getStoredConversations().filter(c => c.id !== currentSessionId);
      saveStoredConversations(list);
    }
    currentSessionId = null;
    if (historyPanel) historyPanel.classList.add("hidden");
    renderWelcomeGreeting();
    input.focus();
  }

  // Initialize history badge on load
  updateHistoryBadge();

  // Toggle open / close
  launcher.addEventListener("click", () => {
    const isHidden = container.classList.contains("hidden");
    if (isHidden) {
      container.classList.remove("hidden");
      document.body.classList.add("chatfpl-open");
      input.focus();
      if (!chatFplInitialized) {
        chatFplInitialized = true;
        // Clean slate on every page reload: start fresh with welcome greeting
        renderWelcomeGreeting();
      }
    } else {
      container.classList.add("hidden");
      document.body.classList.remove("chatfpl-open");
      if (historyPanel) historyPanel.classList.add("hidden");
    }
  });

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      container.classList.add("hidden");
      document.body.classList.remove("chatfpl-open");
      if (historyPanel) historyPanel.classList.add("hidden");
    });
  }

  if (backBtn) {
    backBtn.addEventListener("click", () => {
      container.classList.add("hidden");
      document.body.classList.remove("chatfpl-open");
      if (historyPanel) historyPanel.classList.add("hidden");
    });
  }

  // Action Button: Start New Conversation
  if (newBtn) {
    newBtn.addEventListener("click", () => {
      startNewConversation();
    });
  }

  // Action Button: Clear Chat
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      clearCurrentChat();
    });
  }

  // Action Button: Past Conversations (History)
  if (historyBtn && historyPanel) {
    historyBtn.addEventListener("click", () => {
      const isHidden = historyPanel.classList.contains("hidden");
      if (isHidden) {
        renderHistoryList();
        historyPanel.classList.remove("hidden");
      } else {
        historyPanel.classList.add("hidden");
      }
    });
  }

  if (closeHistoryBtn && historyPanel) {
    closeHistoryBtn.addEventListener("click", () => {
      historyPanel.classList.add("hidden");
    });
  }

  if (clearAllHistoryBtn) {
    clearAllHistoryBtn.addEventListener("click", () => {
      saveStoredConversations([]);
      renderHistoryList();
    });
  }

  // Auto-grow textarea & Enter key submit
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 80) + "px";
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event("submit"));
    }
  });

  // Handle message submission
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;

    input.value = "";
    input.style.height = "auto";
    sendBtn.disabled = true;

    // Render user message bubble & record
    appendUserMessage(query, true);

    // Show loading indicator
    const typingIndicator = createTypingIndicator();
    messagesEl.appendChild(typingIndicator);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    try {
      const historyPayload = (currentSessionMessages.length > 1 
        ? currentSessionMessages.slice(0, -1).slice(-8)
        : []
      ).map(m => ({
        role: m.role === "user" ? "user" : "assistant",
        text: m.text
      }));

      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: query,
          history: historyPayload,
          squad: {
            starters: currentSquad.starters,
            bench: currentSquad.bench,
            bank: currentSquad.bank,
            free_transfers: currentSquad.free_transfers !== undefined ? currentSquad.free_transfers : 1
          }
        })
      });

      const data = await res.json();
      typingIndicator.remove();

      if (data.success && data.reply) {
        appendBotMessage(data.reply, data.suggestions || [], true);
      } else {
        appendBotMessage("⚠️ " + (data.error || "Sorry, I couldn't process that query. Please try again."), [], true);
      }
    } catch (err) {
      console.error("ChatFPL Error:", err);
      typingIndicator.remove();
      appendBotMessage("⚠️ Error communicating with the ChatFPL engine. Please make sure the server is online.", [], true);
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });

  // Helper: Append User Message Bubble (with WhatsApp time and double ticks)
  function appendUserMessage(text, record = true, existingTime = null) {
    const timeStr = existingTime || formatWhatsAppTime();
    const msg = document.createElement("div");
    msg.className = "chat-msg user";
    msg.innerHTML = `
      <div class="chat-bubble">
        <div>${escapeHtml(text)}</div>
        <div class="wa-msg-footer">
          <span class="wa-msg-time">${timeStr}</span>
          <span class="wa-ticks">✓✓</span>
        </div>
      </div>
    `;
    messagesEl.appendChild(msg);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (record) {
      if (!currentSessionId) {
        currentSessionId = "conv_" + Date.now();
      }
      currentSessionMessages.push({ role: "user", text, time: timeStr });
      persistCurrentSession();
    }
  }

  // Helper: Append Bot Message Bubble (with WhatsApp timestamp)
  function appendBotMessage(markdownText, suggestions = [], record = true, existingTime = null) {
    const timeStr = existingTime || formatWhatsAppTime();
    const msg = document.createElement("div");
    msg.className = "chat-msg bot";
    msg.innerHTML = `
      <div class="chat-bubble">
        <div>${renderChatMarkdown(markdownText)}</div>
        <div class="wa-msg-footer">
          <span class="wa-msg-time">${timeStr}</span>
        </div>
      </div>
    `;
    messagesEl.appendChild(msg);

    // Render suggestion chips
    renderSuggestionChips(suggestions);

    // Position view at the top of the received bot message instead of dropping to the bottom
    if (record) {
      setTimeout(() => {
        if (msg) {
          const targetTop = Math.max(0, msg.offsetTop - 12);
          messagesEl.scrollTo({ top: targetTop, behavior: "smooth" });
        }
      }, 40);
    }

    if (record) {
      if (!currentSessionId) {
        currentSessionId = "conv_" + Date.now();
      }
      currentSessionMessages.push({ role: "bot", text: markdownText, suggestions, time: timeStr });
      persistCurrentSession();
    }
  }

  // Helper: Create Typing Indicator
  function createTypingIndicator() {
    const ind = document.createElement("div");
    ind.className = "chat-msg bot";
    ind.innerHTML = `
      <div class="chat-bubble" style="padding:8px 14px;">
        <div class="chat-typing-dots">
          <span></span><span></span><span></span>
        </div>
      </div>
    `;
    return ind;
  }

  // Helper: Render Suggestion Chips
  function renderSuggestionChips(chips) {
    suggestionsEl.innerHTML = "";
    if (!chips || !chips.length) {
      suggestionsEl.style.display = "none";
      return;
    }
    suggestionsEl.style.display = "flex";

    chips.forEach((chipText) => {
      const chip = document.createElement("button");
      chip.className = "chat-chip";
      chip.type = "button";
      chip.textContent = chipText;
      chip.addEventListener("click", () => {
        input.value = chipText;
        form.dispatchEvent(new Event("submit"));
      });
      suggestionsEl.appendChild(chip);
    });
  }
}

/**
 * Welcome / First-Time Onboarding Modal Controller
 */
function initOnboardingModal() {
  const modal = document.getElementById("onboardingModal");
  const btnClose = document.getElementById("btnCloseOnboarding");
  const btnSync = document.getElementById("btnOnboardingSync");
  const btnDemo = document.getElementById("btnOnboardingDemo");
  const inputId = document.getElementById("onboardingFplInput");
  const btnToggleGuide = document.getElementById("btnToggleIdGuide");
  const guideBody = document.getElementById("onboardingGuideBody");
  const alertEl = document.getElementById("onboardingAlert");

  if (!modal) return;

  const closeModal = () => {
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    localStorage.setItem("chatfpl_onboarding_dismissed", "1");
  };

  if (btnClose) btnClose.addEventListener("click", closeModal);
  if (btnDemo) btnDemo.addEventListener("click", closeModal);

  if (btnToggleGuide && guideBody) {
    const toggleGuide = () => {
      const isOpen = btnToggleGuide.classList.toggle("open");
      guideBody.classList.toggle("open", isOpen);
      btnToggleGuide.setAttribute("aria-expanded", isOpen ? "true" : "false");
    };
    btnToggleGuide.addEventListener("click", toggleGuide);
    btnToggleGuide.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleGuide();
      }
    });
  }

  // Check if onboarding should open automatically on startup
  const hasSyncHash = window.location.hash && window.location.hash.includes("fpl_sync=");
  const dismissed = localStorage.getItem("chatfpl_onboarding_dismissed");
  const hasManager = localStorage.getItem("chatfpl_manager_id") || (currentSquad && currentSquad.manager_id);

  if (!hasSyncHash && !dismissed && !hasManager) {
    setTimeout(() => {
      if (!currentSquad.manager_id) {
        modal.classList.remove("hidden");
        document.body.style.overflow = "hidden";
      }
    }, 500);
  }

  if (btnSync && inputId) {
    btnSync.addEventListener("click", async () => {
      const rawVal = inputId.value.trim();
      let managerId = rawVal;
      const match = rawVal.match(/entry\/([0-9]+)/);
      if (match) managerId = match[1];

      if (!managerId) {
        if (alertEl) {
          alertEl.className = "auth-alert error";
          alertEl.textContent = "Please enter a valid FPL Manager ID or link.";
          alertEl.classList.remove("hidden");
        }
        return;
      }

      btnSync.disabled = true;
      btnSync.innerHTML = `<span class="icon">⏳</span> Syncing...`;
      if (alertEl) alertEl.classList.add("hidden");

      try {
        const res = await fetch("/api/import-manager", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ manager_id: managerId })
        });
        const data = await res.json();

        if (data.success) {
          if (data.players && Array.isArray(data.players)) {
            data.players.forEach(p => {
              playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
            });
          }

          currentSquad.starters = data.starters || [];
          currentSquad.bench = data.bench || [];
          currentSquad.captain = data.captain || (currentSquad.starters ? currentSquad.starters[0] : null);
          currentSquad.vice_captain = data.vice_captain || (currentSquad.starters && currentSquad.starters.length > 1 ? currentSquad.starters[1] : null);
          currentSquad.captain_name = data.captain_name || "";
          currentSquad.vice_captain_name = data.vice_captain_name || "";
          currentSquad.bank = data.bank !== undefined ? Number(data.bank) : 0.0;
          currentSquad.manager_id = data.manager_id || managerId;
          currentSquad.team_name = data.team_name || null;
          currentSquad.manager_name = data.manager_name || null;
          currentSquad.overall_points = data.overall_points !== undefined ? data.overall_points : null;
          currentSquad.overall_rank = data.overall_rank !== undefined ? data.overall_rank : null;

          document.getElementById("bankDisplay").textContent = `£${currentSquad.bank.toFixed(1)}m`;
          const navInput = document.getElementById("fplManagerId");
          if (navInput) navInput.value = data.manager_id || managerId;

          updateCaptainPills();
          syncChipsUI(data.chips);

          if (data.team_name) {
            const bannerLabel = document.getElementById("userTeamLabel");
            if (bannerLabel) bannerLabel.textContent = `Managing ${data.team_name}`;
          }
          if (data.manager_name) {
            const bannerSubtitle = document.getElementById("userTeamSubtitle");
            if (bannerSubtitle) {
              const rankStr = data.overall_rank ? ` • Rank: #${Number(data.overall_rank).toLocaleString()}` : "";
              const ptsStr = data.overall_points !== null && data.overall_points !== undefined ? ` • ${data.overall_points} pts` : "";
              bannerSubtitle.innerHTML = `Manager: <strong>${data.manager_name}</strong> (FPL ID: <strong>${data.manager_id || managerId}</strong>)${ptsStr}${rankStr}`;
            }
          }

          if (alertEl) {
            alertEl.className = "auth-alert success";
            alertEl.textContent = `🎉 Squad loaded successfully for ${data.team_name || 'Manager'}!`;
            alertEl.classList.remove("hidden");
          }

          renderPitch();
          runRecommendation();

          setTimeout(() => {
            closeModal();
          }, 1000);
        } else {
          if (alertEl) {
            alertEl.className = "auth-alert error";
            alertEl.textContent = data.error || "Failed to import squad from FPL. Please verify your ID.";
            alertEl.classList.remove("hidden");
          }
        }
      } catch (err) {
        if (alertEl) {
          alertEl.className = "auth-alert error";
          alertEl.textContent = "Network error connecting to FPL.";
          alertEl.classList.remove("hidden");
        }
      } finally {
        btnSync.disabled = false;
        btnSync.innerHTML = '<span class="icon">🔄</span> Sync Squad';
      }
    });
  }
}

// Markdown to HTML Parser for ChatFPL
function renderChatMarkdown(raw) {
  if (!raw) return "";

  let out = raw;

  // Headers
  out = out.replace(/^### (.*$)/gim, "<h3>$1</h3>");
  out = out.replace(/^#### (.*$)/gim, "<h4>$1</h4>");

  // Bold
  out = out.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

  // Inline code
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");

  // Tables
  const tableRegex = /\|(.+)\|\n\| *[-:]+[-| :]*\|\n((?:\|.*\|\n?)*)/g;
  out = out.replace(tableRegex, (match, headerRow, bodyRows) => {
    const headers = headerRow.split("|").map(h => h.trim()).filter(Boolean);
    const ths = headers.map(h => `<th>${h}</th>`).join("");

    const rows = bodyRows.trim().split("\n");
    const trs = rows.map(row => {
      const cells = row.split("|").map(c => c.trim()).filter(Boolean);
      return `<tr>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`;
    }).join("");

    return `<div class="chatfpl-table-wrapper"><table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`;
  });

  // Bullet Lists
  out = out.replace(/^\s*[-*]\s+(.*$)/gim, "<li>$1</li>");
  out = out.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");

  // Linebreaks / Paragraphs
  out = out.replace(/\n\n+/g, "<br/><br/>");

  return out;
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ============================================================================
// Authentication & FPL Profile Management Controller
// ============================================================================

let currentAuthTab = "login";

async function initAuth() {
  setupAuthEventListeners();
  if (authToken) {
    await checkCurrentUser();
  } else {
    updateAuthUI(null);
  }
}

function setupAuthEventListeners() {
  // Trigger to open auth modal: user team banner action button
  const userTeamActionBtn = document.getElementById("userTeamActionBtn");
  if (userTeamActionBtn) {
    userTeamActionBtn.addEventListener("click", () => openAuthModal());
  }

  // Close modal button
  const closeBtn = document.getElementById("authModalCloseBtn");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => closeAuthModal());
  }

  // Close on backdrop click
  const authModal = document.getElementById("authModal");
  if (authModal) {
    authModal.addEventListener("click", (e) => {
      if (e.target === authModal) closeAuthModal();
    });
  }

  // Tab buttons: Log In vs Create Account
  const tabLogin = document.getElementById("authTabLogin");
  const tabRegister = document.getElementById("authTabRegister");
  if (tabLogin && tabRegister) {
    tabLogin.addEventListener("click", () => switchAuthTab("login"));
    tabRegister.addEventListener("click", () => switchAuthTab("register"));
  }

  // Auth form submission (Login or Register)
  const authForm = document.getElementById("authForm");
  if (authForm) {
    authForm.addEventListener("submit", handleAuthSubmit);
  }

  // Profile actions (when logged in)
  const btnLogout = document.getElementById("btnProfileLogout");
  if (btnLogout) {
    btnLogout.addEventListener("click", handleLogout);
  }

  const btnSyncSquad = document.getElementById("btnProfileSyncSquad");
  if (btnSyncSquad) {
    btnSyncSquad.addEventListener("click", async () => {
      const mid = (currentUser && currentUser.manager_id) || currentSquad.manager_id || localStorage.getItem("chatfpl_manager_id");
      if (mid) {
        closeAuthModal();
        await importManagerSquad(mid);
      } else {
        showBanner("Please link a valid FPL Manager ID first.");
      }
    });
  }

}

function openAuthModal() {
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  clearAuthAlert();

  const activeManager = currentUser || (currentSquad.manager_id ? {
    manager_id: currentSquad.manager_id,
    manager_name: currentSquad.manager_name || `Manager #${currentSquad.manager_id}`,
    team_name: currentSquad.team_name || "Official Team",
    overall_points: currentSquad.overall_points !== undefined ? currentSquad.overall_points : "--",
    overall_rank: currentSquad.overall_rank !== undefined ? currentSquad.overall_rank : "--",
    badge_url: currentSquad.badge_url || currentManagerBadge,
    email: currentUser ? currentUser.email : "Local Session (Unlinked)"
  } : null);

  if (activeManager) {
    // Show logged in profile view
    document.getElementById("authLoggedOutView").classList.add("hidden");
    document.getElementById("authLoggedInView").classList.remove("hidden");
    document.getElementById("authModalTitle").textContent = "FPL Manager Profile";
    document.getElementById("authModalSubtitle").textContent = "Linked FPL Manager ID & Account Settings";
    populateProfileView(activeManager);
    const badge = activeManager.badge_url || currentManagerBadge;
    if (badge) {
      updateManagerBadge(badge, activeManager.team_name, activeManager.manager_name);
    }
  } else {
    // Show logged out login/register view
    document.getElementById("authLoggedOutView").classList.remove("hidden");
    document.getElementById("authLoggedInView").classList.add("hidden");
    switchAuthTab(currentAuthTab);
  }
}

function closeAuthModal() {
  const modal = document.getElementById("authModal");
  if (modal) modal.classList.add("hidden");
}

function switchAuthTab(tab) {
  currentAuthTab = tab;
  const tabLogin = document.getElementById("authTabLogin");
  const tabRegister = document.getElementById("authTabRegister");
  const managerGroup = document.getElementById("authManagerIdGroup");
  const submitBtn = document.getElementById("authSubmitBtn");
  const title = document.getElementById("authModalTitle");
  const subtitle = document.getElementById("authModalSubtitle");

  clearAuthAlert();

  if (tab === "login") {
    tabLogin.classList.add("active");
    tabRegister.classList.remove("active");
    if (managerGroup) managerGroup.classList.add("hidden");
    if (submitBtn) submitBtn.textContent = "Log In";
    if (title) title.textContent = "Welcome Back";
    if (subtitle) subtitle.textContent = "Log in with your email to access your linked FPL squad";
  } else {
    tabLogin.classList.remove("active");
    tabRegister.classList.add("active");
    if (managerGroup) managerGroup.classList.remove("hidden");
    if (submitBtn) submitBtn.textContent = "Create Account & Link FPL";
    if (title) title.textContent = "Create Apex FPL Account";
    if (subtitle) subtitle.textContent = "Save your FPL Manager ID and auto-sync your official team";
  }
}

function showAuthAlert(msg, isSuccess = false) {
  const alertEl = document.getElementById("authAlert");
  if (!alertEl) return;
  alertEl.className = `auth-alert ${isSuccess ? "success" : "error"}`;
  alertEl.textContent = msg;
  alertEl.classList.remove("hidden");
}

function clearAuthAlert() {
  const alertEl = document.getElementById("authAlert");
  if (alertEl) {
    alertEl.textContent = "";
    alertEl.className = "auth-alert hidden";
  }
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  clearAuthAlert();

  const email = (document.getElementById("authEmail").value || "").trim();
  const password = (document.getElementById("authPassword").value || "").trim();
  const managerIdVal = (document.getElementById("authManagerId")?.value || "").trim();

  if (!email || !password) {
    showAuthAlert("Please enter both email and password.");
    return;
  }

  const submitBtn = document.getElementById("authSubmitBtn");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Processing...";
  }

  try {
    const isLogin = currentAuthTab === "login";
    const endpoint = isLogin ? "/api/auth/login" : "/api/auth/register";
    const payload = { email, password };
    if (!isLogin && managerIdVal) {
      payload.manager_id = managerIdVal;
    }

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!data.success) {
      showAuthAlert(data.error || "Authentication failed.");
      return;
    }

    // Success
    authToken = data.token;
    currentUser = data.user;
    localStorage.setItem("chatfpl_auth_token", authToken);

    updateAuthUI(currentUser);
    showBanner(isLogin ? `Welcome back, ${currentUser.manager_name || currentUser.email}!` : `Account created and FPL team linked!`);

    // Auto sync squad if manager_id is present
    if (currentUser.manager_id) {
      const inputId = document.getElementById("fplManagerId");
      if (inputId) inputId.value = currentUser.manager_id;
      await importManagerSquad(currentUser.manager_id, null, true);
    }

    // Transition to logged-in profile view in modal
    document.getElementById("authLoggedOutView").classList.add("hidden");
    document.getElementById("authLoggedInView").classList.remove("hidden");
    document.getElementById("authModalTitle").textContent = "FPL Manager Profile";
    document.getElementById("authModalSubtitle").textContent = "Linked FPL Manager ID & Account Settings";
    populateProfileView(currentUser);

  } catch (err) {
    console.error("Auth error:", err);
    showAuthAlert("Network or server error while connecting.");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = currentAuthTab === "login" ? "Log In" : "Create Account & Link FPL";
    }
  }
}

async function checkCurrentUser() {
  if (!authToken) return;
  try {
    const res = await fetch("/api/auth/me", {
      headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (res.status === 401) {
      // Invalid/expired token
      authToken = null;
      currentUser = null;
      localStorage.removeItem("chatfpl_auth_token");
      updateAuthUI(null);
      return;
    }
    const data = await res.json();
    if (data.success && data.user) {
      currentUser = data.user;
      updateAuthUI(currentUser);
      if (currentUser.manager_id && (!currentSquad.manager_id || currentSquad.manager_id !== currentUser.manager_id)) {
        importManagerSquad(currentUser.manager_id, null, true);
      }
    } else {
      authToken = null;
      currentUser = null;
      localStorage.removeItem("chatfpl_auth_token");
      updateAuthUI(null);
    }
  } catch (e) {
    console.error("Error verifying current user:", e);
  }
}

async function handleLogout() {
  if (authToken) {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${authToken}`
        },
        body: JSON.stringify({ token: authToken })
      });
    } catch (e) {
      console.warn("Logout request failed:", e);
    }
  }

  authToken = null;
  currentUser = null;
  localStorage.removeItem("chatfpl_auth_token");
  localStorage.removeItem("chatfpl_manager_id");
  localStorage.removeItem("apex_fpl_manager_id");
  localStorage.removeItem("apex_fpl_team_name");
  localStorage.removeItem("apex_fpl_manager_name");
  localStorage.removeItem("apex_fpl_overall_rank");
  localStorage.removeItem("apex_fpl_overall_points");
  localStorage.removeItem("apex_fpl_badge_url");
  setFplButtonState("import");

  // Completely wipe active manager profile and manager ID from state
  currentSquad.manager_id = null;
  currentSquad.manager_name = null;
  currentSquad.team_name = null;
  currentSquad.badge_url = null;
  currentSquad.overall_points = null;
  currentSquad.overall_rank = null;
  currentManagerBadge = null;

  // Clear top header input field
  const inputId = document.getElementById("fplManagerId");
  if (inputId) {
    inputId.value = "";
  }

  // Reset profile view DOM fields so no stale manager profile data remains
  const profManagerName = document.getElementById("profManagerName");
  const profTeamName = document.getElementById("profTeamName");
  const profManagerId = document.getElementById("profManagerId");
  const profOverallPts = document.getElementById("profOverallPts");
  const profOverallRank = document.getElementById("profOverallRank");
  const profEmail = document.getElementById("profEmail");
  if (profManagerName) profManagerName.textContent = "Manager Name";
  if (profTeamName) profTeamName.textContent = "Team Name";
  if (profManagerId) profManagerId.textContent = "--";
  if (profOverallPts) profOverallPts.textContent = "--";
  if (profOverallRank) profOverallRank.textContent = "--";
  if (profEmail) profEmail.textContent = "--";

  updateManagerBadge(null, null, null);
  updateAuthUI(null);

  // Switch modal view back to logged-out view (login/register)
  const loggedOutView = document.getElementById("authLoggedOutView");
  const loggedInView = document.getElementById("authLoggedInView");
  if (loggedOutView) loggedOutView.classList.remove("hidden");
  if (loggedInView) loggedInView.classList.add("hidden");
  switchAuthTab("login");

  closeAuthModal();

  // Reset to default template squad
  await loadDefaultSquad();
  runRecommendation();

  showBanner("Logged out. Manager profile and FPL ID removed.");
}


let currentManagerBadge = null;

function updateManagerBadge(badgeUrl, teamName, managerName) {
  currentManagerBadge = badgeUrl || null;
  const profBadgeLarge = document.getElementById("profBadgeLarge");
  const profDefaultAvatar = document.getElementById("profDefaultAvatar");

  if (badgeUrl) {
    if (profBadgeLarge && profDefaultAvatar) {
      profBadgeLarge.src = badgeUrl;
      profBadgeLarge.classList.remove("hidden");
      profDefaultAvatar.classList.add("hidden");
    }
  } else {
    if (profBadgeLarge && profDefaultAvatar) {
      profBadgeLarge.classList.add("hidden");
      profDefaultAvatar.classList.remove("hidden");
    }
  }
}

function updateAuthUI(user) {
  const activeManager = user || (currentSquad && currentSquad.manager_id ? {
    manager_id: currentSquad.manager_id,
    manager_name: currentSquad.manager_name || `Manager #${currentSquad.manager_id}`,
    team_name: currentSquad.team_name || "Official Team",
    overall_points: currentSquad.overall_points !== undefined ? currentSquad.overall_points : null,
    overall_rank: currentSquad.overall_rank !== undefined ? currentSquad.overall_rank : null,
    badge_url: currentSquad.badge_url || currentManagerBadge
  } : null);

  const bannerLabel = document.getElementById("userTeamLabel");
  const bannerSubtitle = document.getElementById("userTeamSubtitle");
  const bannerActionText = document.getElementById("userTeamActionText");

  if (activeManager) {
    // Team Label Message: Displays user team name and manager name from FPL
    if (bannerLabel) {
      bannerLabel.textContent = `Managing ${activeManager.team_name || 'My FPL Team'}`;
    }
    if (bannerSubtitle) {
      const rankStr = activeManager.overall_rank ? ` • Rank: #${Number(activeManager.overall_rank).toLocaleString()}` : "";
      const ptsStr = activeManager.overall_points !== null && activeManager.overall_points !== undefined ? ` • ${activeManager.overall_points} pts` : "";
      bannerSubtitle.innerHTML = `Manager: <strong>${activeManager.manager_name || 'Linked'}</strong> (FPL ID: <strong>${activeManager.manager_id}</strong>)${ptsStr}${rankStr}`;
    }
    if (bannerActionText) {
      const displayName = activeManager.manager_name ? activeManager.manager_name.split(" ")[0] : "Profile";
      bannerActionText.textContent = `${displayName} (Profile)`;
    }
    const badge = activeManager.badge_url || (currentSquad && currentSquad.badge_url) || currentManagerBadge;
    if (badge) {
      updateManagerBadge(badge, activeManager.team_name, activeManager.manager_name);
    }
  } else {
    if (bannerLabel) {
      bannerLabel.textContent = "Managing Template Squad";
    }
    if (bannerSubtitle) {
      bannerSubtitle.textContent = "Click 'Sign In / Register' to link your saved FPL Manager ID";
    }
    if (bannerActionText) {
      bannerActionText.textContent = "Sign In / Register";
    }
    if (currentSquad && currentSquad.badge_url) {
      updateManagerBadge(currentSquad.badge_url, currentSquad.team_name, currentSquad.manager_name);
    } else {
      updateManagerBadge(null, null, null);
    }
  }
  if (typeof window.updateChatFplHistory === "function") {
    window.updateChatFplHistory();
  }
}

function populateProfileView(user) {
  if (!user) return;
  const profManagerName = document.getElementById("profManagerName");
  const profTeamName = document.getElementById("profTeamName");
  const profManagerId = document.getElementById("profManagerId");
  const profOverallPts = document.getElementById("profOverallPts");
  const profOverallRank = document.getElementById("profOverallRank");
  const profEmail = document.getElementById("profEmail");

  if (profManagerName) profManagerName.textContent = user.manager_name || "FPL Manager";
  if (profTeamName) profTeamName.textContent = user.team_name || "Official Team";
  if (profManagerId) profManagerId.textContent = user.manager_id ? String(user.manager_id) : "None";
  if (profOverallPts) profOverallPts.textContent = user.overall_points !== null && user.overall_points !== undefined ? `${user.overall_points} pts` : "--";
  if (profOverallRank) profOverallRank.textContent = user.overall_rank ? `#${Number(user.overall_rank).toLocaleString()}` : "--";
  if (profEmail) profEmail.textContent = user.email || "--";

  const badge = user.badge_url || currentSquad.badge_url || currentManagerBadge;
  if (badge) {
    updateManagerBadge(badge, user.team_name, user.manager_name);
  }
}

// Reusable Manager Squad Importer
async function importManagerSquad(managerId, cookie = null, silent = false) {
  if (!managerId) return false;

  // Handle caller passing silent boolean as 2nd argument
  if (typeof cookie === "boolean") {
    silent = cookie;
    cookie = null;
  }
  // Ensure player dataset is loaded before rendering pitch
  if (!allPlayers || allPlayers.length === 0) {
    await loadAllPlayers();
  }

  // Auto-sync gameweek stats whenever import squad is triggered
  autoSyncPipeline();

  const btnImport = document.getElementById("btnImportFPL");
  if (btnImport && !silent) {
    btnImport.disabled = true;
    btnImport.innerHTML = `<span class="icon">⏳</span> Syncing...`;
  }

  try {
    const payload = { manager_id: managerId };

    const res = await fetch("/api/import-manager", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      let errText = "";
      try {
        const errJson = await res.json();
        errText = errJson.error || errJson.message || "";
      } catch (_) {
        try {
          const rawText = await res.text();
          if (rawText.includes("billing is disabled")) {
            errText = "Backend error: Google Cloud billing is disabled or suspended for the Cloud Run project.";
          }
        } catch (_) {}
      }
      const finalMsg = errText || `Backend server error (${res.status}). The service could not process the request.`;
      if (!silent) alert(finalMsg);
      return false;
    }

    const data = await res.json();
    if (!data.success) {
      if (!silent) alert(data.error || "Failed to import squad from FPL.");
      return false;
    }

    if (data.players && Array.isArray(data.players)) {
      data.players.forEach(p => {
        playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
      });
    }

    currentSquad.starters = data.starters || [];
    currentSquad.bench = data.bench || [];
    currentSquad.captain = data.captain || (currentSquad.starters ? currentSquad.starters[0] : null);
    currentSquad.vice_captain = data.vice_captain || (currentSquad.starters && currentSquad.starters.length > 1 ? currentSquad.starters[1] : null);
    currentSquad.captain_name = data.captain_name || "";
    currentSquad.vice_captain_name = data.vice_captain_name || "";
    currentSquad.bank = data.bank !== undefined ? Number(data.bank) : 0.0;
    currentSquad.free_transfers = data.free_transfers !== undefined ? Number(data.free_transfers) : 1;
    currentSquad.free_transfers_allocated = data.free_transfers_allocated !== undefined ? Number(data.free_transfers_allocated) : currentSquad.free_transfers;
    currentSquad.transfers_made = data.transfers_made !== undefined ? Number(data.transfers_made) : 0;
    currentSquad.transfer_cost = data.transfer_cost !== undefined ? Number(data.transfer_cost) : 0;
    currentSquad.ft_history_breakdown = data.ft_history_breakdown || [];
    const effectiveId = data.manager_id || managerId;
    localStorage.setItem("chatfpl_manager_id", effectiveId);
    localStorage.setItem("apex_fpl_manager_id", effectiveId);
    if (data.team_name) localStorage.setItem("apex_fpl_team_name", data.team_name);
    if (data.manager_name) localStorage.setItem("apex_fpl_manager_name", data.manager_name);
    if (data.overall_rank) localStorage.setItem("apex_fpl_overall_rank", data.overall_rank);
    if (data.overall_points !== undefined && data.overall_points !== null) localStorage.setItem("apex_fpl_overall_points", data.overall_points);
    if (data.badge_url) localStorage.setItem("apex_fpl_badge_url", data.badge_url);
    setFplButtonState("reset");

    // Update bank display (Money left from official FPL)
    document.getElementById("bankDisplay").textContent = `£${currentSquad.bank.toFixed(1)}m`;

    const inputId = document.getElementById("fplManagerId");
    if (inputId) inputId.value = effectiveId;

    updateCaptainPills();
    updateSquadFtDisplay();
    syncChipsUI(data.chips);

    currentSquad.manager_id = effectiveId;
    currentSquad.badge_url = data.badge_url || null;
    currentSquad.manager_name = data.manager_name || null;
    currentSquad.team_name = data.team_name || null;
    currentSquad.overall_points = data.overall_points !== undefined ? data.overall_points : null;
    currentSquad.overall_rank = data.overall_rank !== undefined ? data.overall_rank : null;

    updateAuthUI(currentUser);

    if (!silent) {
      let syncNote = `Synced official squad for GW${data.target_gameweek || ''}`;
      if (data.was_reverted_from_freehit) {
        syncNote += ` (Reverted from temporary Free Hit in GW${data.loaded_from_gameweek ? data.loaded_from_gameweek + 1 : ''})`;
      } else if (data.applied_upcoming_transfers && data.applied_upcoming_transfers.length > 0) {
        syncNote += ` (${data.applied_upcoming_transfers.length} upcoming transfer(s) applied)`;
      }
      syncNote += ` • Bank: £${currentSquad.bank.toFixed(1)}m • ${currentSquad.free_transfers} Free Transfer${currentSquad.free_transfers === 1 ? '' : 's'}`;
      showBanner(syncNote);
    }

    renderPitch();
    runRecommendation();
    if (typeof window.updateChatFplHistory === "function") {
      window.updateChatFplHistory();
    }
    return true;
  } catch (err) {
    console.error("Manager import error:", err);
    if (!silent) alert(err && err.message ? `Error: ${err.message}` : "Error contacting FPL API.");
    return false;
  } finally {
    if (btnImport) {
      btnImport.disabled = false;
      if (fplButtonState !== "reset") {
        setFplButtonState("import");
      }
    }
  }
}

/**
 * Generates the minified 1-Click Bookmarklet javascript: snippet
 * dynamically bound to the current window.location.origin
 */
function generateFplBookmarkletCode() {
  const origin = window.location.origin;
  const script = `javascript:(async function(){
try{
  if(!location.hostname.includes("premierleague.com")){
    alert("⚠️ Apex FPL 1-Click Sync:\\nPlease click this bookmark while on official Fantasy Premier League (fantasy.premierleague.com)!");
    return;
  }
  var hid="apex-fpl-sync-hud",h=document.getElementById(hid);
  if(!h){
    h=document.createElement("div");
    h.id=hid;
    h.style.cssText="position:fixed;top:24px;right:24px;z-index:9999999;background:#090d16;color:#fff;border:2px solid #00ff87;border-radius:12px;padding:16px 20px;box-shadow:0 12px 40px rgba(0,0,0,0.7);font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;min-width:280px;pointer-events:none;transition:all 0.3s ease;";
    document.body.appendChild(h);
  }
  var setMsg=function(m,i){
    h.innerHTML='<div style="display:flex;align-items:center;gap:12px;"><span style="font-size:24px;">'+(i||"⏳")+'</span><div><strong style="color:#00ff87;display:block;font-size:11px;letter-spacing:1px;text-transform:uppercase;">Apex FPL 1-Click Sync</strong><span style="color:#f1f5f9;font-size:13px;font-weight:600;">'+m+'</span></div></div>';
  };
  setMsg("Reading official profile...","⚡");
  var meRes=await fetch("/api/me/");
  if(!meRes.ok){
    setMsg("Please log in to FPL first!","⚠️");
    setTimeout(function(){h.remove();},4000);
    alert("Please log in to your official Fantasy Premier League account first!");
    return;
  }
  var me=await meRes.json();
  var eid=me.entry;
  if(!eid){
    setMsg("No squad found on account!","⚠️");
    setTimeout(function(){h.remove();},4000);
    alert("No active FPL squad found for this account.");
    return;
  }
  setMsg("Syncing live team data...","🔄");
  var tRes=await fetch("/api/my-team/"+eid+"/");
  if(!tRes.ok){
    setMsg("Could not load /api/my-team/!","❌");
    setTimeout(function(){h.remove();},4000);
    alert("Could not load live team data from /api/my-team/.");
    return;
  }
  var tData=await tRes.json();
  var eData={};
  try{
    var er=await fetch("/api/entry/"+eid+"/");
    if(er.ok) eData=await er.json();
  }catch(e){}
  var payload={
    source:"1-click-bookmarklet",
    timestamp:Date.now(),
    manager_id:eid,
    team_name:eData.name||(me.player?me.player.entry_name:"")||("Team #"+eid),
    manager_name:(me.player&&me.player.first_name)?(me.player.first_name+" "+me.player.last_name):(eData.player_first_name?(eData.player_first_name+" "+eData.player_last_name):"Official Manager"),
    overall_points:eData.summary_overall_points||0,
    overall_rank:eData.summary_overall_rank||0,
    picks:tData.picks||[],
    chips:tData.chips||[],
    transfers:tData.transfers||{}
  };
  setMsg("Opening Apex with your live squad!","🎉");
  var jsonStr=JSON.stringify(payload);
  var b64=btoa(encodeURIComponent(jsonStr).replace(/%([0-9A-F]{2})/g,function(m,p1){return String.fromCharCode("0x"+p1);}));
  var targetUrl="${origin}/#fpl_sync="+encodeURIComponent(b64);
  setTimeout(function(){
    h.remove();
    var win=window.open(targetUrl,"_blank");
    if(!win||win.closed||typeof win.closed==="undefined"){
      window.location.href=targetUrl;
    }
  },700);
}catch(err){
  alert("Apex FPL Sync Error: "+err.message);
}
})();`;
  return script.replace(/\n\s*/g, " ");
}

/**
 * Handles ingestion of 1-Click Bookmarklet payload from window.location.hash (#fpl_sync=...)
 */
async function handleFplBookmarkletSync() {
  const hash = window.location.hash || "";
  const match = hash.match(/#fpl_sync=([^&]+)/);
  if (!match) return false;

  try {
    const rawB64 = decodeURIComponent(match[1]);
    const jsonStr = decodeURIComponent(escape(atob(rawB64)));
    const syncData = JSON.parse(jsonStr);

    console.log("Ingesting 1-Click Bookmarklet Sync Payload:", syncData);
    showBanner("⚡ Processing 1-Click Sync from Official FPL...");

    // Pre-ensure all players are available in memory
    await ensureAllPlayersLoaded();

    const res = await fetch("/api/sync-payload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: syncData })
    });
    const data = await res.json();

    if (data.success) {
      if (data.players && Array.isArray(data.players)) {
        data.players.forEach(p => {
          playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
        });
      }

      currentSquad.starters = data.starters || [];
      currentSquad.bench = data.bench || [];
      currentSquad.captain = data.captain || (currentSquad.starters ? currentSquad.starters[0] : null);
      currentSquad.vice_captain = data.vice_captain || (currentSquad.starters && currentSquad.starters.length > 1 ? currentSquad.starters[1] : null);
      currentSquad.captain_name = data.captain_name || "";
      currentSquad.vice_captain_name = data.vice_captain_name || "";
      currentSquad.bank = data.bank !== undefined ? Number(data.bank) : 0.5;
      currentSquad.free_transfers = data.free_transfers !== undefined ? Number(data.free_transfers) : 1;
      currentSquad.free_transfers_allocated = data.free_transfers_allocated !== undefined ? Number(data.free_transfers_allocated) : currentSquad.free_transfers;
      currentSquad.transfers_made = data.transfers_made !== undefined ? Number(data.transfers_made) : 0;
      currentSquad.transfer_cost = data.transfer_cost !== undefined ? Number(data.transfer_cost) : 0;
      currentSquad.ft_history_breakdown = data.ft_history_breakdown || [];
      currentSquad.manager_id = data.manager_id;
      currentSquad.team_name = data.team_name;
      currentSquad.manager_name = data.manager_name;
      currentSquad.overall_points = data.overall_points;
      currentSquad.overall_rank = data.overall_rank;

      document.getElementById("bankDisplay").textContent = `£${currentSquad.bank.toFixed(1)}m`;
      const inputId = document.getElementById("fplManagerId");
      if (inputId && data.manager_id) inputId.value = data.manager_id;

      updateCaptainPills();
      updateSquadFtDisplay();
      syncChipsUI(data.chips);

      if (data.team_name) {
        const bannerLabel = document.getElementById("userTeamLabel");
        if (bannerLabel) bannerLabel.textContent = `Managing ${data.team_name}`;
      }
      if (data.manager_name) {
        const bannerSubtitle = document.getElementById("userTeamSubtitle");
        if (bannerSubtitle) {
          const rankStr = data.overall_rank ? ` • Rank: #${Number(data.overall_rank).toLocaleString()}` : "";
          const ptsStr = (data.overall_points !== null && data.overall_points !== undefined) ? ` • ${data.overall_points} pts` : "";
          bannerSubtitle.innerHTML = `Manager: <strong>${data.manager_name}</strong> (FPL ID: <strong>${data.manager_id}</strong>)${ptsStr}${rankStr}`;
        }
      }

      renderPitch();
      runRecommendation();

      // Clean the hash cleanly without reloading
      history.replaceState(null, "", window.location.pathname + window.location.search);

      showBanner(`🎉 1-Click Sync Complete! Live squad loaded for ${data.team_name} (Bank: £${currentSquad.bank.toFixed(1)}m)`);

      const modal = document.getElementById("fplCookieModal");
      if (modal && !modal.classList.contains("hidden")) {
        modal.classList.add("hidden");
        document.body.style.overflow = "";
      }
      return true;
    } else {
      showBanner(`⚠️ Sync error: ${data.error || "Failed to process FPL sync payload."}`);
      return false;
    }
  } catch (err) {
    console.error("Error ingesting bookmarklet sync:", err);
    showBanner("⚠️ Could not parse 1-Click Sync data.");
    return false;
  }
}

/**
 * Opens the integrated Import Squad & Live Session modal
 */
function openFplCookieModal() {
  const modal = document.getElementById("fplCookieModal");
  if (!modal) return;
  const btnBookmarklet = document.getElementById("fplBookmarkletBtn");
  if (btnBookmarklet) {
    btnBookmarklet.href = generateFplBookmarkletCode();
  }
  const alertEl = document.getElementById("fplCookieAlert");
  if (alertEl) {
    alertEl.className = "auth-alert hidden";
    alertEl.textContent = "";
  }
  const inputId = document.getElementById("modalManagerIdInput");
  const headerId = document.getElementById("fplManagerId")?.value.trim() || currentSquad.manager_id || localStorage.getItem("chatfpl_manager_id") || "";
  if (inputId) {
    inputId.value = headerId;
  }

  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

/**
 * Initializes the FPL Import modal, 1-click helper, and verify listeners
 */
function initFplCookieModal() {
  const modal = document.getElementById("fplCookieModal");
  const btnClose = document.getElementById("fplCookieModalCloseBtn");
  const btnSync = document.getElementById("btnSyncAuthenticatedSquad");
  const alertEl = document.getElementById("fplCookieAlert");

  const closeModal = () => {
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  };

  if (btnClose) btnClose.addEventListener("click", closeModal);

  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });
  }

  // 1-Click Bookmarklet link setup
  const btnBookmarklet = document.getElementById("fplBookmarkletBtn");
  const btnCopyBookmarklet = document.getElementById("btnCopyBookmarkletCode");

  if (btnBookmarklet) {
    btnBookmarklet.href = generateFplBookmarkletCode();
    btnBookmarklet.addEventListener("click", (e) => {
      e.preventDefault();
      alert("💡 Don't click this button here!\n\nDrag and drop this '⚡ Sync to Apex' button onto your browser's Bookmarks Bar (or click 'Copy Bookmark Code').\n\nThen, while viewing your squad on fantasy.premierleague.com/my-team, click the bookmark to instantly sync your squad into Apex!");
    });
  }

  if (btnCopyBookmarklet) {
    btnCopyBookmarklet.addEventListener("click", () => {
      const code = generateFplBookmarkletCode();
      navigator.clipboard.writeText(code).then(() => {
        const orig = btnCopyBookmarklet.innerHTML;
        btnCopyBookmarklet.innerHTML = `<span class="icon">✅</span> Copied!`;
        setTimeout(() => { btnCopyBookmarklet.innerHTML = orig; }, 2000);
      }).catch(err => console.error("Clipboard copy error:", err));
    });
  }

  // Listen for hashchange (e.g. if bookmarklet triggers in same window)
  window.addEventListener("hashchange", handleFplBookmarkletSync);

  // Import Squad via Manager ID
  if (btnSync) {
    btnSync.addEventListener("click", async () => {
      const idInput = document.getElementById("modalManagerIdInput");
      const managerId = (idInput ? idInput.value.trim() : "") || document.getElementById("fplManagerId")?.value.trim() || currentSquad.manager_id || "";
      if (!managerId) {
        if (alertEl) {
          alertEl.className = "auth-alert error";
          alertEl.textContent = "Please enter an official FPL Manager ID or link.";
        }
        return;
      }

      btnSync.disabled = true;
      btnSync.innerHTML = `<span class="icon">⏳</span> Importing Squad...`;
      if (alertEl) {
        alertEl.className = "auth-alert hidden";
        alertEl.textContent = "";
      }

      try {
        const payload = { manager_id: managerId };

        const res = await fetch("/api/import-manager", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
          if (data.players && Array.isArray(data.players)) {
            data.players.forEach(p => {
              playersMap[p.id] = Object.assign(playersMap[p.id] || {}, p);
            });
          }

          currentSquad.starters = data.starters || [];
          currentSquad.bench = data.bench || [];
          currentSquad.captain = data.captain || (currentSquad.starters ? currentSquad.starters[0] : null);
          currentSquad.vice_captain = data.vice_captain || (currentSquad.starters && currentSquad.starters.length > 1 ? currentSquad.starters[1] : null);
          currentSquad.captain_name = data.captain_name || "";
          currentSquad.vice_captain_name = data.vice_captain_name || "";
          currentSquad.bank = data.bank !== undefined ? Number(data.bank) : 0.0;

          document.getElementById("bankDisplay").textContent = `£${currentSquad.bank.toFixed(1)}m`;
          const inputId = document.getElementById("fplManagerId");
          if (inputId) inputId.value = data.manager_id || managerId;

          updateCaptainPills();
          syncChipsUI(data.chips);

          currentSquad.manager_id = data.manager_id || managerId;
          currentSquad.badge_url = data.badge_url || null;
          currentSquad.manager_name = data.manager_name || null;
          currentSquad.team_name = data.team_name || null;
          currentSquad.overall_points = data.overall_points !== undefined ? data.overall_points : null;
          currentSquad.overall_rank = data.overall_rank !== undefined ? data.overall_rank : null;

          if (data.badge_url) updateManagerBadge(data.badge_url, data.team_name, data.manager_name);
          if (data.team_name) {
            const bannerLabel = document.getElementById("userTeamLabel");
            if (bannerLabel) bannerLabel.textContent = `Managing ${data.team_name}`;
          }
          if (data.manager_name) {
            const bannerSubtitle = document.getElementById("userTeamSubtitle");
            if (bannerSubtitle) {
              const rankStr = data.overall_rank ? ` • Rank: #${Number(data.overall_rank).toLocaleString()}` : "";
              const ptsStr = data.overall_points !== null && data.overall_points !== undefined ? ` • ${data.overall_points} pts` : "";
              bannerSubtitle.innerHTML = `Manager: <strong>${data.manager_name}</strong> (FPL ID: <strong>${data.manager_id || managerId}</strong>)${ptsStr}${rankStr}`;
            }
          }

          if (alertEl) {
            alertEl.className = "auth-alert success";
            alertEl.textContent = "Squad imported successfully!";
          }

          const bannerMsg = `Synced official squad for GW${data.target_gameweek || ''} • Bank: £${currentSquad.bank.toFixed(1)}m`;
          showBanner(bannerMsg);

          renderPitch();
          runRecommendation();

          setTimeout(() => {
            closeModal();
          }, 1100);
        } else {
          if (alertEl) {
            alertEl.className = "auth-alert error";
            alertEl.textContent = data.error || "Failed to import squad from FPL. Please verify your ID.";
          }
        }
      } catch (err) {
        if (alertEl) {
          alertEl.className = "auth-alert error";
          alertEl.textContent = "Connection error connecting to FPL.";
        }
      } finally {
        btnSync.disabled = false;
        btnSync.innerHTML = `<span class="icon">🔄</span> Import Squad`;
      }
    });
  }
}



