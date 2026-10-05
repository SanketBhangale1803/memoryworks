import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  ExtrudeGeometry,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  LineBasicMaterial,
  LineLoop,
  MathUtils,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  Object3D,
  PerspectiveCamera,
  PMREMGenerator,
  PointLight,
  Points,
  PointsMaterial,
  Raycaster,
  Scene,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { SYMBOL_PATHS } from "@/components/brandPaths";

/* The hero: the logo's three bars, extruded into pearly slabs that catch the
   site's gradient as coloured light. They tilt toward the pointer, spin when
   dragged, lift when hovered, and drift apart as the hero scrolls away. A ring
   of memory motes orbits them.

   One draw call per bar, one for the motes, one for the dust, two rings — light
   enough to sit behind a headline on a laptop GPU. The loop sleeps whenever the
   canvas is off screen or the tab is hidden. */

const BRAND = ["#50a8fc", "#a168fa", "#f485ad", "#fecb91"];

export type HeroScene = {
  setScroll: (progress: number) => void;
  setActive: (active: boolean) => void;
  dispose: () => void;
};

/* The canvas covers the whole hero so the motes can roam; `anchor` is the
   column the logo should sit in, and sets its size. */
type Options = { anchor: HTMLElement; reducedMotion: boolean; lowPower: boolean; onReady?: () => void };

