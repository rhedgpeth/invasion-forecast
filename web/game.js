// The shooter, one wave at a time. playWave() resolves when the wave is cleared or the city falls.
// The world is 320x200 "arcade pixels"; CSS scales the canvas up with crisp, square pixels.
// Everything a wave needs comes from the game pack: the sprite, hits, speed and colour all trace back
// to a real planet (see the bio card), and the skyline is the city painted for this game.

export const W = 320;
export const H = 200;

const SHIP = [
  "......X......",
  ".....XXX.....",
  "....XXXXX....",
  "..X.XXXXX.X..",
  ".XXXXXXXXXXX.",
  "XXXXX.X.XXXXX",
  "XX....X....XX",
];

// Drawn when the GPU couldn't paint an alien in time, so the game still plays.
const STAND_IN = [
  "................",
  "....XX....XX....",
  ".....X....X.....",
  "....XXXXXXXX....",
  "...XXXXXXXXXX...",
  "..XXX..XX..XXX..",
  "..XXX..XX..XXX..",
  "..XXXXXXXXXXXX..",
  "..XXXXXXXXXXXX..",
  "...XXXXXXXXXX...",
  "...X.XX..XX.X...",
  "..X..X....X..X..",
  "..X..........X..",
  "................",
  "................",
  "................",
];

const FONT = "8px 'Press Start 2P', monospace";
const KEYS = new Set(["ArrowLeft", "ArrowRight", "KeyA", "KeyD", "Space"]);

function bitmap(rows, colour) {
  const c = document.createElement("canvas");
  c.width = rows[0].length;
  c.height = rows.length;
  const ctx = c.getContext("2d");
  ctx.fillStyle = colour;
  rows.forEach((row, y) => [...row].forEach((ch, x) => ch === "X" && ctx.fillRect(x, y, 1, 1)));
  return c;
}

/** The 16x16 sprite for a wave: the painted one, or a grey stand-in if the GPU didn't deliver. */
export function spriteCanvas(img) {
  if (!img) return bitmap(STAND_IN, "#5A5A74");
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  c.getContext("2d").drawImage(img, 0, 0, 16, 16);
  return c;
}

