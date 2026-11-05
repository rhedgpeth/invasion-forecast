// Screens: title → incoming (a live map of the infrastructure) → bio card → wave → … → game over. Plus errors.
// The backend contract is two calls: POST /api/invasions starts a game, GET /api/invasions/{id} reports progress.
import { playWave, spriteCanvas } from "./game.js";
import { mountTopology } from "./topology.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;
// Demo fault switch: /?simulate=gpu_down (the server only honours it when DEMO_FAULTS=1)
const simulate = new URLSearchParams(location.search).get("simulate") || "";

const STATUS = { waiting: "WAITING", running: "WORKING", done: "DONE", fallback: "FALLBACK", failed: "FAILED" };
const RECEIPT = { sky: "Sky · Open-Meteo", planets: "Planets · NASA", species: "Species · Runpod Qwen3", art: "Art · Runpod FLUX" };
const ERROR_TITLES = { city_not_found: "UNKNOWN CITY", weather_down: "CAN'T READ THE SKY", no_planets: "EMPTY SKY" };

let game = null; // { pack, base, saved, skyline, sprites, score, lives, wave }
let lastCity = "";
let live = null; // the invasion being built: { steps, images, city }, for "under the hood" while loading

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pad = (n) => String(n).padStart(6, "0");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function show(id) {
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== id;
  window.scrollTo(0, 0);
}

function el(tag, attrs = {}, ...children) {
  const node = Object.assign(document.createElement(tag), attrs);
  node.append(...children);
  return node;
}

// ---------- 1. Start an invasion ----------

$("city-form").addEventListener("submit", (e) => { e.preventDefault(); start($("city"), $("city-error")); });
$("again-form").addEventListener("submit", (e) => { e.preventDefault(); start($("again-city"), $("again-error")); });

