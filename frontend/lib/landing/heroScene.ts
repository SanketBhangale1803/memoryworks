import {
  AdditiveBlending,
  BufferGeometry,
  CanvasTexture,
  Color,
  Float32BufferAttribute,
  Fog,
  Group,
  Line,
  LineBasicMaterial,
  LineSegments,
  MathUtils,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Raycaster,
  Scene,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  Texture,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import {
  BACKGROUND_MEMORIES,
  KIND_COLOR,
  KIND_LABEL,
  SCENARIOS,
  VERDICT_LABEL,
  type Memory,
  type Scenario,
} from "@/lib/landing/scenarios";

/* The hero: a company's memory laid out in time, and one briefing pulled out
   of it.

   A year of memories — pull requests, incidents, decisions, threads, documents
   — recede along a time axis. An agent states a change it is about to make; a
   pulse searches back through time; the memories that apply light up as it
   passes, fly forward into a briefing, and a verdict lands beneath them. Then
   the next change: the same four scenarios as the briefing demo below.

   Drag to look across the timeline; hover a memory to bring it forward (the
   story waits while you read); click for the next change. Card text is drawn
   to canvas textures in the page's font. The loop sleeps off screen and in a
   hidden tab. */

export type HeroScene = {
  setScroll: (progress: number) => void;
  setActive: (active: boolean) => void;
  dispose: () => void;
};

/* The canvas covers the whole hero; `anchor` is the column the briefing forms
   in, and sets the scene's size. */
type Options = { anchor: HTMLElement; reducedMotion: boolean; lowPower: boolean; onReady?: () => void };

const MONTH_DEPTH = 2.15; // world units per month back in time
const FRONT_Z = 2.4; // where the briefing forms
const CARD_W = 2.4;
const CARD_H = 1.125;
const SLOT_Y = [0.84, -0.38, -1.6];
const AGENT_Y = 2.06;
const VERDICT_Y = -2.62;
const AXIS_Y = -3.2;
const FAR_Z = -13 * MONTH_DEPTH;
const CYCLE = 9.2; // seconds per scenario
const HOLD_AT = 4.4; // where a reduced-motion scene rests

const VERDICT_COLOR: Record<Scenario["verdict"], string> = {
  requires_approval: "#f485ad",
  proceed_with_context: "#a168fa",
  proceed: "#50a8fc",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthLabel(monthsAgo: number): string {
  const index = 9 - monthsAgo; // now is October 2026
  const year = 2026 + Math.floor(index / 12);
  return `${MONTHS[((index % 12) + 12) % 12]} ${year}`;
}

function random(seed: number) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const smooth = (a: number, b: number, t: number) => {
  const x = MathUtils.clamp((t - a) / (b - a), 0, 1);
  return x * x * (3 - 2 * x);
};

// ---- canvas textures

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number, maxLines: number): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let line = "";
  let used = 0;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width <= width) {
      line = next;
      used += 1;
      continue;
    }
    if (lines.length === maxLines - 1) break;
    lines.push(line);
    line = word;
    used += 1;
  }
  lines.push(line);
  if (used < words.length) {
    let tail = lines[lines.length - 1];
    while (tail && ctx.measureText(`${tail}…`).width > width) tail = tail.slice(0, -1);
    lines[lines.length - 1] = `${tail.trimEnd()}…`;
  }
  return lines;
}

function makeCanvas(w: number, h: number, scale: number) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.scale(scale, scale);
  return { canvas, ctx };
}