export function playWave({ canvas, wave, sprite, skyline, score, lives, onHud }) {
  return new Promise((resolve) => {
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    const ship = bitmap(SHIP, "#FFD23F");
    const keys = new Set();
    const player = { x: W / 2 - 6, y: H - 12, w: 13, h: 7, cooldown: 0, safe: 1 };
    const shots = [];
    const bombs = [];
    const pops = [];
    const aliens = [];
    let boss = null;
    let dir = 1;
    let fireIn = 1.5;
    let state = "intro";
    let stateTime = 0;
    let paused = false;
    let finished = false;

    if (wave.boss) {
      boss = { x: W / 2 - 24, y: 22, w: 48, h: 48, hp: wave.hits, max: wave.hits, flash: 0, t: 0 };
    } else {
      for (let r = 0; r < 3; r++) {
        for (let c = 0; c < 7; c++) aliens.push({ x: 56 + c * 32, y: 24 + r * 20, w: 16, h: 16, hp: wave.hits, flash: 0 });
      }
    }
    const total = aliens.length;

    const down = (e) => {
      if (KEYS.has(e.code)) { e.preventDefault(); keys.add(e.code); }
      if (e.code === "KeyP" || e.code === "Escape") paused = !paused;
    };
    const up = (e) => keys.delete(e.code);
    const hidden = () => { if (document.hidden) paused = true; };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    document.addEventListener("visibilitychange", hidden);

    function finish(won) {
      finished = true;
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      document.removeEventListener("visibilitychange", hidden);
      resolve({ won, score, lives });
    }

    function hud() { onHud({ score, lives }); }

    function overlaps(a, b) {
      return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    }

    function pop(x, y, colour) { pops.push({ x, y, t: 0, colour }); }

    function update(dt) {
      stateTime += dt;
      if (state === "intro") { if (stateTime > 1.2) { state = "play"; stateTime = 0; } return; }
      if (state !== "play") { if (stateTime > 1.6) finish(state === "won"); return; }

      // Player
      const move = (keys.has("ArrowRight") || keys.has("KeyD")) - (keys.has("ArrowLeft") || keys.has("KeyA"));
      player.x = Math.max(2, Math.min(W - player.w - 2, player.x + move * 100 * dt));
      player.cooldown -= dt;
      player.safe -= dt;
      if (keys.has("Space") && player.cooldown <= 0 && shots.length < 2) {
        shots.push({ x: player.x + 6, y: player.y - 4, w: 1, h: 4 });
        player.cooldown = 0.3;
      }
      for (const s of shots) s.y -= 170 * dt;

      // Aliens march, speeding up as their numbers fall (the planet's year sets the base speed)
      if (boss) {
        boss.t += dt;
        boss.x = W / 2 - 24 + Math.sin(boss.t * 0.9 * wave.speed) * 110;
        boss.y = 22 + boss.t * 1.2;
        boss.flash -= dt;
        if (boss.y + boss.h >= player.y) return lose();
      } else {
        const v = 12 * wave.speed * (1 + 2 * (1 - aliens.length / total));
        for (const a of aliens) { a.x += dir * v * dt; a.flash -= dt; }
        const minX = Math.min(...aliens.map((a) => a.x));
        const maxX = Math.max(...aliens.map((a) => a.x + a.w));
        if ((dir > 0 && maxX > W - 4) || (dir < 0 && minX < 4)) {
          dir = -dir;
          for (const a of aliens) a.y += 6;
        }
        if (aliens.some((a) => a.y + a.h >= player.y)) return lose();
      }

      // Aliens fire back
      fireIn -= dt;
      if (fireIn <= 0) {
        if (boss) {
          for (const vx of [-25, 0, 25]) bombs.push({ x: boss.x + 23, y: boss.y + 44, vx, w: 2, h: 5 });
          fireIn = (1 + Math.random() * 0.6) / wave.speed;
        } else {
          const lowest = Object.values(aliens.reduce((cols, a) => {
            const col = Math.round(a.x);
            if (!cols[col] || cols[col].y < a.y) cols[col] = a;
            return cols;
          }, {}));
          const a = lowest[Math.floor(Math.random() * lowest.length)];
          bombs.push({ x: a.x + 7, y: a.y + 16, vx: 0, w: 2, h: 5 });
          fireIn = (1.3 + Math.random()) / wave.speed;
        }
      }
      for (const b of bombs) { b.y += (60 + 20 * wave.speed) * dt; b.x += b.vx * dt; }

      // Hits
      for (const s of shots) {
        if (boss && overlaps(s, boss)) {
          s.y = -99; boss.hp -= 1; boss.flash = 0.12; score += 10;
          if (boss.hp <= 0) { score += 1000; pop(boss.x + 24, boss.y + 24, wave.colour); boss = null; return win(); }
        }
        for (const a of aliens) {
          if (s.y > -50 && overlaps(s, a)) {
            s.y = -99; a.hp -= 1; a.flash = 0.12; score += 5;
            if (a.hp <= 0) { score += 50 * wave.number; pop(a.x + 8, a.y + 8, wave.colour); }
          }
        }
      }
      for (let i = aliens.length - 1; i >= 0; i--) if (aliens[i].hp <= 0) aliens.splice(i, 1);
      if (!boss && aliens.length === 0) return win();

      for (const b of bombs) {
        if (player.safe <= 0 && overlaps(b, player)) {
          b.y = H + 99; lives -= 1; player.safe = 1.5; pop(player.x + 6, player.y + 3, "#FFD23F");
          if (lives <= 0) return lose();
        }
      }

      prune(shots, (s) => s.y > -6);
      prune(bombs, (b) => b.y < H + 6);
      for (const p of pops) p.t += dt;
      prune(pops, (p) => p.t < 0.35);
      hud();
    }

    function win() { state = "won"; stateTime = 0; hud(); }
    function lose() { state = "lost"; stateTime = 0; hud(); }

    function prune(list, keep) { for (let i = list.length - 1; i >= 0; i--) if (!keep(list[i])) list.splice(i, 1); }

    function draw() {
      if (skyline) {
        ctx.drawImage(skyline, 0, 0, W, H);
        ctx.fillStyle = "rgba(7, 7, 12, 0.45)";
        ctx.fillRect(0, 0, W, H);
      } else {
        ctx.fillStyle = "#0B0B1A";
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = "#3A3A50";
        for (let i = 0; i < 40; i++) ctx.fillRect((i * 97) % W, (i * 53) % (H - 40), 1, 1);
      }

      const blink = (t) => t > 0 && Math.floor(t * 40) % 2 === 0;
      for (const a of aliens) if (!blink(a.flash)) ctx.drawImage(sprite, Math.round(a.x), Math.round(a.y));
      if (boss) {
        if (!blink(boss.flash)) ctx.drawImage(sprite, Math.round(boss.x), Math.round(boss.y), 48, 48);
        ctx.fillStyle = "#2A2A3D";
        ctx.fillRect(60, 6, 200, 4);
        ctx.fillStyle = wave.colour;
        ctx.fillRect(60, 6, Math.round(200 * boss.hp / boss.max), 4);
      }
      if (player.safe <= 0 || Math.floor(player.safe * 10) % 2 === 0) ctx.drawImage(ship, Math.round(player.x), player.y);

      ctx.fillStyle = "#FFD23F";
      for (const s of shots) ctx.fillRect(Math.round(s.x), Math.round(s.y), s.w, s.h);
      ctx.fillStyle = wave.colour;
      for (const b of bombs) ctx.fillRect(Math.round(b.x), Math.round(b.y), b.w, b.h);
      for (const p of pops) {
        const r = 2 + p.t * 30;
        ctx.fillStyle = p.colour;
        for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, -1.4], [0, 1.4], [-1.4, 0], [1.4, 0]]) {
          ctx.fillRect(Math.round(p.x + dx * r), Math.round(p.y + dy * r), 2, 2);
        }
      }

      const banner = { intro: wave.boss ? "BOSS INCOMING" : `WAVE ${wave.number}`, won: wave.boss ? "BOSS DESTROYED" : "WAVE CLEARED",
                       lost: "CITY FALLEN" }[state];
      if (banner) text(banner, H / 2);
      if (paused) text("PAUSED", H / 2);
    }

    function text(message, y) {
      ctx.font = FONT;
      ctx.textAlign = "center";
      ctx.fillStyle = "#07070C";
      ctx.fillRect(W / 2 - message.length * 4 - 6, y - 10, message.length * 8 + 12, 16);
      ctx.fillStyle = "#FFD23F";
      ctx.fillText(message, W / 2, y + 3);
    }

    let last = performance.now();
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!paused) update(dt);
      draw();
      if (!finished) requestAnimationFrame(frame);
    }
    hud();
    requestAnimationFrame(frame);
  });
}