export function mountHeroScene(canvas: HTMLCanvasElement, { anchor, reducedMotion, lowPower, onReady }: Options): HeroScene {
  const renderer = new WebGLRenderer({ canvas, antialias: !lowPower, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, lowPower ? 1.25 : 1.75));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.setClearColor(0x000000, 0);

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const envTexture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environment = envTexture;

  const camera = new PerspectiveCamera(32, 1, 0.1, 100);
  camera.position.set(0, 0, 9.5);

  // Coloured lights circle the slabs, so their reflections sweep the gradient.
  const lights = BRAND.map((hex, i) => {
    const light = new PointLight(hex, lowPower ? 46 : 60, 14, 1.4);
    scene.add(light);
    return { light, phase: (i / BRAND.length) * Math.PI * 2 };
  });

  // ---- the three bars, from the logo's own outlines
  const root = new Group();
  scene.add(root);
  const logo = new Group();
  root.add(logo);

  const svg = `<svg xmlns="http://www.w3.org/2000/svg"><g transform="scale(1 -1)">${SYMBOL_PATHS.map((d) => `<path d="${d}"/>`).join("")}</g></svg>`;
  const parsed = new SVGLoader().parse(svg);
  const extrude = {
    depth: 34,
    bevelEnabled: true,
    bevelThickness: 9,
    bevelSize: 6,
    bevelSegments: lowPower ? 4 : 8,
    curveSegments: lowPower ? 16 : 32,
  };
  const geometries = parsed.paths.map((path) => new ExtrudeGeometry(SVGLoader.createShapes(path), extrude));
  // Centre of the whole symbol, so each bar sits where the logo puts it.
  const all = new Vector3();
  const centres = geometries.map((geometry) => {
    geometry.computeBoundingBox();
    const centre = new Vector3();
    geometry.boundingBox!.getCenter(centre);
    all.add(centre);
    return centre;
  });
  all.divideScalar(centres.length);
  const UNIT = 4.4 / 560; // the symbol is ~560 units wide; make it ~4.4 world units

  const bars = geometries.map((geometry, i) => {
    const centre = centres[i];
    geometry.translate(-centre.x, -centre.y, -centre.z);
    const material = new MeshPhysicalMaterial({
      color: new Color("#dcd6f5"),
      metalness: 0.45,
      roughness: 0.2,
      clearcoat: 1,
      clearcoatRoughness: 0.06,
      iridescence: 1,
      iridescenceIOR: 1.55,
      iridescenceThicknessRange: [180, 720],
      envMapIntensity: 1.15,
      emissive: new Color("#a168fa"),
      emissiveIntensity: 0,
    });
    const mesh = new Mesh(geometry, material);
    mesh.scale.setScalar(UNIT);
    const home = new Vector3((centre.x - all.x) * UNIT, (centre.y - all.y) * UNIT, 0);
    mesh.position.copy(home);
    mesh.userData.index = i;
    logo.add(mesh);
    return {
      mesh,
      material,
      home,
      lift: 0,
      spin: 0,
      spinTarget: 0,
      // intro: each bar arrives from depth, staggered
      intro: reducedMotion ? 1 : 0,
      introDelay: 0.15 + i * 0.14,
      // scroll: each bar leaves along its own direction
      scatter: new Vector3((i - 1) * 1.1, (i - 1) * -0.25, (1 - i) * 1.6),
    };
  });
  logo.rotation.set(-0.18, 0.32, 0.04);

  // ---- motes: small facets orbiting in a tilted band
  const MOTES = lowPower ? 48 : 96;
  const moteGeometry = new IcosahedronGeometry(0.045, 0);
  // flat, untoned colour: the motes read as small points of light
  const moteMaterial = new MeshBasicMaterial({ toneMapped: false });
  const motes = new InstancedMesh(moteGeometry, moteMaterial, MOTES);
  const moteData = Array.from({ length: MOTES }, (_, i) => ({
    radius: 2.9 + Math.random() * 1.6,
    angle: (i / MOTES) * Math.PI * 2 + Math.random() * 0.4,
    speed: 0.05 + Math.random() * 0.08,
    height: (Math.random() - 0.5) * 0.9,
    size: 0.5 + Math.random() * 1.1,
    wobble: Math.random() * Math.PI * 2,
  }));
  const tmpColor = new Color();
  moteData.forEach((_, i) => {
    tmpColor.set(BRAND[i % BRAND.length]);
    motes.setColorAt(i, tmpColor);
  });
  const band = new Group();
  band.rotation.set(1.15, 0.2, -0.32);
  band.add(motes);
  root.add(band);

  // two hairline orbit rings
  const ringMaterial = new LineBasicMaterial({ color: "#b8a6ff", transparent: true, opacity: 0.16, depthWrite: false });
  const rings = [3.25, 4.25].map((r) => {
    const points = new Float32Array(160 * 3);
    for (let i = 0; i < 160; i += 1) {
      const a = (i / 160) * Math.PI * 2;
      points.set([Math.cos(a) * r, Math.sin(a) * r, 0], i * 3);
    }
    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new BufferAttribute(points, 3));
    const ring = new LineLoop(geometry, ringMaterial);
    band.add(ring);
    return ring;
  });

  // ---- dust: a soft field of points far behind
  const DUST = lowPower ? 380 : 900;
  const dustPositions = new Float32Array(DUST * 3);
  const dustColors = new Float32Array(DUST * 3);
  for (let i = 0; i < DUST; i += 1) {
    const r = 6 + Math.random() * 10;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    dustPositions.set([r * Math.sin(phi) * Math.cos(theta), r * Math.sin(phi) * Math.sin(theta) * 0.6, -Math.abs(r * Math.cos(phi)) - 2], i * 3);
    tmpColor.set(BRAND[i % BRAND.length]).lerp(new Color("#ffffff"), 0.4);
    dustColors.set([tmpColor.r, tmpColor.g, tmpColor.b], i * 3);
  }
  const dustGeometry = new BufferGeometry();
  dustGeometry.setAttribute("position", new BufferAttribute(dustPositions, 3));
  dustGeometry.setAttribute("color", new BufferAttribute(dustColors, 3));
  const dustMaterial = new PointsMaterial({ size: 0.035, vertexColors: true, transparent: true, opacity: 0.7, depthWrite: false, blending: AdditiveBlending, sizeAttenuation: true });
  const dust = new Points(dustGeometry, dustMaterial);
  scene.add(dust);

  // ---- interaction
  const pointer = new Vector2(0, 0); // -1..1, for tilt
  const ndc = new Vector2(2, 2); // for raycasting; off-canvas until moved
  const tilt = new Vector2(0, 0);
  const raycaster = new Raycaster();
  let hovered = -1;
  let dragging = false;
  let dragX = 0;
  let spinVelocity = 0;
  let spinAngle = 0;
  let scroll = 0;
  let scrollEased = 0;
  let downAt = 0;
  let downX = 0;

  function localPoint(event: PointerEvent) {
    const rect = canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  }
  function onWindowPointer(event: PointerEvent) {
    pointer.set((event.clientX / window.innerWidth) * 2 - 1, -(event.clientY / window.innerHeight) * 2 + 1);
    const p = localPoint(event);
    ndc.set(p.x * 2 - 1, -p.y * 2 + 1);
    if (dragging) {
      const dx = event.clientX - dragX;
      dragX = event.clientX;
      spinVelocity = MathUtils.clamp(spinVelocity + dx * 0.0022, -0.6, 0.6);
    }
  }
  function onDown(event: PointerEvent) {
    dragging = true;
    dragX = event.clientX;
    downX = event.clientX;
    downAt = performance.now();
    canvas.classList.add("is-dragging");
  }
  function onUp(event: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    canvas.classList.remove("is-dragging");
    // a tap (not a drag) on a bar flips it once around its long axis
    if (performance.now() - downAt < 260 && Math.abs(event.clientX - downX) < 6 && hovered >= 0) {
      bars[hovered].spinTarget += Math.PI * 2;
    }
  }
  function onLeave() {
    ndc.set(2, 2);
  }
  window.addEventListener("pointermove", onWindowPointer, { passive: true });
  canvas.addEventListener("pointerdown", onDown);
  window.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointerleave", onLeave);

  // ---- sizing
  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // size the symbol (4.4 × ~2 units) to its column, then shift the view so
    // the origin lands on the column's centre
    const c = canvas.getBoundingClientRect();
    const a = anchor.getBoundingClientRect();
    const pxPerUnit = Math.min((a.width * 0.74) / 4.4, (a.height * 0.5) / 2);
    camera.position.z = h / pxPerUnit / 2 / Math.tan(MathUtils.degToRad(camera.fov / 2));
    const ax = a.left + a.width / 2 - c.left;
    const ay = a.top + a.height / 2 - c.top;
    camera.setViewOffset(w, h, w / 2 - ax, h / 2 - ay, w, h);
    camera.updateProjectionMatrix();
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  resizeObserver.observe(anchor);
  resize();

  // ---- loop
  let active = true;
  let frame = 0;
  let last = performance.now();
  let elapsed = 0;
  let readySent = false;
  const dummy = new Object3D();
  const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

  function tick(now: number) {
    frame = requestAnimationFrame(tick);
    const dt = MathUtils.clamp((now - last) / 1000, 0, 1 / 20);
    last = now;
    elapsed += dt;
    const motion = reducedMotion ? 0 : 1;

    // pointer tilt, eased
    tilt.x += (pointer.x - tilt.x) * Math.min(1, dt * 3.2);
    tilt.y += (pointer.y - tilt.y) * Math.min(1, dt * 3.2);
    scrollEased += (scroll - scrollEased) * Math.min(1, dt * 6);

    // drag spin with inertia, plus a slow idle sway
    spinAngle += spinVelocity;
    spinVelocity *= dragging ? 0.85 : 0.94;
    const idle = Math.sin(elapsed * 0.35) * 0.18 * motion;

    root.rotation.y = tilt.x * 0.32 + spinAngle + idle + scrollEased * 0.9;
    root.rotation.x = -tilt.y * 0.22 + scrollEased * 0.35;
    root.position.y = scrollEased * 1.4;

    // hover
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(bars.map((b) => b.mesh), false)[0];
    const nextHover = hit ? (hit.object.userData.index as number) : -1;
    if (nextHover !== hovered) {
      hovered = nextHover;
      canvas.style.cursor = hovered >= 0 ? "pointer" : "grab";
    }

    bars.forEach((bar, i) => {
      if (bar.intro < 1) bar.intro = Math.min(1, Math.max(0, (elapsed - bar.introDelay) / 1.5));
      const arrive = easeOutExpo(bar.intro);
      bar.lift += ((hovered === i ? 1 : 0) - bar.lift) * Math.min(1, dt * 8);
      bar.spin += (bar.spinTarget - bar.spin) * Math.min(1, dt * 4.5);
      const float = Math.sin(elapsed * 0.9 + i * 1.3) * 0.06 * motion;
      bar.mesh.position.set(
        bar.home.x + bar.scatter.x * scrollEased,
        bar.home.y + float + bar.scatter.y * scrollEased + (1 - arrive) * -1.2,
        bar.home.z + bar.lift * 0.42 + bar.scatter.z * scrollEased + (1 - arrive) * -6,
      );
      bar.mesh.rotation.set((1 - arrive) * 1.4, bar.spin + (1 - arrive) * -0.8, 0);
      bar.mesh.scale.setScalar(UNIT * (1 + bar.lift * 0.05) * (0.6 + 0.4 * arrive));
      bar.material.emissiveIntensity = bar.lift * 0.22;
    });

    // motes orbit
    moteData.forEach((m, i) => {
      m.angle += m.speed * dt * (0.4 + motion * 0.6);
      const r = m.radius + Math.sin(elapsed * 0.6 + m.wobble) * 0.12 + scrollEased * 1.2;
      dummy.position.set(Math.cos(m.angle) * r, Math.sin(m.angle) * r, m.height + Math.sin(elapsed + m.wobble) * 0.08);
      dummy.rotation.set(elapsed * 0.6 + i, elapsed * 0.4, 0);
      dummy.scale.setScalar(m.size);
      dummy.updateMatrix();
      motes.setMatrixAt(i, dummy.matrix);
    });
    motes.instanceMatrix.needsUpdate = true;
    band.rotation.z = -0.32 + elapsed * 0.03 * motion;
    rings.forEach((ring, i) => (ring.rotation.z = elapsed * (i ? -0.02 : 0.015) * motion));

    // lights sweep the gradient across the slabs
    lights.forEach(({ light, phase }, i) => {
      const a = elapsed * 0.35 * motion + phase;
      light.position.set(Math.cos(a) * 4.2, Math.sin(a * 0.8 + i) * 2.4, 3.2 + Math.sin(a) * 1.2);
    });

    dust.rotation.y = elapsed * 0.01 * motion + tilt.x * 0.05;
    dust.rotation.x = tilt.y * 0.03;

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
      geometries.forEach((g) => g.dispose());
      bars.forEach((b) => b.material.dispose());
      moteGeometry.dispose();
      moteMaterial.dispose();
      motes.dispose();
      rings.forEach((r) => r.geometry.dispose());
      ringMaterial.dispose();
      dustGeometry.dispose();
      dustMaterial.dispose();
      envTexture.dispose();
      pmrem.dispose();
      renderer.dispose();
    },
  };
}