function toTexture(canvas: HTMLCanvasElement): CanvasTexture {
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

function memoryTexture(memory: Memory, font: string, scale: number): CanvasTexture {
  const W = 640;
  const H = 300;
  const { canvas, ctx } = makeCanvas(W, H, scale);
  roundRect(ctx, 3, 3, W - 6, H - 6, 26);
  ctx.fillStyle = "rgba(16, 15, 25, 0.95)";
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
  ctx.stroke();
  ctx.fillStyle = KIND_COLOR[memory.kind];
  ctx.beginPath();
  ctx.arc(40, 52, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = `600 21px ${font}`;
  ctx.fillText(KIND_LABEL[memory.kind].toUpperCase(), 58, 59);
  ctx.fillStyle = "#f4f3fb";
  ctx.font = `600 33px ${font}`;
  wrap(ctx, memory.title, W - 64, 2).forEach((line, i) => ctx.fillText(line, 30, 118 + i * 44));
  ctx.fillStyle = "#9b98b2";
  ctx.font = `500 23px ${font}`;
  ctx.fillText(`${monthLabel(memory.monthsAgo)} · ${memory.source}`, 30, 258);
  return toTexture(canvas);
}

function agentTexture(scenario: Scenario, mono: string, scale: number): CanvasTexture {
  const W = 820;
  const H = 188;
  const { canvas, ctx } = makeCanvas(W, H, scale);
  roundRect(ctx, 3, 3, W - 6, H - 6, 30);
  ctx.fillStyle = "rgba(11, 11, 18, 0.97)";
  ctx.fill();
  ctx.lineWidth = 2.5;
  ctx.strokeStyle = "rgba(80, 168, 252, 0.6)";
  ctx.stroke();
  ctx.font = `500 21px ${mono}`;
  ctx.fillStyle = "#7f7c96";
  ctx.fillText("AN AGENT IS ABOUT TO", 34, 52);
  ctx.font = `500 30px ${mono}`;
  ctx.fillStyle = "#50a8fc";
  ctx.fillText("›", 34, 110);
  ctx.fillStyle = "#f4f3fb";
  wrap(ctx, scenario.task, W - 104, 2).forEach((line, i) => ctx.fillText(line, 66, 110 + i * 40));
  return toTexture(canvas);
}

function verdictTexture(scenario: Scenario, font: string, mono: string, scale: number): CanvasTexture {
  const W = 680;
  const H = 150;
  const { canvas, ctx } = makeCanvas(W, H, scale);
  const color = VERDICT_COLOR[scenario.verdict];
  roundRect(ctx, 3, 3, W - 6, H - 6, 30);
  ctx.fillStyle = "rgba(16, 13, 26, 0.97)";
  ctx.fill();
  ctx.lineWidth = 2.5;
  ctx.strokeStyle = color;
  ctx.stroke();
  ctx.font = `700 31px ${font}`;
  const label = VERDICT_LABEL[scenario.verdict];
  roundRect(ctx, 26, 24, ctx.measureText(label).width + 44, 54, 27);
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = color;
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  ctx.fillText(label, 48, 62);
  ctx.font = `500 21px ${mono}`;
  ctx.fillStyle = "#9b98b2";
  ctx.fillText(`briefing · ${scenario.memories.length} memories cited`, 30, 120);
  return toTexture(canvas);
}

function labelTexture(text: string, mono: string): CanvasTexture {
  const { canvas, ctx } = makeCanvas(220, 52, 1);
  ctx.font = `500 24px ${mono}`;
  ctx.fillStyle = "#8a86a3";
  ctx.fillText(text, 4, 34);
  return toTexture(canvas);
}

function glowTexture(): CanvasTexture {
  const { canvas, ctx } = makeCanvas(128, 128, 1);
  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.35, "rgba(255,255,255,0.45)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  return toTexture(canvas);
}

type Card = {
  mesh: Mesh;
  material: MeshBasicMaterial;
  halo: Mesh;
  haloMaterial: MeshBasicMaterial;
  home: Vector3;
  homeYaw: number;
  phase: number;
  hover: number;
  lit: number;
};

export function mountHeroScene(
  canvas: HTMLCanvasElement,
  { anchor, reducedMotion, lowPower, onReady }: Options,
): HeroScene {
  const renderer = new WebGLRenderer({ canvas, antialias: !lowPower, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, lowPower ? 1.25 : 1.75));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);

  const scene = new Scene();
  scene.fog = new Fog(0x050507, 9, 34);
  const camera = new PerspectiveCamera(34, 1, 0.1, 80);

  const bodyFont = getComputedStyle(document.body).fontFamily || "Inter, system-ui, sans-serif";
  const monoFont = '"SF Mono", ui-monospace, Menlo, Consolas, monospace';
  const textureScale = lowPower ? 0.75 : 1;
  const textures: Texture[] = [];
  const track = <T extends Texture>(texture: T): T => {
    textures.push(texture);
    return texture;
  };
  const glow = track(glowTexture());

  const world = new Group();
  scene.add(world);
  const cardGeometry = new PlaneGeometry(CARD_W, CARD_H);
  const haloGeometry = new PlaneGeometry(CARD_W * 1.5, CARD_H * 2.1);

  // ---- the timeline: every memory at its place in time, either side of the aisle
  const rand = random(7);
  const scenarioCards: number[][] = [];
  const memories: Memory[] = [];
  for (const scenario of SCENARIOS) {
    scenarioCards.push(scenario.memories.map((memory) => memories.push(memory) - 1));
  }
  memories.push(...(lowPower ? BACKGROUND_MEMORIES.slice(0, 10) : BACKGROUND_MEMORIES));

  const cards: Card[] = memories.map((memory, index) => {
    const side = index % 2 === 0 ? -1 : 1;
    const home = new Vector3(
      side * (1.95 + rand() * 1.5),
      -1.9 + rand() * 3.8,
      -memory.monthsAgo * MONTH_DEPTH - 0.4 - rand() * 1.4,
    );
    const material = new MeshBasicMaterial({
      map: track(memoryTexture(memory, bodyFont, textureScale)),
      transparent: true,
      opacity: 0.75,
      toneMapped: false,
    });
    const mesh = new Mesh(cardGeometry, material);
    mesh.position.copy(home);
    mesh.renderOrder = 2;
    mesh.userData.index = index;
    const haloMaterial = new MeshBasicMaterial({
      map: glow,
      color: new Color(KIND_COLOR[memory.kind]),
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthWrite: false,
      toneMapped: false,
    });
    const halo = new Mesh(haloGeometry, haloMaterial);
    halo.renderOrder = 1;
    world.add(halo, mesh);
    return { mesh, material, halo, haloMaterial, home, homeYaw: -side * 0.22, phase: rand() * 6.28, hover: 0, lit: 0 };
  });

  // ---- time axis down the aisle, with a mark every three months
  const axisMaterial = new LineBasicMaterial({ color: "#3a3550", transparent: true, opacity: 0.9 });
  const marks = [0, 3, 6, 9, 12];
  const labelled = marks.filter((m) => m > 0); // the front of the axis is now
  const axisGeometry = new BufferGeometry();
  axisGeometry.setAttribute(
    "position",
    new Float32BufferAttribute(
      [
        0, AXIS_Y, FRONT_Z - 0.6, 0, AXIS_Y, FAR_Z,
        ...marks.flatMap((m) => [-0.22, AXIS_Y, -m * MONTH_DEPTH, 0.22, AXIS_Y, -m * MONTH_DEPTH]),
      ],
      3,
    ),
  );
  const axis = new LineSegments(axisGeometry, axisMaterial);
  world.add(axis);
  const labels = labelled.map((monthsAgo) => {
    const material = new SpriteMaterial({
      map: track(labelTexture(monthLabel(monthsAgo), monoFont)),
      transparent: true,
      depthWrite: false,
    });
    const sprite = new Sprite(material);
    sprite.scale.set(1.15, 0.27, 1);
    sprite.center.set(0, 0.5);
    sprite.position.set(0.32, AXIS_Y, -monthsAgo * MONTH_DEPTH);
    world.add(sprite);
    return sprite;
  });

  // ---- the briefing: agent card, search beam and pulse, verdict
  const panel = (map: Texture) =>
    new MeshBasicMaterial({ map, transparent: true, opacity: 0, toneMapped: false, depthWrite: false });
  const agentMaterials = SCENARIOS.map((s) => panel(track(agentTexture(s, monoFont, textureScale))));
  const verdictMaterials = SCENARIOS.map((s) => panel(track(verdictTexture(s, bodyFont, monoFont, textureScale))));
  const agentGeometry = new PlaneGeometry(3.5, 0.8);
  const verdictGeometry = new PlaneGeometry(2.9, 0.64);
  const agent = new Mesh(agentGeometry, agentMaterials[0]);
  agent.position.set(0, AGENT_Y, FRONT_Z);
  agent.renderOrder = 3;
  const verdict = new Mesh(verdictGeometry, verdictMaterials[0]);
  verdict.position.set(0, VERDICT_Y, FRONT_Z);
  verdict.renderOrder = 3;
  world.add(agent, verdict);

  const beamGeometry = new BufferGeometry();
  beamGeometry.setAttribute("position", new Float32BufferAttribute([0, AGENT_Y - 0.4, FRONT_Z, 0, 0, FAR_Z], 3));
  const beamMaterial = new LineBasicMaterial({ color: "#a168fa", transparent: true, opacity: 0, depthWrite: false });
  world.add(new Line(beamGeometry, beamMaterial));
  const pulseMaterial = new SpriteMaterial({
    map: glow,
    color: new Color("#c8b4ff"),
    transparent: true,
    opacity: 0,
    blending: AdditiveBlending,
    depthWrite: false,
  });
  const pulse = new Sprite(pulseMaterial);
  pulse.scale.setScalar(1.1);
  world.add(pulse);

  // ---- interaction
  const pointer = new Vector2(0, 0);
  const tilt = new Vector2(0, 0);
  const ndc = new Vector2(2, 2);
  const raycaster = new Raycaster();
  let hovered = -1;
  let dragging = false;
  let dragX = 0;
  let downX = 0;
  let downAt = 0;
  let yaw = 0;
  let yawVelocity = 0;
  let scroll = 0;
  let scrollEased = 0;
  let scenarioIndex = 0;
  let t = reducedMotion ? HOLD_AT : 0;

  function nextScenario() {
    scenarioIndex = (scenarioIndex + 1) % SCENARIOS.length;
    agent.material = agentMaterials[scenarioIndex];
    verdict.material = verdictMaterials[scenarioIndex];
    t = reducedMotion ? HOLD_AT : 0;
  }
  function onWindowPointer(event: PointerEvent) {
    pointer.set((event.clientX / window.innerWidth) * 2 - 1, -(event.clientY / window.innerHeight) * 2 + 1);
    const rect = canvas.getBoundingClientRect();
    ndc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    if (dragging) {
      const dx = event.clientX - dragX;
      dragX = event.clientX;
      yawVelocity = MathUtils.clamp(yawVelocity + dx * 0.0016, -0.08, 0.08);
    }
  }
  function onDown(event: PointerEvent) {
    dragging = true;
    dragX = downX = event.clientX;
    downAt = performance.now();
    canvas.classList.add("is-dragging");
  }
  function onUp(event: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    canvas.classList.remove("is-dragging");
    // a click, not a drag: on to the next change
    if (performance.now() - downAt < 260 && Math.abs(event.clientX - downX) < 6) nextScenario();
  }
  function onLeave() {
    ndc.set(2, 2);
  }
  window.addEventListener("pointermove", onWindowPointer, { passive: true });
  canvas.addEventListener("pointerdown", onDown);
  window.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointerleave", onLeave);

  // ---- sizing: fit the scene to the anchor column, centred on it
  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const c = canvas.getBoundingClientRect();
    const a = anchor.getBoundingClientRect();
    // size the briefing column (3.5 wide, ~5.6 tall) to the anchor; the
    // timeline behind it may run past the column's edges
    const pxPerUnit = Math.min(a.width / 5.2, a.height / 6.6);
    const distance = h / pxPerUnit / 2 / Math.tan(MathUtils.degToRad(camera.fov / 2));
    // the column runs from the agent card (top) to the verdict (bottom); centre on it
    const middle = (AGENT_Y + 0.4 + VERDICT_Y - 0.32) / 2;
    camera.position.set(0, middle + 0.25, FRONT_Z + distance);
    // fog starts just behind the briefing, so the past fades with distance
    const fog = scene.fog as Fog;
    fog.near = distance + 1;
    fog.far = distance + 30;
    camera.lookAt(0, middle, FRONT_Z - 6);
    camera.setViewOffset(
      w,
      h,
      w / 2 - (a.left + a.width / 2 - c.left),
      h / 2 - (a.top + a.height / 2 - c.top),
      w,
      h,
    );
    camera.updateProjectionMatrix();
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  resizeObserver.observe(anchor);
  resize();

  // ---- loop
  let frame = 0;
  let active = true;
  let last = performance.now();
  let elapsed = 0;
  let readySent = false;
  const motion = reducedMotion ? 0 : 1;

  function tick(now: number) {
    frame = requestAnimationFrame(tick);
    const dt = MathUtils.clamp((now - last) / 1000, 0, 1 / 20);
    last = now;
    elapsed += dt;

    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(cards.map((card) => card.mesh), false)[0];
    const nextHover = hit ? (hit.object.userData.index as number) : -1;
    if (nextHover !== hovered) {
      hovered = nextHover;
      canvas.style.cursor = hovered >= 0 ? "pointer" : "grab";
    }
    // the story waits while someone reads a memory
    if (!reducedMotion && hovered < 0) {
      t += dt;
      if (t >= CYCLE) nextScenario();
    }

    tilt.x += (pointer.x - tilt.x) * Math.min(1, dt * 2.5);
    tilt.y += (pointer.y - tilt.y) * Math.min(1, dt * 2.5);
    yaw = MathUtils.clamp(yaw + yawVelocity, -0.55, 0.55);
    yawVelocity *= dragging ? 0.82 : 0.92;
    scrollEased += (scroll - scrollEased) * Math.min(1, dt * 6);
    world.rotation.y = yaw + tilt.x * 0.1 + Math.sin(elapsed * 0.12) * 0.03 * motion;
    world.rotation.x = -tilt.y * 0.05;
    world.position.z = scrollEased * 9; // scrolling travels back through time

    // the story's beats
    const agentIn = smooth(0, 0.6, t) * (1 - smooth(7.9, 8.6, t));
    const searching = !reducedMotion && t >= 0.6 && t < 2.6;
    const pulseZ = reducedMotion ? FAR_Z : MathUtils.lerp(FRONT_Z, FAR_Z, MathUtils.clamp((t - 0.6) / 1.8, 0, 1));
    const fly = smooth(2.5, 3.7, t) * (1 - smooth(7.9, 9.0, t));
    const verdictIn = smooth(3.7, 4.2, t) * (1 - smooth(7.7, 8.3, t));
    agent.material.opacity = agentIn;
    agent.position.y = AGENT_Y + (1 - agentIn) * 0.25;
    verdict.material.opacity = verdictIn;
    verdict.position.y = VERDICT_Y - (1 - verdictIn) * 0.2;
    beamMaterial.opacity = searching ? 0.55 : 0.12 * agentIn;
    pulseMaterial.opacity = searching ? 0.9 : 0;
    pulse.position.set(0, MathUtils.lerp(AGENT_Y - 0.4, 0, (FRONT_Z - pulseZ) / (FRONT_Z - FAR_Z)), pulseZ);

    const relevant = scenarioCards[scenarioIndex];
    cards.forEach((card, index) => {
      const order = relevant.indexOf(index);
      const isRelevant = order >= 0;
      // a memory lights up as the search passes it in time
      const passed = isRelevant && t >= 0.6 && pulseZ <= card.home.z + 0.6;
      const litTarget = passed ? 1 - smooth(7.9, 8.8, t) : 0;
      card.lit += (litTarget - card.lit) * Math.min(1, dt * 6);
      card.hover += ((hovered === index ? 1 : 0) - card.hover) * Math.min(1, dt * 10);
      const f = isRelevant ? fly : 0;
      const float = Math.sin(elapsed * 0.6 + card.phase) * 0.06 * motion;
      const slotY = SLOT_Y[Math.max(order, 0)];
      card.mesh.position.set(
        MathUtils.lerp(card.home.x, 0, f),
        MathUtils.lerp(card.home.y + float, slotY, f),
        MathUtils.lerp(card.home.z, FRONT_Z, f) + card.hover * 0.35,
      );
      card.mesh.rotation.y = card.homeYaw * (1 - f);
      card.mesh.scale.setScalar(1 + card.hover * 0.08);
      card.material.opacity = Math.min(1, 0.72 + card.lit * 0.3 + card.hover * 0.3 + f * 0.3);
      card.halo.position.set(card.mesh.position.x, card.mesh.position.y, card.mesh.position.z - 0.05);
      card.halo.rotation.y = card.mesh.rotation.y;
      card.haloMaterial.opacity = Math.max(card.lit * 0.5, card.hover * 0.4);
    });

    renderer.render(scene, camera);
    if (!readySent) {
      readySent = true;
      onReady?.();
    }
  }

  function start() {
    if (frame) return;
    last = performance.now();
    frame = requestAnimationFrame(tick);
  }
  function stop() {
    cancelAnimationFrame(frame);
    frame = 0;
  }
  function onVisibility() {
    if (document.hidden) stop();
    else if (active) start();
  }
  document.addEventListener("visibilitychange", onVisibility);
  start();

  return {
    setScroll(progress) {
      scroll = MathUtils.clamp(progress, 0, 1);
    },
    setActive(next) {
      active = next;
      if (next && !document.hidden) start();
      else stop();
    },
    dispose() {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onWindowPointer);
      window.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerleave", onLeave);
      resizeObserver.disconnect();
      textures.forEach((texture) => texture.dispose());
      [cardGeometry, haloGeometry, agentGeometry, verdictGeometry, beamGeometry, axisGeometry].forEach((g) => g.dispose());
      cards.forEach((card) => {
        card.material.dispose();
        card.haloMaterial.dispose();
      });
      [...agentMaterials, ...verdictMaterials].forEach((m) => m.dispose());
      labels.forEach((sprite) => sprite.material.dispose());
      [axisMaterial, beamMaterial, pulseMaterial].forEach((m) => m.dispose());
      renderer.dispose();
    },
  };
}
