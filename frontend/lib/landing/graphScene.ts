import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  IcosahedronGeometry,
  InstancedMesh,
  LineBasicMaterial,
  LineSegments,
  MathUtils,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  Object3D,
  PerspectiveCamera,
  PMREMGenerator,
  PointLight,
  Raycaster,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GRAPH_EDGES, GRAPH_NODES, type GraphGroup } from "@/lib/landing/graphData";

/* The memory graph: sources feed kinds of memory, memory feeds the core, the
   core briefs the agents. Pulses run along the edges in that direction. Drag to
   orbit (on a pointer device), hover or tap a node to light up what it touches.

   Labels are HTML, positioned by the caller from the projected points this
   module reports each frame, so text stays crisp and selectable. */

const GROUP_COLOR: Record<GraphGroup, string> = {
  core: "#f4f3fb",
  source: "#50a8fc",
  memory: "#a168fa",
  agent: "#fecb91",
};
const GROUP_SIZE: Record<GraphGroup, number> = { core: 0.42, source: 0.17, memory: 0.14, agent: 0.15 };

export type ProjectedLabel = { x: number; y: number; depth: number; visible: boolean };

export type GraphScene = {
  setActive: (active: boolean) => void;
  focus: (id: string | null) => void;
  dispose: () => void;
};

type Options = {
  reducedMotion: boolean;
  lowPower: boolean;
  touch: boolean;
  onFrame: (labels: ProjectedLabel[]) => void;
  onHover: (id: string | null) => void;
};

function layout(): Vector3[] {
  // sources on an upper-left shell, agents lower-right, memory in a ring round the core
  const sources = GRAPH_NODES.filter((n) => n.group === "source");
  const memory = GRAPH_NODES.filter((n) => n.group === "memory");
  const agents = GRAPH_NODES.filter((n) => n.group === "agent");
  const position = new Map<string, Vector3>();
  position.set("core", new Vector3(0, 0, 0));
  sources.forEach((n, i) => {
    const a = (i / sources.length) * Math.PI * 2;
    position.set(n.id, new Vector3(-3.4 + Math.sin(a) * 0.7, Math.cos(a) * 1.9, Math.sin(a) * 1.7));
  });
  memory.forEach((n, i) => {
    const a = (i / memory.length) * Math.PI * 2 + 0.3;
    position.set(n.id, new Vector3(Math.cos(a) * 1.55, Math.sin(a) * 1.55, Math.sin(a * 2) * 0.6));
  });
  agents.forEach((n, i) => {
    const a = (i / agents.length) * Math.PI * 2 + 0.6;
    position.set(n.id, new Vector3(3.4 + Math.sin(a) * 0.6, Math.cos(a) * 1.8, Math.sin(a) * 1.6));
  });
  return GRAPH_NODES.map((n) => position.get(n.id)!);
}

