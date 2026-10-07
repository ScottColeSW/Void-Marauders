// The Start and Quit buttons, shared by the dashboard and the mission summary page.
//
// The server builds the colony at boot and then waits: nothing happens until Start. Quit is a clean
// shutdown (it lets the current tick finish, saves colonist memory, unloads the models from Ollama
// and exits the server), so this page is the only thing the player has to touch to put the game away.
//
// Buttons are only rebuilt when the phase changes, never on every poll, so a click never lands on
// an element that was just replaced.

(() => {
  const POLL_MS = 2000;
  const controls = document.getElementById("controls");
  const banner = document.getElementById("ready-banner"); // dashboard only
  let phase = null;
  let quitting = false;

  function makeButton(label, className, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `ctl-btn ${className}`;
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  async function post(path) {
    const res = await fetch(path, { method: "POST" });
    if (!res.ok) throw new Error(`${path} -> ${res.status}`);
    return res.json();
  }

  async function start(event) {
    const btn = event.currentTarget;
    btn.disabled = true;
    btn.textContent = "Starting...";
    try {
      const body = await post("/start");
      setPhase(body.phase);
    } catch (err) {
      console.error("start failed", err);
      btn.disabled = false;
      btn.textContent = "Could not start: try again";
    }
  }

  async function quit() {
    if (!window.confirm("Quit? This stops the colony, saves the crew's memories and unloads the models.")) return;
    quitting = true;
    showStopped("Shutting down...", "Finishing the current tick, saving memory and unloading the models.");
    try {
      await post("/quit");
    } catch (err) {
      console.error("quit failed", err); // the server may already be going away; the poll below settles it
    }
  }

  function showStopped(title, detail) {
    let overlay = document.getElementById("stopped-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "stopped-overlay";
      overlay.className = "stopped-overlay";
      overlay.setAttribute("role", "alertdialog");
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="stopped-card"><h2></h2><p></p></div>`;
    overlay.querySelector("h2").textContent = title;
    overlay.querySelector("p").textContent = detail;
  }

  function render() {
    controls.replaceChildren();
    if (phase === "ready") controls.appendChild(makeButton("START", "primary", start));
    controls.appendChild(makeButton("QUIT", "danger", quit));

    if (banner) {
      banner.replaceChildren();
      if (phase === "ready") {
        const text = document.createElement("span");
        text.textContent = "COLONY READY — the crew has landed and is waiting for your order.";
        banner.append(text, makeButton("START", "primary", start));
        banner.classList.remove("hidden");
      } else {
        banner.classList.add("hidden");
      }
    }
  }

  function setPhase(next) {
    if (next === phase) return;
    phase = next;
    render();
  }

  async function poll() {
    try {
      const res = await fetch("/control");
      if (!res.ok) throw new Error(`/control -> ${res.status}`);
      const body = await res.json();
      if (body.phase === "stopping" && !quitting) {
        quitting = true;
        showStopped("Shutting down...", "The colony server is saving and unloading the models.");
      }
      setPhase(body.phase);
    } catch (err) {
      // Unreachable. After Quit that is exactly the success case: the server has exited.
      if (quitting) {
        showStopped("Colony server stopped", "The crew's memories are saved and the models are unloaded. You can close this tab.");
        return; // nothing left to poll
      }
    }
    setTimeout(poll, POLL_MS);
  }

  poll();
})();
