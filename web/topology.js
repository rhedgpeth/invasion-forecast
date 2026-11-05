// The loading screen is a live map of the app's infrastructure. Every poll of GET /api/invasions/{id}
// redraws it: nodes light up, requests flow along the wires, and each node shows what its service returned.
// The same map, frozen, is the "under the hood" view.

const STAGE = { w: 1216, h: 530 };

// Static, trusted markup: positions match the design mock. Everything dynamic is set with textContent.
const LAYOUT = `
  <div class="t-zone" style="left:0;top:18px;width:210px;height:510px"><span class="t-zlabel">YOUR BROWSER</span></div>
  <div class="t-zone runpod" style="left:238px;top:18px;width:620px;height:510px"><span class="t-zlabel">RUNPOD</span></div>
  <div class="t-zone" style="left:886px;top:18px;width:330px;height:510px"><span class="t-zlabel">THE OUTSIDE WORLD · FREE APIS</span></div>

  <div class="t-wire h free" data-wire="you" style="left:190px;top:94px;width:70px"><i></i><i></i></div>
  <div class="t-wire h free" data-wire="sky" style="left:836px;top:94px;width:70px"><i></i><i></i></div>
  <div class="t-wire h free" data-wire="planets" style="left:836px;top:132px;width:36px"><i></i></div>
  <div class="t-wire v free" data-wire="planets" style="left:870px;top:132px;height:226px"><i></i><i></i></div>
  <div class="t-wire h free" data-wire="planets" style="left:870px;top:356px;width:36px"><i></i></div>
  <div class="t-wire v gpu" data-wire="species" style="left:396px;top:158px;height:122px"><i></i><i></i></div>
  <div class="t-wire v gpu" data-wire="art" style="left:696px;top:158px;height:122px"><i></i><i></i></div>
  <span class="t-break" data-break style="left:846px;top:226px">✗</span>

  <div class="t-node cpu done" style="left:20px;top:52px;width:170px;height:90px">
    <div class="t-head"><span class="t-title">YOU</span><span class="t-tag cpu">BROWSER</span></div>
    <span class="t-result" data-you></span>
  </div>
  <p class="t-note" style="left:20px;top:170px;width:170px">Asks for an invasion, then checks its progress every 1.5 s.</p>

  <div class="t-node cpu done" style="left:260px;top:40px;width:576px;height:118px">
    <div class="t-head"><span class="t-title">POD · GAME BACKEND</span><span class="t-tag cpu">RUNPOD CPU POD</span></div>
    <span class="t-sub">Python · validates input · holds the API key · runs the 4 steps · rate-limits players</span>
    <span class="t-chip" data-catalogue>SAVED PLANET CATALOGUE · standby</span>
  </div>

  <div class="t-node gpu" data-node="species" style="left:260px;top:280px;width:276px;height:220px">
    <div class="t-head"><span class="t-title">QWEN3 32B</span><span class="t-tag gpu">GPU</span></div>
    <span class="t-sub">Public Endpoint · LLM · OpenAI-compatible</span>
    <span class="t-status"></span><div class="t-result"></div><span class="t-cost"></span>
  </div>
  <div class="t-node gpu" data-node="art" style="left:560px;top:280px;width:276px;height:220px">
    <div class="t-head"><span class="t-title">FLUX.1 SCHNELL</span><span class="t-tag gpu">GPU</span></div>
    <span class="t-sub">Public Endpoint · image model · in parallel</span>
    <span class="t-status"></span><div class="t-result t-tiles"></div><span class="t-cost"></span>
  </div>

  <div class="t-node free" data-node="sky" style="left:906px;top:40px;width:290px;height:150px">
    <div class="t-head"><span class="t-title">OPEN-METEO</span><span class="t-tag free">FREE · NO KEY</span></div>
    <span class="t-sub">Where is it? What's the sky doing?</span>
    <span class="t-status"></span><div class="t-result"></div>
  </div>
  <div class="t-node free" data-node="planets" style="left:906px;top:280px;width:290px;height:220px">
    <div class="t-head"><span class="t-title">NASA EXOPLANET<br>ARCHIVE</span><span class="t-tag free">FREE · NO KEY</span></div>
    <span class="t-sub">Which planets' stars are overhead now?</span>
    <span class="t-status"></span><div class="t-result"></div>
  </div>`;

const CAPTIONS = {
  sky: (r) => `Your request reached the game backend on a Runpod Pod. It's asking Open-Meteo, a free weather API, `
    + `where ${r.city || "your city"} is and what its sky is doing.`,
  planets: (r, city) => `From ${city}'s position and the exact time, the backend works out which patch of sky is `
    + `overhead, then asks NASA which known planets orbit stars in it.`,
  planets_fallback: () => "NASA didn't answer, so the backend used its saved copy of the catalogue. Same maths, same game.",
  species: () => "Qwen3 32B on a Runpod GPU is inventing a species for each planet. The endpoint speaks the OpenAI API, "
    + "so existing code just works.",
  species_fallback: () => "The LLM didn't answer, so the backend used built-in descriptions. The game goes on.",
  art: (r) => `Painting ${r.total || 4} images at once on Runpod GPUs: one HTTPS call each, $0.003 an image, `
    + "nothing to rent or manage.",
  art_fallback: () => "Some paintings didn't arrive within 60 s, so those aliens become silhouettes. The game still plays.",
};