async function start(input, errorEl) {
  const city = input.value.trim();
  input.removeAttribute("aria-invalid");
  errorEl.textContent = "";
  if (city.length < 2) return fieldError(input, errorEl, "Type a city name: letters, 2 to 80 characters.");

  let resp;
  try {
    resp = await fetch(`/api/invasions${simulate ? `?simulate=${encodeURIComponent(simulate)}` : ""}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ city }),
    });
  } catch {
    return showError("NO SIGNAL", "Couldn't reach the game server. Check your connection and try again.", city);
  }
  const body = await resp.json().catch(() => ({}));
  if (resp.status === 422) return fieldError(input, errorEl, body.error?.message);
  if (resp.status === 429) return coolDown(input.form, errorEl, body.error, Number(resp.headers.get("Retry-After")) || 20);
  if (!resp.ok) return showError("SOMETHING BROKE", body.error?.message || `The server answered ${resp.status}.`, city);

  lastCity = city;
  game = null;
  $("incoming-sub").textContent = `Defending ${city}`;
  $("meet").hidden = true;
  $("spend").textContent = "$0.0000";
  const render = mountTopology($("topology"), city);
  $("incoming-title").textContent = "INVASION INCOMING…";
  show("incoming");
  follow(body.poll, city, render);
}

function fieldError(input, errorEl, message) {
  input.setAttribute("aria-invalid", "true");
  errorEl.textContent = message;
  input.focus();
}

function coolDown(form, errorEl, error, seconds) {
  const button = form.querySelector("button");
  button.disabled = true;
  const tick = () => {
    if (seconds <= 0) {
      button.disabled = false;
      button.textContent = "INSERT COIN";
      errorEl.textContent = "";
      return;
    }
    button.textContent = `INSERT COIN · ${seconds}`;
    errorEl.textContent = error?.code === "too_fast"
      ? `Easy, defender: one invasion every 20 seconds. Next coin in ${seconds} s.` : error?.message;
    seconds -= 1;
    setTimeout(tick, 1000);
  };
  tick();
}

// ---------- 2. Follow the backend, step by step ----------

async function follow(url, city, render) {
  for (;;) {
    let job;
    try {
      job = await (await fetch(url)).json();
    } catch {
      await sleep(POLL_MS); // a dropped poll is harmless: ask again
      continue;
    }
    if (!job.status) return showError("INVASION LOST", job.error?.message || "That invasion expired.", city);
    live = { steps: job.steps, images: job.images, city };
    showProgress(render(job.steps, job.images), city, job.steps.length);
    if (job.status === "ready") {
      await prepareGame(job.pack, false);
      return ready(city);
    }
    if (job.status === "failed") {
      return showError(ERROR_TITLES[job.error.code] || "INVASION ABORTED", job.error.message, city);
    }
    await sleep(POLL_MS);
  }
}

function showProgress(view, city, total) {
  $("spend").textContent = `$${view.spend.toFixed(4)}`;
  $("caption-text").textContent = view.caption.text;
  $("caption").className = `caption ${view.caption.tone}`;
  if (view.current) $("incoming-sub").textContent = `Defending ${city} · step ${view.current} of ${total}`;
}

function ready(city) {
  $("incoming-title").textContent = "INVASION READY";
  $("incoming-sub").textContent = `Defending ${city} · ready`;
  $("meet").hidden = false;
  $("meet").focus(); // Space or Enter: a booth host can talk through the map first
}

$("meet").addEventListener("click", () => showCard());

/** Replay a saved invasion's recorded steps through the map in ~15 s: attract mode, and a demo backup. */
async function replay(name) {
  const url = `/packs/${name}/pack.json`;
  const pack = await (await fetch(url)).json();
  const city = pack.city.name;
  const base = `/packs/${name}/`;
  const render = mountTopology($("topology"), city);
  $("meet").hidden = true;
  $("incoming-title").textContent = "INVASION INCOMING…";
  $("incoming-sub").textContent = `Defending ${city} · replay of a saved invasion`;
  show("incoming");
  const total = pack.steps.reduce((t, s) => t + (s.seconds || 0), 0);
  const speed = Math.max(1, total / 15);
  let start = 0;
  const spans = pack.steps.map((s) => { const span = [start, start + (s.seconds || 0.5) / speed]; start = span[1]; return span; });
  const began = performance.now();
  for (;;) {
    const t = (performance.now() - began) / 1000;
    const steps = pack.steps.map((s, i) => {
      const [from, to] = spans[i];
      if (t < from) return { ...s, status: "waiting", seconds: null, cost_usd: 0, result: {} };
      if (t >= to) return s;
      const share = (t - from) / (to - from);
      const result = s.key === "art"
        ? { ...s.result, painted: s.result.painted.slice(0, Math.floor(share * s.result.total)) } : {};
      return { ...s, status: "running", seconds: null, cost_usd: s.key === "art" ? s.cost_usd * share : 0, result };
    });
    showProgress(render(steps, base), city, steps.length);
    if (t >= start) break;
    await sleep(250);
  }
  await prepareGame(url, true);
  ready(city);
}

// ---------- Load a game pack (fresh from the backend, or a saved one) ----------

async function prepareGame(packUrl, saved) {
  const pack = await (await fetch(packUrl)).json();
  const base = packUrl.replace(/pack\.json$/, "");
  const [skyline, ...sprites] = await Promise.all([
    loadImage(pack.skyline && base + pack.skyline),
    ...pack.waves.map((w) => loadImage(w.sprite && base + w.sprite)),
  ]);
  game = { pack, base, saved, skyline, sprites: sprites.map(spriteCanvas), score: 0, lives: 3, wave: 0 };
}

async function loadGame(packUrl, saved) {
  await prepareGame(packUrl, saved);
  showCard();
}

function loadImage(src) {
  if (!src) return Promise.resolve(null);
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null); // a missing image becomes a stand-in, never a crash
    img.src = src;
  });
}

// ---------- 3. Bio card: who's attacking, and why ----------

function showCard() {
  const { pack, wave } = game;
  const w = pack.waves[wave];
  const p = w.planet;
  $("card-wave").textContent = w.boss ? "BOSS WAVE" : `WAVE ${w.number} OF ${pack.waves.length}`;
  $("card-wave").classList.toggle("boss", w.boss);
  $("card-city").textContent = `Invading ${pack.city.name} · ${pack.sky.time_of_day}, ${pack.sky.condition}, ${Math.round(pack.sky.temp_c)}°C`;

  const portrait = $("card-sprite").getContext("2d");
  portrait.clearRect(0, 0, 16, 16);
  portrait.drawImage(game.sprites[wave], 0, 0);
  $("card-sprite-note").textContent = w.sprite
    ? "Shape written by Qwen3, painted by FLUX, both on Runpod."
    : "The GPU didn't finish this one in time, so it's a stand-in silhouette.";

  $("card-name").textContent = w.species.toUpperCase();
  $("card-origin").textContent = `From ${p.name}, a real planet ${p.light_years.toLocaleString()} light-years away, `
    + `${Math.round(p.altitude_deg)}° above ${pack.city.name} right now.`;
  bar("tough", w.boss ? 10 : w.hits * 2, `${plural(w.hits, "hit")} · the planet is ${p.radius_earths.toFixed(1)}× Earth's size`);
  bar("speed", Math.round((w.speed - 0.5) * 8), p.year_days ? `its year lasts ${formatYear(p.year_days)}` : "year unknown");
  bar("heat", Math.max(1, Math.min(10, Math.round(p.temp_k / 400))), `${Math.round(p.temp_k).toLocaleString()} K · a ${w.climate} world`);
  $("card-look").textContent = w.look;
  $("card-bio").textContent = w.bio || "";
  $("card-bio").previousElementSibling.hidden = !w.bio;

  $("card-go").textContent = `DEFEND ${pack.city.name.toUpperCase()}`;
  show("card");
  $("card-go").focus(); // Space or Enter starts the wave
}

function bar(name, segments, label) {
  $(`bar-${name}`).replaceChildren(...Array.from({ length: 10 }, (_, i) => el("i", { className: i < segments ? "on" : "" })));
  $(`txt-${name}`).textContent = label;
}

function formatYear(days) {
  if (days < 1) return `${Math.round(days * 24)} hours`;
  if (days < 2) return `${days.toFixed(1)} days`;
  if (days < 365) return `${Math.round(days)} days`;
  return `${(days / 365.25).toFixed(1)} Earth years`;
}

$("card-go").addEventListener("click", playCurrentWave);

// ---------- 4. Play a wave ----------

async function playCurrentWave() {
  const { pack, wave } = game;
  const w = pack.waves[wave];
  show("play");
  $("hud-wave").textContent = w.boss ? `BOSS · ${w.species.toUpperCase()}` : `WAVE ${w.number}/${pack.waves.length} · ${w.species.toUpperCase()}`;
  $("hud-sky").textContent = `${pack.city.name} · ${Math.round(pack.sky.temp_c)}°C · ${pack.sky.condition}`;
  $("legend-alien").textContent = `${w.species} from ${w.planet.name}: ${plural(w.hits, "hit")} each`;
  $("game").focus(); // keys go to the game, not the (now hidden) DEFEND button

  const result = await playWave({
    canvas: $("game"), wave: w, sprite: game.sprites[wave], skyline: game.skyline, sky: pack.sky,
    score: game.score, lives: game.lives, quitButton: $("quit"),
    onHud: ({ score, lives }) => {
      $("hud-score").textContent = pad(score);
      $("hud-lives").textContent = "▲ ".repeat(Math.max(0, lives)).trim() || "–";
    },
  });
  game.score = result.score;
  game.lives = result.lives;
  if (result.quit) {
    show("title");
    $("city").focus();
    return;
  }
  if (!result.won) return showOver(false);
  game.wave += 1;
  if (game.wave < pack.waves.length) showCard(); else showOver(true);
}

// ---------- 5. Game over: the log and the receipt ----------

function showOver(saved) {
  const { pack } = game;
  $("over-title").textContent = saved ? "CITY SAVED" : "CITY FALLEN";
  $("over-score").textContent = `SCORE ${pad(game.score)}`;
  $("over-line").textContent = saved
    ? `You held ${pack.city.name} against invaders from ${pack.waves.length} real planets.`
    : `${pack.city.name} fell to the ${pack.waves[game.wave].species}.`;
  $("over-log").replaceChildren(...pack.waves.map((w, i) => el("li", {
    textContent: `${w.species} from ${w.planet.name}: ${w.climate}, ${w.planet.light_years.toLocaleString()} light-years`
      + (i < game.wave || saved ? " · defeated" : i === game.wave ? " · took the city" : " · never arrived"),
  })));

  const when = new Date(pack.created).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  $("receipt-city").textContent = `${pack.city.name} · ${when}${game.saved ? " · saved invasion" : ""}`;
  $("receipt-rows").replaceChildren(...pack.steps.flatMap((s) => [
    el("span", { textContent: RECEIPT[s.key] + (s.status === "fallback" ? " (fallback)" : "") }),
    el("span", { className: "dim", textContent: s.seconds == null ? "" : `${s.seconds.toFixed(1)} s` }),
    el("span", { textContent: s.cost_usd ? `$${s.cost_usd.toFixed(4)}` : "free" }),
  ]));
  $("receipt-total").textContent = `$${pack.cost_usd.toFixed(4)}`;
  $("again-city").value = "";
  $("again-error").textContent = "";
  show("over");
}

// ---------- 6. Errors ----------

function showError(title, message, city) {
  $("error-title").textContent = title;
  $("error-message").textContent = message;
  lastCity = city || lastCity;
  show("error");
  $("error-retry").focus();
}

$("error-retry").addEventListener("click", () => {
  $("city").value = lastCity;
  show("title");
  $("city-form").requestSubmit();
});
$("error-home").addEventListener("click", () => { show("title"); $("city").focus(); });

// ---------- Under the hood: the same map, frozen, plus every request ----------

function openHood() {
  const source = game ? { steps: game.pack.steps, images: game.base, city: game.pack.city.name } : live;
  if (!source) return;
  mountTopology($("hood-topology"), source.city)(source.steps, source.images);
  $("hood-steps").replaceChildren(...source.steps.map((s) => el("li", {},
    el("div", { className: "hood-row" },
      el("span", { className: `badge ${s.status}`, textContent: STATUS[s.status] || s.status }),
      el("b", { textContent: s.label }),
      el("span", { className: "muted", textContent: [s.seconds == null ? "" : `${s.seconds.toFixed(1)} s`,
        s.cost_usd ? `$${s.cost_usd.toFixed(4)}` : "free"].filter(Boolean).join(" · ") })),
    el("p", { className: "muted", textContent: s.detail }),
    ...(s.requests || []).map((r) => el("code", { textContent: r })),
  )));
  $("hood").showModal();
}

document.addEventListener("click", (e) => { if (e.target.closest("[data-hood]")) openHood(); });
$("hood-close").addEventListener("click", () => $("hood").close());
document.addEventListener("keydown", (e) => {
  if (e.code !== "KeyU" || e.target.matches("input") || !$("play").hidden) return;
  if ($("hood").open) $("hood").close(); else openHood();
});

// ---------- Saved invasions: offline play, the demo backup ----------

async function listSaved() {
  try {
    const packs = await (await fetch("/api/packs")).json();
    if (!packs.length) return;
    for (const [wrap, list] of [[$("saved"), $("saved-list")], [$("error-saved"), $("error-saved").querySelector(".saved-list")]]) {
      list.replaceChildren(...packs.map((p) => el("button", {
        type: "button", textContent: p.name.replace("-", " · ").toUpperCase(), onclick: () => loadGame(p.url, true),
      })));
      wrap.hidden = false;
    }
  } catch { /* no saved invasions available: the buttons simply don't appear */ }
}

listSaved();
// /?replay=tokyo-night replays a saved invasion's loading map: attract mode, and the demo backup if the GPU is slow.
const replayName = new URLSearchParams(location.search).get("replay");
if (replayName && /^[a-z0-9-]+$/.test(replayName)) replay(replayName);
else $("city").focus();
