const POLL_MS = 2000;

const RESOURCE_LABELS = { metal: "Metal", food: "Food", energy: "Energy", biomatter: "Biomatter" };

// Hand-placed layout for the sim's fixed 8-sector world (see world_seed.py).
// Positions are still just a legibility aid (the backend has no x/y), but
// the hub-and-spoke shape they draw IS now the real adjacency graph --
// world_seed.SECTOR_ADJACENCY matches this exactly, and engine.py's
// _resolve_explore enforces it: colony_core reaches everywhere in one move,
// two outlying sectors take two (through colony_core). If world_seed.py's
// sectors or adjacency change, this table needs updating too -- there's no
// way to derive positions from the backend since it doesn't model space,
// only which-sector-connects-to-which.
const SECTOR_LAYOUT = {
  landing_ship: { x: 100, y: 220 },
  colony_core: { x: 270, y: 220 },
  resource_field_north: { x: 270, y: 80 },
  resource_field_south: { x: 270, y: 360 },
  geothermal_vent: { x: 430, y: 140 },
  unexplored_east: { x: 430, y: 300 },
  unexplored_west: { x: 100, y: 80 },
  alien_nest: { x: 430, y: 420 },
};

const SECTOR_LABELS = {
  landing_ship: "Landing Ship",
  colony_core: "Colony Core",
  resource_field_north: "N. Field",
  resource_field_south: "S. Field",
  geothermal_vent: "Geo Vent",
  unexplored_east: "East",
  unexplored_west: "West",
  alien_nest: "Alien Nest",
};

const MAP_TILE_W = 108;
const MAP_TILE_H = 64;

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function renderStatusBanner(status) {
  const el = document.getElementById("status-banner");
  if (status === "won") {
    el.className = "status-banner won";
    el.textContent = "VICTORY — the colony is secure.";
  } else if (status === "lost") {
    el.className = "status-banner lost";
    el.textContent = "COLONY LOST — no crew remain.";
  } else {
    el.className = "status-banner hidden";
    el.textContent = "";
  }
}

function renderResources(resources) {
  const el = document.getElementById("resources");
  el.innerHTML = Object.entries(RESOURCE_LABELS)
    .map(
      ([key, label]) => `
      <div class="stat-tile">
        <div class="label">${label}</div>
        <div class="value">${resources[key]}</div>
      </div>`
    )
    .join("");
}

function renderMap(sectors, agents, aliens, structures) {
  const el = document.getElementById("map");
  const aliensBySector = groupBy(Object.values(aliens), (a) => a.sector_id);
  const structuresBySector = groupBy(Object.values(structures), (s) => s.sector_id);
  const agentsBySector = groupBy(agents, (a) => a.current_sector);

  const tiles = Object.values(sectors)
    .map((sector) => {
      const pos = SECTOR_LAYOUT[sector.sector_id];
      if (!pos) return ""; // unknown sector id -- skip rather than guess a position
      const label = SECTOR_LABELS[sector.sector_id] || sector.sector_id;
      const threatened = sector.threat_level > 0;
      const x = pos.x - MAP_TILE_W / 2;
      const y = pos.y - MAP_TILE_H / 2;

      const sectorAliens = aliensBySector[sector.sector_id] || [];
      const sectorStructures = structuresBySector[sector.sector_id] || [];
      const yieldText = Object.entries(sector.resource_yield || {})
        .map(([k, v]) => `${k}+${v}`)
        .join(", ");
      const tooltip = [
        sector.sector_id,
        `${sector.sector_type} — ${sector.explored ? "explored" : "unexplored"}`,
        threatened ? `threat level ${sector.threat_level}` : null,
        yieldText ? `yield: ${yieldText}` : null,
        sectorAliens.length ? `aliens: ${sectorAliens.map((a) => `${a.alien_id} (${a.health}hp)`).join(", ")}` : null,
        sectorStructures.length
          ? `built: ${sectorStructures.map((s) => `${s.structure_type} (${s.build_progress}%)`).join(", ")}`
          : null,
      ]
        .filter(Boolean)
        .join("\n");

      const tileClass = ["map-tile", sector.explored ? "explored" : "unexplored", threatened ? "threat" : ""]
        .join(" ")
        .trim();

      const colonistMarkers = (agentsBySector[sector.sector_id] || [])
        .map((a, i) => {
          const cx = x + 15 + i * 17;
          const cy = y + MAP_TILE_H - 13;
          const captainClass = a.profile.is_captain ? " captain" : "";
          return `
            <circle cx="${cx}" cy="${cy}" r="7" class="map-colonist${captainClass}"><title>${escapeHtml(a.profile.name)} — ${a.health}hp</title></circle>
            <text x="${cx}" y="${cy + 3}" text-anchor="middle" class="map-colonist-label">${escapeHtml(a.profile.name[0])}</text>`;
        })
        .join("");

      const alienMarkers = sectorAliens
        .map((a, i) => {
          const cx = x + MAP_TILE_W - 13 - i * 14;
          const cy = y + MAP_TILE_H - 13;
          return `<circle cx="${cx}" cy="${cy}" r="5" class="map-alien"><title>${escapeHtml(a.alien_id)} — ${a.health}hp</title></circle>`;
        })
        .join("");

      const structureBadge = sectorStructures.length
        ? `<circle cx="${x + MAP_TILE_W - 11}" cy="${y + 11}" r="6" class="map-structure"><title>${sectorStructures
            .map((s) => `${s.structure_type} ${s.build_progress}%`)
            .join(", ")}</title></circle>`
        : "";

      return `
        <g class="${tileClass}">
          <title>${escapeHtml(tooltip)}</title>
          <rect x="${x}" y="${y}" width="${MAP_TILE_W}" height="${MAP_TILE_H}" rx="8" />
          <text x="${pos.x}" y="${y + 19}" text-anchor="middle" class="map-tile-label">${escapeHtml(label)}</text>
          ${structureBadge}
          ${colonistMarkers}
          ${alienMarkers}
        </g>`;
    })
    .join("");

  // Lines from colony_core to every other sector -- this is the real
  // adjacency graph now (see SECTOR_LAYOUT's own note), not decoration.
  const hub = SECTOR_LAYOUT.colony_core;
  const links = Object.keys(sectors)
    .filter((id) => id !== "colony_core" && SECTOR_LAYOUT[id])
    .map((id) => {
      const pos = SECTOR_LAYOUT[id];
      return `<line x1="${hub.x}" y1="${hub.y}" x2="${pos.x}" y2="${pos.y}" class="map-link" />`;
    })
    .join("");

  el.innerHTML = `<svg viewBox="0 0 540 460" class="map-svg" role="img" aria-label="Colony sector map">${links}${tiles}</svg>`;
}

