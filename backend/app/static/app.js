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
//
// Laid out wide and short (3 rows, hub in the middle) so the whole map fits in
// one screenful next to the colonist cards -- see MAP_VIEWBOX below.
const SECTOR_LAYOUT = {
  unexplored_west: { x: 110, y: 45 },
  resource_field_north: { x: 350, y: 45 },
  geothermal_vent: { x: 590, y: 45 },
  landing_ship: { x: 110, y: 130 },
  colony_core: { x: 350, y: 130 },
  unexplored_east: { x: 590, y: 130 },
  resource_field_south: { x: 230, y: 215 },
  alien_nest: { x: 470, y: 215 },
};

const MAP_VIEWBOX = "0 0 700 260";

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

// Each sector gets one structure of its own kind (world_seed.STRUCTURE_SITES -- this table must
// track it, and so must STRUCTURE_STYLE's effect text against world_seed.STRUCTURE_EFFECTS).
const STRUCTURE_SITE_TYPES = {
  colony_core: "habitat",
  geothermal_vent: "power_plant",
  resource_field_south: "hydroponics",
  resource_field_north: "foundry",
};

const STRUCTURE_STYLE = {
  habitat: { label: "Habitat", color: "#4ea8e0", effect: "colonists here recover 3 HP a tick" },
  power_plant: { label: "Power Plant", color: "#e0b84e", effect: "+4 energy a tick" },
  hydroponics: { label: "Hydroponics", color: "#4ee08a", effect: "+2 food a tick while powered" },
  foundry: { label: "Foundry", color: "#e08a4e", effect: "+2 metal a tick while powered" },
};

// Drawn in a 40x40 box so the same shapes serve the map (scaled in place) and the panel icons.
const GLYPH_SHAPES = {
  habitat: (c) =>
    `<polygon points="3,19 20,4 37,19" fill="${c}"/><rect x="6" y="18" width="28" height="18" fill="${c}"/>` +
    `<rect x="16" y="25" width="8" height="11" fill="#0a0e0c"/><rect x="9" y="22" width="5" height="5" fill="#0a0e0c"/>` +
    `<rect x="26" y="22" width="5" height="5" fill="#0a0e0c"/>`,
  power_plant: (c) =>
    `<rect x="4" y="4" width="32" height="32" rx="6" fill="${c}"/>` +
    `<polygon points="22,6 11,22 19,22 16,34 29,16 21,16" fill="#0a0e0c"/>`,
  hydroponics: (c) =>
    `<path d="M4 36 V20 Q20 0 36 20 V36 Z" fill="${c}"/><path d="M20 36 V19" stroke="#0a0e0c" stroke-width="2.5" fill="none"/>` +
    `<ellipse cx="14" cy="24" rx="5" ry="3" transform="rotate(-30 14 24)" fill="#0a0e0c"/>` +
    `<ellipse cx="26" cy="20" rx="5" ry="3" transform="rotate(30 26 20)" fill="#0a0e0c"/>`,
  foundry: (c) =>
    `<rect x="4" y="18" width="32" height="18" fill="${c}"/><rect x="24" y="5" width="8" height="15" fill="${c}"/>` +
    `<circle cx="29" cy="3" r="3" fill="${c}" opacity=".6"/><rect x="9" y="25" width="9" height="11" fill="#e0574e"/>` +
    `<rect x="22" y="26" width="10" height="4" fill="#0a0e0c"/>`,
};

function glyphOpacity(progress, ghost) {
  if (ghost) return 0.16;
  return progress >= 100 ? 1 : 0.35 + 0.5 * (progress / 100);
}

// A structure drawn into the map at (cx, cy). Unfinished ones are dimmer with a dashed scaffold;
// ghost=true is the faint outline of a build site nobody has started on.
function structureGlyph(type, cx, cy, size, progress = 100, ghost = false) {
  const style = STRUCTURE_STYLE[type];
  if (!style) return "";
  const scaffold =
    !ghost && progress < 100
      ? `<rect x="1" y="1" width="38" height="38" fill="none" stroke="${style.color}" stroke-width="1.5" stroke-dasharray="4 3"/>`
      : "";
  return `<g transform="translate(${cx - size / 2} ${cy - size / 2}) scale(${size / 40})" opacity="${glyphOpacity(progress, ghost)}">${GLYPH_SHAPES[type](style.color)}${scaffold}</g>`;
}

