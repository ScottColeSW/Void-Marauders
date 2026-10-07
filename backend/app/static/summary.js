// The mission summary page: what the colony did, once the run is over. The dashboard's status banner
// links here when the game ends. If the colony is still running when this page is opened, it waits
// and fills itself in the moment the run finishes, so no refresh is needed.

const POLL_MS = 3000;
const NEST_MAX_HP = 100; // must track world_seed.NEST_MAX_HP
const STRUCTURES_REQUIRED = 3; // must track engine.py's WIN_STRUCTURES_REQUIRED

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function tile(label, value) {
  return `<div class="stat-tile"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

function renderWaiting(state) {
  document.getElementById("summary").innerHTML = `
    <div class="gameover-banner waiting">
      <h2>THE COLONY IS STILL RUNNING</h2>
      <p>Tick ${state.tick}. This page fills in on its own when the run ends.</p>
    </div>`;
}

function renderSummary(state, agents, events) {
  const won = state.status === "won";
  const alive = agents.filter((a) => a.health > 0).length;
  const complete = Object.values(state.structures).filter((s) => s.build_progress >= 100).length;
  const res = state.colony_resources;
  const totals = agents.reduce(
    (t, a) => ({
      gathered: t.gathered + a.stats.resources_gathered_total,
      contributed: t.contributed + a.stats.resources_contributed_total,
    }),
    { gathered: 0, contributed: 0 }
  );

  const headline = won ? "READY FOR HUMANITY TO ARRIVE" : "THE COLONY HAS FALLEN";
  const tagline = won
    ? "The landing zone is secure. The nest is gone, the structures stand, and the crew held the line. The next ship can come down."
    : "No crew remain. Whatever arrives next will find only the wreckage and the silence.";
  const nestText = state.nest_destroyed ? "Destroyed" : `${state.nest_health}/${NEST_MAX_HP} HP`;

  const crewRows = agents
    .map((a) => {
      const s = a.stats;
      const fallen = a.health <= 0;
      return `
        <tr class="${fallen ? "fallen" : ""}">
          <td>${escapeHtml(a.profile.name)}<span class="crew-role">${escapeHtml(a.profile.role)}</span></td>
          <td>${fallen ? "fallen" : `${a.health} hp`}</td>
          <td>${s.resources_gathered_total}</td>
          <td>${s.resources_contributed_total}</td>
          <td>${s.aliens_killed}</td>
          <td>${s.damage_taken_total}</td>
          <td>${s.orders_issued}</td>
        </tr>`;
    })
    .join("");

  const moments = events
    .filter((e) => /\[system\]|destroys|breaks ground|construction complete/.test(e))
    .slice(-10)
    .map((e) => `<li>${escapeHtml(e.replace("[system] ", ""))}</li>`)
    .join("");

  document.getElementById("summary").innerHTML = `
    <div class="gameover-banner ${won ? "won" : "lost"}">
      <h2>${headline}</h2>
      <p>${tagline}</p>
    </div>
    <div class="stat-row gameover-stats">
      ${tile("Ticks", state.tick)}
      ${tile("Crew alive", `${alive}/${agents.length}`)}
      ${tile("Structures", `${complete}/${STRUCTURES_REQUIRED}`)}
      ${tile("Alien nest", nestText)}
      ${tile("Swarm kills", state.swarm_kills)}
      ${tile("Hostiles left", Object.keys(state.aliens).length)}
    </div>
    <div class="stat-row gameover-stats">
      ${tile("Metal", res.metal)}${tile("Food", res.food)}${tile("Energy", res.energy)}${tile("Biomatter", res.biomatter)}
      ${tile("Gathered", totals.gathered)}${tile("Delivered", totals.contributed)}
    </div>
    <h3>Crew</h3>
    <div class="crew-table-wrap">
      <table class="crew-table">
        <thead><tr><th>Colonist</th><th>Status</th><th>Gathered</th><th>Delivered</th><th>Kills</th><th>Damage taken</th><th>Orders given</th></tr></thead>
        <tbody>${crewRows}</tbody>
      </table>
    </div>
    ${moments ? `<h3>Key moments</h3><ul class="moments">${moments}</ul>` : ""}
    <div class="gameover-actions">
      <button id="summary-again" class="gameover-btn primary">Run it again</button>
      <a href="/" class="gameover-btn">Back to the dashboard</a>
    </div>`;

  document.getElementById("summary-again").onclick = async () => {
    const btn = document.getElementById("summary-again");
    btn.disabled = true;
    btn.textContent = "Starting...";
    try {
      const resp = await fetch("/reset", { method: "POST" });
      if (!resp.ok) throw new Error(`/reset -> ${resp.status}`);
      window.location.href = "/";
    } catch (err) {
      console.error("reset failed", err);
      btn.disabled = false;
      btn.textContent = "Could not restart: try again";
    }
  };
}

async function load() {
  try {
    const state = await fetchJSON("/state");
    if (state.status === "active") {
      renderWaiting(state);
      setTimeout(load, POLL_MS);
      return;
    }
    const [agents, events] = await Promise.all([fetchJSON("/agents"), fetchJSON("/events?limit=200")]);
    renderSummary(state, agents, events);
  } catch (err) {
    console.error("summary load failed", err);
    document.getElementById("summary").innerHTML =
      `<div class="gameover-banner waiting"><h2>CANNOT REACH THE COLONY</h2><p>Is the backend running?</p></div>`;
    setTimeout(load, POLL_MS);
  }
}

load();