function renderColonists(agents) {
  const el = document.getElementById("colonists");
  el.innerHTML = agents
    .map((agent) => {
      const lastLine = agent.last_dialogue ? `<div class="dialogue">"${agent.last_dialogue}"</div>` : "";
      const captainBadge = agent.profile.is_captain
        ? `<span class="captain-badge">&#9733; CAPTAIN</span>`
        : "";
      const loyaltyRow = agent.profile.is_captain
        ? ""
        : `
          <div>loyalty</div>
          <div class="bar"><div class="bar-fill loyalty" style="width:${agent.loyalty * 10}%"></div></div>`;
      return `
        <div class="card">
          <div class="card-title">${agent.profile.name} <span style="color:var(--text-dim)">— ${agent.profile.role}</span> ${captainBadge}</div>
          <div class="card-row"><span>sector</span><span>${agent.current_sector}</span></div>
          <div>health</div>
          <div class="bar"><div class="bar-fill health" style="width:${agent.health}%"></div></div>
          <div>stress</div>
          <div class="bar"><div class="bar-fill stress" style="width:${agent.stress_level * 10}%"></div></div>
          ${loyaltyRow}
          ${lastLine}
        </div>`;
    })
    .join("");
}

function renderEvents(events) {
  const el = document.getElementById("events");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  el.innerHTML = events.map((e) => `<div>${escapeHtml(e)}</div>`).join("");
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function groupBy(list, keyFn) {
  return list.reduce((acc, item) => {
    const key = keyFn(item);
    (acc[key] = acc[key] || []).push(item);
    return acc;
  }, {});
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function refresh() {
  try {
    const [state, agents, events] = await Promise.all([
      fetchJSON("/state"),
      fetchJSON("/agents"),
      fetchJSON("/events?limit=100"),
    ]);

    document.getElementById("tick-count").textContent = state.tick;
    renderStatusBanner(state.status);
    renderResources(state.colony_resources);
    renderMap(state.sectors, agents, state.aliens, state.structures);

    const nameToLastLine = {};
    for (const line of events) {
      const match = line.match(/^\[tick \d+\] ([^:]+): "(.+)"$/);
      if (match) nameToLastLine[match[1]] = match[2];
    }
    agents.forEach((a) => {
      a.last_dialogue = nameToLastLine[a.profile.name] || null;
    });

    renderColonists(agents);
    renderEvents(events);
  } catch (err) {
    console.error("refresh failed", err);
  }
}

refresh();
setInterval(refresh, POLL_MS);