function structureIcon(type, progress = 100, ghost = false) {
  const style = STRUCTURE_STYLE[type];
  if (!style) return "";
  return `<svg class="structure-icon" viewBox="0 0 40 40" width="38" height="38" aria-hidden="true" opacity="${glyphOpacity(progress, ghost)}">${GLYPH_SHAPES[type](style.color)}</svg>`;
}

const MAP_TILE_W = 132;
const MAP_TILE_H = 72;

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function renderStatusBanner(status) {
  const el = document.getElementById("status-banner");
  // The summary link is plain markup, set only when the state changes, so the button is never
  // rebuilt under the cursor on every 2 s poll.
  const link = '<a class="banner-link" href="/summary">VIEW MISSION SUMMARY</a>';
  const html =
    status === "won"
      ? `<span>VICTORY — the colony is secure.</span>${link}`
      : status === "lost"
        ? `<span>COLONY LOST — no crew remain.</span>${link}`
        : "";
  if (el.dataset.status === status) return;
  el.dataset.status = status;
  el.className = status === "won" ? "status-banner won" : status === "lost" ? "status-banner lost" : "status-banner hidden";
  el.innerHTML = html;
}

const SWARM_KILLS_TO_COLLAPSE = 10; // must track world_seed.SWARM_KILLS_TO_COLLAPSE
const NEST_MAX_HP = 100; // must track world_seed.NEST_MAX_HP