export function mountGraphScene(canvas: HTMLCanvasElement, opts: Options): GraphScene {
  const { reducedMotion, lowPower, touch, onFrame, onHover } = opts;
  const renderer = new WebGLRenderer({ canvas, antialias: !lowPower, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, lowPower ? 1.25 : 1.75));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.setClearColor(0x000000, 0);

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const envTexture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environment = envTexture;

  const camera = new PerspectiveCamera(36, 1, 0.1, 100);
  camera.position.set(0, 0.6, 11);

  const glowA = new PointLight("#a168fa", 30, 12, 1.5);
  const glowB = new PointLight("#50a8fc", 22, 12, 1.5);
  glowA.position.set(0, 0, 2);
  glowB.position.set(-4, 2, 3);
  scene.add(glowA, glowB);

  const world = new Group();
  scene.add(world);

  const homes = layout();
  const index = new Map(GRAPH_NODES.map((n, i) => [n.id, i]));
  const edges = GRAPH_EDGES.map(([a, b]) => [index.get(a)!, index.get(b)!] as const);
  const neighbours = GRAPH_NODES.map((_, i) => new Set<number>([i]));
  edges.forEach(([a, b]) => {
    neighbours[a].add(b);
    neighbours[b].add(a);
  });

  // ---- nodes: glossy spheres, the core a faceted crystal in a wire shell
  const sphere = new SphereGeometry(1, lowPower ? 20 : 32, lowPower ? 14 : 24);
  const crystal = new IcosahedronGeometry(1, 1);
  const nodeMaterials: MeshPhysicalMaterial[] = [];
  const nodes = GRAPH_NODES.map((node, i) => {
    const material = new MeshPhysicalMaterial({
      color: new Color(GROUP_COLOR[node.group]),
      roughness: node.group === "core" ? 0.08 : 0.22,
      metalness: 0.15,
      clearcoat: 1,
      clearcoatRoughness: 0.1,
      iridescence: node.group === "core" ? 1 : 0.4,
      iridescenceIOR: 1.5,
      emissive: new Color(GROUP_COLOR[node.group]),
      emissiveIntensity: 0.18,
      flatShading: node.group === "core",
      transparent: true,
    });
    nodeMaterials.push(material);
    const mesh = new Mesh(node.group === "core" ? crystal : sphere, material);
    mesh.scale.setScalar(GROUP_SIZE[node.group]);
    mesh.position.copy(homes[i]);
    mesh.userData.index = i;
    world.add(mesh);
    return { mesh, material, phase: Math.random() * Math.PI * 2, glow: 0 };
  });
  const shellGeometry = new IcosahedronGeometry(0.72, 1);
  const shellMaterial = new MeshBasicMaterial({ color: "#b8a6ff", wireframe: true, transparent: true, opacity: 0.22, depthWrite: false });
  const shell = new Mesh(shellGeometry, shellMaterial);
  world.add(shell);

  // ---- edges: one buffer, colour per vertex so it can be lit per edge
  const edgePositions = new Float32Array(edges.length * 6);
  const edgeColors = new Float32Array(edges.length * 6);
  const edgeGeometry = new BufferGeometry();
  edgeGeometry.setAttribute("position", new BufferAttribute(edgePositions, 3));
  edgeGeometry.setAttribute("color", new BufferAttribute(edgeColors, 3));
  const edgeMaterial = new LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false, blending: AdditiveBlending });
  world.add(new LineSegments(edgeGeometry, edgeMaterial));
  const dim = new Color("#463c78");
  const lit = new Color("#d9ccff");
  const edgeGlow = edges.map(() => 0);

  // ---- pulses travelling along edges, source → memory → core → agent
  const PULSES = lowPower ? 26 : 54;
  const pulseGeometry = new SphereGeometry(1, 8, 6);
  const pulseMaterial = new MeshBasicMaterial({ toneMapped: false, transparent: true, opacity: 0.95, blending: AdditiveBlending, depthWrite: false });
  const pulses = new InstancedMesh(pulseGeometry, pulseMaterial, PULSES);
  const pulseData = Array.from({ length: PULSES }, (_, i) => ({ edge: i % edges.length, t: Math.random(), speed: 0.25 + Math.random() * 0.35 }));
  const tmpColor = new Color();
  pulseData.forEach((p, i) => {
    tmpColor.set(GROUP_COLOR[GRAPH_NODES[edges[p.edge][0]].group]);
    pulses.setColorAt(i, tmpColor);
  });
  world.add(pulses);

  // ---- controls
  let controls: OrbitControls | null = null;
  if (!touch) {
    controls = new OrbitControls(camera, canvas);
    controls.enableZoom = false;
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.rotateSpeed = 0.6;
    controls.autoRotate = !reducedMotion;
    controls.autoRotateSpeed = 0.55;
    controls.minPolarAngle = Math.PI * 0.28;
    controls.maxPolarAngle = Math.PI * 0.72;
  }

  // ---- hover / tap
  const raycaster = new Raycaster();
  const ndc = new Vector2(2, 2);
  let hovered = -1;
  let focused = -1;
  // tell the caller which node leads: the hovered one, else the tapped one
  function report() {
    const lead = hovered >= 0 ? hovered : focused;
    onHover(lead >= 0 ? GRAPH_NODES[lead].id : null);
  }
  function setPointer(event: PointerEvent) {
    const rect = canvas.getBoundingClientRect();
    ndc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  }
  function onMove(event: PointerEvent) {
    if (event.pointerType === "mouse") setPointer(event);
  }
  function onLeave() {
    ndc.set(2, 2);
  }
  function onTap(event: PointerEvent) {
    if (event.pointerType === "mouse") return;
    setPointer(event);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(nodes.map((n) => n.mesh), false)[0];
    focused = hit ? (hit.object.userData.index as number) : -1;
    ndc.set(2, 2);
    report();
  }
  canvas.addEventListener("pointermove", onMove, { passive: true });
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("pointerup", onTap);

  // ---- sizing
  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // step back until the graph (~9 units wide, ~4.5 tall) fits either way
    const tan = Math.tan(MathUtils.degToRad(camera.fov / 2));
    const fit = Math.max(4.6 / (tan * camera.aspect), 2.6 / tan) + 2.2;
    camera.position.setLength(fit);
    camera.updateProjectionMatrix();
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  resize();

  // ---- loop
  const projected: ProjectedLabel[] = GRAPH_NODES.map(() => ({ x: 0, y: 0, depth: 0, visible: true }));
  const v = new Vector3();
  const viewSpace = new Vector3();
  const dummy = new Object3D();
  let frame = 0;
  let active = true;
  let last = performance.now();
  let elapsed = 0;

  function tick(now: number) {
    frame = requestAnimationFrame(tick);
    const dt = MathUtils.clamp((now - last) / 1000, 0, 1 / 20);
    last = now;
    elapsed += dt;
    const motion = reducedMotion ? 0 : 1;

    if (controls) {
      controls.autoRotate = !reducedMotion && hovered < 0;
      controls.update(dt);
    } else if (motion) {
      world.rotation.y += dt * 0.12;
    }

    // hover
    if (ndc.x <= 1) {
      raycaster.setFromCamera(ndc, camera);
      const hit = raycaster.intersectObjects(nodes.map((n) => n.mesh), false)[0];
      const next = hit ? (hit.object.userData.index as number) : -1;
      if (next !== hovered) {
        hovered = next;
        canvas.style.cursor = hovered >= 0 ? "pointer" : "grab";
        report();
      }
    } else if (hovered >= 0) {
      hovered = -1;
      report();
    }
    const lead = hovered >= 0 ? hovered : focused;

    // nodes drift and light up with their neighbourhood
    nodes.forEach((node, i) => {
      const near = lead < 0 || neighbours[lead].has(i);
      node.glow += ((lead === i ? 1 : 0) - node.glow) * Math.min(1, dt * 8);
      const drift = Math.sin(elapsed * 0.7 + node.phase) * 0.08 * motion;
      node.mesh.position.set(homes[i].x, homes[i].y + drift, homes[i].z + Math.cos(elapsed * 0.5 + node.phase) * 0.06 * motion);
      const size = GROUP_SIZE[GRAPH_NODES[i].group] * (1 + node.glow * 0.35);
      node.mesh.scale.setScalar(size);
      node.material.emissiveIntensity = 0.18 + node.glow * 0.9;
      node.material.opacity += ((near ? 1 : 0.22) - node.material.opacity) * Math.min(1, dt * 8);
    });
    nodes[0].mesh.rotation.set(elapsed * 0.2 * motion, elapsed * 0.3 * motion, 0);
    shell.rotation.set(-elapsed * 0.12 * motion, elapsed * 0.08 * motion, 0);

    // edges follow the nodes; lit when they touch the lead node
    edges.forEach(([a, b], e) => {
      const pa = nodes[a].mesh.position;
      const pb = nodes[b].mesh.position;
      edgePositions.set([pa.x, pa.y, pa.z, pb.x, pb.y, pb.z], e * 6);
      const target = lead >= 0 && (a === lead || b === lead) ? 1 : 0;
      edgeGlow[e] += (target - edgeGlow[e]) * Math.min(1, dt * 8);
      const faded = lead >= 0 && !target ? 0.35 : 1;
      tmpColor.copy(dim).lerp(lit, edgeGlow[e]).multiplyScalar(faded);
      edgeColors.set([tmpColor.r, tmpColor.g, tmpColor.b, tmpColor.r, tmpColor.g, tmpColor.b], e * 6);
    });
    edgeGeometry.attributes.position.needsUpdate = true;
    edgeGeometry.attributes.color.needsUpdate = true;
    edgeGeometry.computeBoundingSphere();

    // pulses
    pulseData.forEach((p, i) => {
      p.t += dt * p.speed * (0.35 + motion * 0.65);
      if (p.t >= 1) {
        p.t = 0;
        p.edge = Math.floor(Math.random() * edges.length);
        tmpColor.set(GROUP_COLOR[GRAPH_NODES[edges[p.edge][0]].group]);
        pulses.setColorAt(i, tmpColor);
        if (pulses.instanceColor) pulses.instanceColor.needsUpdate = true;
      }
      const [a, b] = edges[p.edge];
      dummy.position.copy(nodes[a].mesh.position).lerp(nodes[b].mesh.position, p.t);
      const onLead = lead >= 0 && (a === lead || b === lead);
      dummy.scale.setScalar((onLead ? 0.06 : 0.04) * Math.sin(p.t * Math.PI));
      dummy.updateMatrix();
      pulses.setMatrixAt(i, dummy.matrix);
    });
    pulses.instanceMatrix.needsUpdate = true;

    renderer.render(scene, camera);

    // project labels for the HTML overlay
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    nodes.forEach((node, i) => {
      node.mesh.getWorldPosition(v);
      const depth = viewSpace.copy(v).applyMatrix4(camera.matrixWorldInverse).z;
      v.project(camera);
      const p = projected[i];
      p.x = (v.x * 0.5 + 0.5) * w;
      p.y = (-v.y * 0.5 + 0.5) * h;
      p.depth = MathUtils.clamp(MathUtils.mapLinear(depth, -camera.position.length() - 3, -camera.position.length() + 3, 1, 0), 0, 1);
      p.visible = lead < 0 || neighbours[lead].has(i);
    });
    onFrame(projected);
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
    setActive(next) {
      active = next;
      if (next && !document.hidden) start();
      else stop();
    },
    focus(id) {
      focused = id ? index.get(id) ?? -1 : -1;
    },
    dispose() {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("pointerup", onTap);
      resizeObserver.disconnect();
      controls?.dispose();
      sphere.dispose();
      crystal.dispose();
      nodeMaterials.forEach((m) => m.dispose());
      shellGeometry.dispose();
      shellMaterial.dispose();
      edgeGeometry.dispose();
      edgeMaterial.dispose();
      pulseGeometry.dispose();
      pulseMaterial.dispose();
      pulses.dispose();
      envTexture.dispose();
      pmrem.dispose();
      renderer.dispose();
    },
  };
}
