const POLL_MS = 2000;

const RESOURCE_LABELS = { metal: "Metal", food: "Food", energy: "Energy", biomatter: "Biomatter" };

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

function renderSectors(sectors, aliens, structures) {
  const el = document.getElementById("sectors");
  const aliensBySector = groupBy(Object.values(aliens), (a) => a.sector_id);
  const structuresBySector = groupBy(Object.values(structures), (s) => s.sector_id);

  el.innerHTML = Object.values(sectors)
    .map((sector) => {
      const classes = ["card"];
      if (!sector.explored) classes.push("unexplored");
      if (sector.threat_level > 0) classes.push("threat");

      const yieldText = Object.entries(sector.resource_yield || {})
        .map(([k, v]) => `${k}+${v}`)
        .join(", ");

      const alienList = (aliensBySector[sector.sector_id] || [])
        .map((a) => `${a.alien_id} (${a.health}hp)`)
        .join(", ");

      const structureList = (structuresBySector[sector.sector_id] || [])
        .map((s) => `${s.structure_type} (${s.build_progress}%)`)
        .join(", ");

      return `
        <div class="${classes.join(" ")}">
          <div class="card-title">${sector.sector_id}</div>
          <div class="card-row"><span>${sector.sector_type}</span><span>${
        sector.explored ? "explored" : "unexplored"
      }</span></div>
          ${sector.threat_level > 0 ? `<div class="card-row"><span>threat</span><span>${sector.threat_level}</span></div>` : ""}
          ${yieldText ? `<div class="card-row"><span>yield</span><span>${yieldText}</span></div>` : ""}
          ${alienList ? `<div class="card-row"><span>aliens</span><span>${alienList}</span></div>` : ""}
          ${structureList ? `<div class="card-row"><span>built</span><span>${structureList}</span></div>` : ""}
        </div>`;
    })
    .join("");
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
    renderSectors(state.sectors, state.aliens, state.structures);

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