function renderResources(resources, state) {
  const el = document.getElementById("resources");
  const nestValue = state.nest_destroyed ? "DESTROYED" : `${state.nest_health}/${NEST_MAX_HP}`;
  const tiles = [
    ...Object.entries(RESOURCE_LABELS).map(([key, label]) => [label, resources[key], ""]),
    ["Alien Nest", nestValue, state.nest_destroyed ? "" : "danger"],
    ["Swarm Kills", `${state.swarm_kills}/${SWARM_KILLS_TO_COLLAPSE}`, ""],
  ];
  el.innerHTML = tiles
    .map(
      ([label, value, cls]) => `
      <div class="stat-tile ${cls}">
        <div class="label">${label}</div>
        <div class="value">${value}</div>
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
          ? `built: ${sectorStructures.map((s) => `${(STRUCTURE_STYLE[s.structure_type] || {}).label || s.structure_type} (${s.build_progress}%)`).join(", ")}`
          : STRUCTURE_SITE_TYPES[sector.sector_id]
            ? `build site: ${STRUCTURE_STYLE[STRUCTURE_SITE_TYPES[sector.sector_id]].label}`
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

      // The building itself sits on the right of the tile, large enough to read at a glance.
      const built = sectorStructures[0];
      const siteType = STRUCTURE_SITE_TYPES[sector.sector_id];
      const gx = x + MAP_TILE_W - 36;
      const gy = y + 38;
      let structureBadge = "";
      if (built) {
        const style = STRUCTURE_STYLE[built.structure_type] || { label: built.structure_type, effect: "" };
        const done = built.build_progress >= 100;
        const note = done ? (built.hp < 100 ? `${built.hp} HP, decaying` : "complete") : `${built.build_progress}% built`;
        structureBadge =
          `<g class="map-building"><title>${escapeHtml(`${style.label}: ${style.effect} (${note})`)}</title>` +
          structureGlyph(built.structure_type, gx, gy, 56, built.build_progress) +
          (done ? "" : `<text x="${gx}" y="${y + MAP_TILE_H - 3}" text-anchor="middle" class="map-build-pct">${built.build_progress}%</text>`) +
          (done && built.hp < 100 ? `<circle cx="${gx + 25}" cy="${gy - 25}" r="4.5" class="map-decay-dot"/>` : "") +
          `</g>`;
      } else if (siteType) {
        structureBadge = structureGlyph(siteType, gx, gy, 56, 0, true);
      }

      return `
        <g class="${tileClass}">
          <title>${escapeHtml(tooltip)}</title>
          <rect x="${x}" y="${y}" width="${MAP_TILE_W}" height="${MAP_TILE_H}" rx="8" />
          <text x="${x + 10}" y="${y + 17}" class="map-tile-label">${escapeHtml(label)}</text>
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

  el.innerHTML = `<svg viewBox="${MAP_VIEWBOX}" class="map-svg" role="img" aria-label="Colony sector map">${links}${tiles}</svg>`;
}

const STRUCTURES_REQUIRED = 3; // must track engine.py's WIN_STRUCTURES_REQUIRED

function renderStructures(structures) {
  const list = Object.values(structures);
  const done = list.filter((s) => s.build_progress >= 100).length;
  document.getElementById("structures-summary").textContent = `${done}/${STRUCTURES_REQUIRED} complete`;
  const el = document.getElementById("structures");
  // Ongoing builds first (closest to done on top), finished ones after.
  list.sort((a, b) => {
    const aDone = a.build_progress >= 100;
    const bDone = b.build_progress >= 100;
    return aDone === bDone ? b.build_progress - a.build_progress : aDone - bDone;
  });
  const built = list
    .map((s) => {
      const complete = s.build_progress >= 100;
      const style = STRUCTURE_STYLE[s.structure_type] || { label: s.structure_type, effect: "" };
      const status = complete ? "complete" : `building ${s.build_progress}%`;
      const decaying = complete && s.hp < 100;
      const hpNote = decaying ? ` <span class="structure-decay">decaying: ${s.hp} hp</span>` : "";
      return `
        <div class="structure-row ${complete ? "done" : "building"}">
          ${structureIcon(s.structure_type, s.build_progress)}
          <div class="structure-body">
            <div class="structure-head">
              <span class="structure-name">${escapeHtml(style.label)}</span>
              <span class="structure-meta">${escapeHtml(s.sector_id)} &middot; ${status}${hpNote}</span>
            </div>
            <div class="structure-effect">${escapeHtml(style.effect)}</div>
            <div class="bar"><div class="bar-fill ${complete ? "health" : "stress"}" style="width:${s.build_progress}%"></div></div>
          </div>
        </div>`;
    })
    .join("");

  // One structure per sector: show the sites still open, so it is clear where the next one goes.
  const open = Object.entries(STRUCTURE_SITE_TYPES)
    .filter(([sector]) => !list.some((s) => s.sector_id === sector))
    .map(([sector, type]) => {
      const style = STRUCTURE_STYLE[type];
      return `
        <div class="structure-row open">
          ${structureIcon(type, 0, true)}
          <div class="structure-body">
            <div class="structure-head">
              <span class="structure-name">${escapeHtml(style.label)}</span>
              <span class="structure-meta">${escapeHtml(sector)} &middot; open site &middot; 15 metal</span>
            </div>
            <div class="structure-effect">${escapeHtml(style.effect)}</div>
          </div>
        </div>`;
    })
    .join("");

  el.innerHTML = built + open;
}

const SWARMLING_MAX_HP = 25; // must track world_seed.SWARMLING_HEALTH

// Event-log lines that belong on the skirmish card, and how to colour them.
const COMBAT_EVENT =
  /attacks|fires on|destroys|alien nest|nest is destroyed|nest births|assault|swarmling|falls back|too hurt|drops everything|moves out|muster|rallies|retreats|has fallen|takes cover/i;

function combatClass(line) {
  if (/ attacks .* damage|blunts it/.test(line)) return "taken";
  if (/has fallen|has starved/.test(line)) return "fallen";
  if (/fires on|destroys|turns their weapon/.test(line)) return "given";
  if (/births/.test(line)) return "spawn";
  if (/nest is destroyed|assault|rallies|moves out|muster/.test(line)) return "milestone";
  return "note";
}

// The skirmish card: where colonists and hostiles are in contact right now, the nest's health, and the
// last exchanges of fire. Built from the same state/agents/events the rest of the dashboard polls.
function renderSkirmish(state, agents, events) {
  const el = document.getElementById("skirmish");
  const aliens = Object.values(state.aliens);
  const living = agents.filter((a) => a.health > 0);
  const crewBySector = groupBy(living, (a) => a.current_sector);
  const aliensBySector = groupBy(aliens, (a) => a.sector_id);
  const contact = Object.keys(aliensBySector).filter((id) => crewBySector[id]);
  const engaged = contact.length > 0;

  let mode = ["QUIET", "quiet"];
  if (engaged) mode = ["ENGAGED", "engaged"];
  else if (state.assault_on) mode = ["ASSAULT UNDERWAY", "assault"];
  else if (state.nest_destroyed && aliens.length) mode = ["MOPPING UP", "assault"];
  else if (state.nest_destroyed) mode = ["NEST DESTROYED", "won"];
  else if (aliens.length) mode = [`${aliens.length} HOSTILE${aliens.length > 1 ? "S" : ""} AT THE NEST`, "watch"];

  const nestPct = state.nest_destroyed ? 0 : Math.max(0, (state.nest_health / NEST_MAX_HP) * 100);
  const nestText = state.nest_destroyed ? "destroyed" : `${state.nest_health}/${NEST_MAX_HP} HP`;
  const damageTaken = agents.reduce((t, a) => t + a.stats.damage_taken_total, 0);
  const fallen = agents.filter((a) => a.health <= 0).length;

  const hpChip = (name, hp, max, cls) => `
    <span class="chip ${cls}"><span class="chip-name">${escapeHtml(name)}</span>
      <span class="chip-bar"><span style="width:${Math.max(0, Math.min(100, (hp / max) * 100))}%"></span></span>
      <span class="chip-hp">${hp}</span></span>`;

  const front = engaged
    ? contact
        .map((id) => {
          const crew = crewBySector[id];
          const foes = aliensBySector[id];
          return `
          <div class="front">
            <div class="front-title">${escapeHtml(SECTOR_LABELS[id] || id)}: ${crew.length} crew vs ${foes.length} hostile${foes.length > 1 ? "s" : ""}</div>
            <div class="front-sides">
              <div class="side crew">${crew.map((a) => hpChip(a.profile.name, a.health, 100, "crew")).join("")}</div>
              <div class="side vs">VS</div>
              <div class="side hostile">${foes.map((a) => hpChip(a.alien_id.replace("swarmling_", "swarm "), a.health, SWARMLING_MAX_HP, "hostile")).join("")}</div>
            </div>
          </div>`;
        })
        .join("")
    : `<div class="front idle">${
        state.assault_on
          ? "The crew is moving out on the nest."
          : state.nest_destroyed
            ? "The nest has fallen. No contact."
            : "No contact. The swarm is holding at the nest."
      }</div>`;

  const log = events
    .filter((e) => COMBAT_EVENT.test(e))
    .slice(-8)
    .reverse()
    .map((e) => `<li class="${combatClass(e)}">${escapeHtml(e.replace("[system] ", ""))}</li>`)
    .join("");

  el.innerHTML = `
    <div class="skirmish ${mode[1]}">
      <div class="skirmish-head">
        <h2>Skirmish</h2>
        <span class="skirmish-chip ${mode[1]}">${mode[0]}</span>
      </div>
      <div class="skirmish-stats">
        <div class="skirmish-stat">
          <div class="label">Alien nest &middot; ${nestText}</div>
          <div class="bar"><div class="bar-fill nest" style="width:${nestPct}%"></div></div>
        </div>
        <div class="skirmish-stat"><div class="label">Swarm kills</div><div class="value">${state.swarm_kills}/${SWARM_KILLS_TO_COLLAPSE}</div></div>
        <div class="skirmish-stat"><div class="label">Hostiles</div><div class="value">${aliens.length}</div></div>
        <div class="skirmish-stat"><div class="label">Crew lost</div><div class="value">${fallen}</div></div>
        <div class="skirmish-stat"><div class="label">Damage taken</div><div class="value">${damageTaken}</div></div>
      </div>
      ${front}
      ${log ? `<ul class="skirmish-log">${log}</ul>` : ""}
    </div>`;
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
    renderResources(state.colony_resources, state);
    renderMap(state.sectors, agents, state.aliens, state.structures);
    renderStructures(state.structures);

    const nameToLastLine = {};
    for (const line of events) {
      const match = line.match(/^\[tick \d+\] ([^:]+): "(.+)"$/);
      if (match) nameToLastLine[match[1]] = match[2];
    }
    agents.forEach((a) => {
      a.last_dialogue = nameToLastLine[a.profile.name] || null;
    });

    renderColonists(agents);
    renderSkirmish(state, agents, events);
    renderEvents(events);
  } catch (err) {
    console.error("refresh failed", err);
  }
}

refresh();
setInterval(refresh, POLL_MS);