const el = (tag, props = {}, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };

function clock(localTime) {
  const [h, m] = localTime.slice(11, 16).split(":").map(Number);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

/** Build the map inside `host` (an element with a .t-stage child) and return a render(steps, imageBase) function. */
export function mountTopology(host, city) {
  const stage = host.querySelector(".t-stage");
  stage.innerHTML = LAYOUT;
  stage.querySelector("[data-you]").textContent = `typed "${city}"`;
  const fit = () => {
    const scale = Math.min(1, host.clientWidth / STAGE.w);
    stage.style.transform = `scale(${scale})`;
    host.style.height = `${Math.ceil(STAGE.h * scale)}px`;
  };
  host.topologyObserver?.disconnect();
  host.topologyObserver = new ResizeObserver(fit);
  host.topologyObserver.observe(host);
  fit();
  const loaded = new Map(); // image name → <img>, so each image loads once

  return function render(steps, imageBase) {
    const by = Object.fromEntries(steps.map((s) => [s.key, s]));
    for (const s of steps) {
      for (const wire of stage.querySelectorAll(`[data-wire="${s.key}"]`)) wire.className = wire.className.replace(/\b(waiting|running|done|fallback|failed)\b/g, "").trim() + ` ${s.status}`;
      const node = stage.querySelector(`[data-node="${s.key}"]`);
      node.classList.remove("waiting", "running", "done", "fallback", "failed");
      node.classList.add(s.status);
      node.querySelector(".t-status").textContent = statusText(s);
      const cost = node.querySelector(".t-cost");
      if (cost) cost.textContent = s.cost_usd ? `$${s.cost_usd.toFixed(4)}` : "";
      fill(s, node.querySelector(".t-result"), imageBase, loaded);
    }
    stage.querySelector('[data-wire="you"]').classList.add("done");
    stage.querySelector("[data-break]").hidden = by.planets?.status !== "fallback";
    const catalogue = stage.querySelector("[data-catalogue]");
    const usedCatalogue = by.planets?.status === "fallback";
    catalogue.classList.toggle("in-use", usedCatalogue);
    catalogue.textContent = usedCatalogue
      ? `SAVED PLANET CATALOGUE · IN USE · ${by.planets.result.overhead?.toLocaleString() ?? ""} overhead`
      : "SAVED PLANET CATALOGUE · standby";
    return {
      caption: caption(steps, by.sky?.result?.city),
      spend: steps.reduce((sum, s) => sum + (s.cost_usd || 0), 0),
      current: steps.findIndex((s) => s.status === "running") + 1,
    };
  };
}

function statusText(s) {
  if (s.status === "waiting") return "waiting";
  if (s.status === "running") return "◐ working…";
  if (s.status === "fallback") return `✗ fallback · ${s.seconds?.toFixed(1)} s`;
  if (s.status === "failed") return "✗ failed";
  return `✓ ${s.seconds?.toFixed(1)} s`;
}

function fill(s, box, imageBase, loaded) {
  const r = s.result || {};
  if (s.key === "sky" && r.city) {
    box.replaceChildren(`${r.city}${r.region && r.region !== r.city ? `, ${r.region}` : ""}`, el("br"),
      `${clock(r.local_time)} · ${r.condition} · ${Math.round(r.temp_c)}°C`);
  }
  if (s.key === "planets" && r.picks) {
    box.replaceChildren(
      el("span", { textContent: s.status === "fallback" ? "NASA didn't answer: used the saved catalogue" : `${r.overhead.toLocaleString()} overhead now` }),
      ...r.picks.map((p) => el("span", { className: `t-line${p.boss ? " boss" : ""}`,
        textContent: `${p.name} · ${p.climate} ${Math.round(p.temp_k).toLocaleString()} K${p.boss ? " (boss)" : ""}` })));
  }
  if (s.key === "species" && r.species) {
    box.replaceChildren(el("span", { className: "t-species", textContent: r.species.join("\n").toUpperCase() }));
  }
  if (s.key === "art" && r.total) {
    const names = ["skyline.png", ...Array.from({ length: r.total - 1 }, (_, i) => `alien-${i + 1}.png`)];
    const finished = s.status !== "running" && s.status !== "waiting";
    box.replaceChildren(...names.map((name) => {
      if (r.painted?.includes(name) && imageBase) {
        if (!loaded.has(name)) loaded.set(name, el("img", { src: imageBase + name, alt: name.replace(".png", "") }));
        return el("span", { className: `t-tile done${name === "skyline.png" ? " sky" : ""}` }, loaded.get(name));
      }
      return el("span", { className: `t-tile ${finished ? "lost" : "wait"}`, textContent: finished ? "✗" : "…" });
    }));
  }
}

function caption(steps, city = "your city") {
  const active = steps.find((s) => s.status === "running") || [...steps].reverse().find((s) => s.status !== "waiting");
  if (!active) return { text: "Contacting the game backend…", tone: "" };
  if (steps.every((s) => s.status === "done" || s.status === "fallback")) {
    return { text: "Invasion ready. Meet the invaders.", tone: "ready" };
  }
  const fallback = active.status === "fallback" && CAPTIONS[`${active.key}_fallback`];
  return { text: (fallback || CAPTIONS[active.key])(active.result || {}, city), tone: fallback ? "bad" : "" };
}
