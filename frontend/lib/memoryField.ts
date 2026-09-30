import * as THREE from "three";

/* The landing scene. A breathing core of memory points hangs over a still
   particle surface; every few seconds a memory detaches from the core, falls,
   and lands as a ripple. The visitor's pointer writes ripples too. Everything
   is points and lines drawn by two small shaders, so it stays light enough to
   run behind text on a laptop GPU.

   Palette values are sRGB and go to the shaders untouched — the materials
   write gl_FragColor directly, so passing THREE.Color (linear) would darken
   every hue. */

const DEEP_2 = [0x02 / 255, 0x49 / 255, 0x50 / 255];
const TEAL = [0x0f / 255, 0xa4 / 255, 0xaf / 255];
const MIST = [0xaf / 255, 0xdd / 255, 0xe5 / 255];
const RUST = [0xc4 / 255, 0x6a / 255, 0x52 / 255]; // #964734 lifted so it reads as light on #003135

const MAX_RIPPLES = 10;
const MAX_DROPS = 6;

const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`;

const SURFACE_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
uniform vec4 uRipples[${MAX_RIPPLES}];
uniform vec2 uCore;
attribute float aSeed;
varying float vEnergy;
varying float vFade;
varying float vGlow;
${NOISE}
void main(){
  vec3 p = position;
  float h = sin(p.x*0.42 + uTime*0.32)*0.05 + sin(p.z*0.55 - uTime*0.26 + p.x*0.2)*0.045;
  h += snoise(vec3(p.x*0.16, p.z*0.16, uTime*0.05))*0.13;
  float e = 0.0;
  for (int i = 0; i < ${MAX_RIPPLES}; i++) {
    vec4 r = uRipples[i];
    float t = uTime - r.z;
    if (t < 0.0 || t > 10.0) continue;
    float d = distance(p.xz, r.xy);
    float x = d - t*1.8;
    float env = exp(-x*x*1.2) * exp(-t*0.38) * r.w / (1.0 + d*0.32);
    h += sin(x*4.6) * env * 0.42;
    e += env;
  }
  p.y += h;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float depth = -mv.z;
  gl_PointSize = (1.5 + e*2.4 + aSeed*0.7) * uPixelRatio * (12.0 / depth);
  vEnergy = clamp(e*1.1 + h*1.4, 0.0, 1.6);
  vFade = smoothstep(30.0, 8.0, depth) * smoothstep(19.0, 12.0, abs(p.x));
  vGlow = exp(-distance(p.xz, uCore)*0.3);
}
`;

const SURFACE_FRAGMENT = /* glsl */ `
uniform vec3 uDeep;
uniform vec3 uTeal;
uniform vec3 uMist;
varying float vEnergy;
varying float vFade;
varying float vGlow;
void main(){
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float a = smoothstep(0.5, 0.05, d);
  vec3 col = mix(uDeep, uTeal, clamp(0.3 + vEnergy*0.9 + vGlow*0.45, 0.0, 1.0));
  col = mix(col, uMist, clamp(vEnergy*0.7 - 0.15, 0.0, 1.0));
  gl_FragColor = vec4(col, a * vFade * (0.34 + vEnergy*0.6 + vGlow*0.4));
}
`;

/* The core and its reflection share one vertex shader; MIRROR flips it across
   the water line and lets the surface bend it. */
const CORE_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
attribute float aSeed;
attribute float aKind;
varying float vKind;
varying float vLight;
varying float vAlpha;
${NOISE}
void main(){
  vec3 n = normalize(position);
  float disp = snoise(n*1.5 + vec3(0.0, uTime*0.11, 0.0))*0.17 + snoise(n*3.4 - uTime*0.07)*0.05;
  vec3 p = position + n*disp;
  vec4 world = modelMatrix * vec4(p, 1.0);
  vec3 wn = normalize(mat3(modelMatrix) * n);
  vec3 toCam = normalize(cameraPosition - world.xyz);
  float facing = dot(wn, toCam);
  float mirrorFade = 1.0;
  #ifdef MIRROR
    world.y = -world.y;
    world.x += sin(world.y*5.0 + uTime*1.1 + world.z)*0.06;
    mirrorFade = 0.16 * smoothstep(-4.2, -0.4, world.y);
  #endif
  vec4 mv = viewMatrix * world;
  gl_Position = projectionMatrix * mv;
  float pulse = aKind > 0.5 ? 0.65 + 0.35*sin(uTime*1.5 + aSeed*25.0) : 1.0;
  float size = aKind > 0.5 ? 5.2 : 1.9 + aSeed*1.7;
  gl_PointSize = size * pulse * uPixelRatio * (12.0 / -mv.z);
  vLight = clamp(0.25 + 0.75*pow(1.0 - abs(facing), 1.4) + disp*1.8, 0.0, 1.3);
  vAlpha = (0.28 + 0.72*smoothstep(-0.7, 0.5, facing)) * mirrorFade;
  vKind = aKind;
}
`;

const CORE_FRAGMENT = /* glsl */ `
uniform vec3 uTeal;
uniform vec3 uMist;
uniform vec3 uRust;
varying float vKind;
varying float vLight;
varying float vAlpha;
void main(){
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float core = smoothstep(0.5, 0.0, d);
  vec3 col = vKind > 0.5 ? mix(uRust, vec3(1.0, 0.86, 0.8), core*0.35) : mix(uTeal, uMist, clamp(vLight, 0.0, 1.0));
  float a = vKind > 0.5 ? core*core*0.95 : core*(0.35 + 0.6*vLight);
  gl_FragColor = vec4(col, a * vAlpha);
}
`;

/* Edges between memories. Each segment carries a pulse that travels along it,
   the way a retrieval walks from one fact to the next. */
const LINK_VERTEX = /* glsl */ `
uniform float uTime;
attribute float aT;
attribute float aSeed;
varying float vT;
varying float vSeed;
${NOISE}
void main(){
  vec3 n = normalize(position);
  float disp = snoise(n*1.5 + vec3(0.0, uTime*0.11, 0.0))*0.17 + snoise(n*3.4 - uTime*0.07)*0.05;
  vec3 p = position + n*disp;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  vT = aT;
  vSeed = aSeed;
}
`;

const LINK_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform vec3 uTeal;
uniform vec3 uMist;
varying float vT;
varying float vSeed;
void main(){
  float head = fract(uTime*0.22 + vSeed);
  float pulse = exp(-pow((vT - head)*9.0, 2.0)) * step(0.55, fract(vSeed*7.31));
  vec3 col = mix(uTeal, uMist, pulse);
  gl_FragColor = vec4(col, 0.07 + pulse*0.75);
}
`;

const DUST_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
attribute float aSeed;
varying float vA;
void main(){
  vec3 p = position;
  p.y = mod(p.y + uTime*(0.05 + aSeed*0.08), 7.0) - 0.4;
  p.x += sin(uTime*0.2 + aSeed*30.0)*0.3;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (1.0 + aSeed*1.6) * uPixelRatio * (12.0 / -mv.z);
  vA = smoothstep(-0.4, 0.8, p.y) * smoothstep(6.6, 4.5, p.y) * smoothstep(28.0, 6.0, -mv.z);
}
`;

const DUST_FRAGMENT = /* glsl */ `
uniform vec3 uMist;
varying float vA;
void main(){
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  gl_FragColor = vec4(uMist, smoothstep(0.5, 0.0, d) * vA * 0.35);
}
`;

const SPARK_VERTEX = /* glsl */ `
uniform float uPixelRatio;
attribute float aSize;
attribute float aAlpha;
varying float vAlpha;
void main(){
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * uPixelRatio * (12.0 / -mv.z);
  vAlpha = aAlpha;
}
`;

const SPARK_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
varying float vAlpha;
void main(){
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float c = smoothstep(0.5, 0.0, d);
  gl_FragColor = vec4(mix(uColor, vec3(1.0), c*c*0.6), c * vAlpha);
}
`;

const TRAIL_VERTEX = /* glsl */ `
attribute float aAlpha;
varying float vAlpha;
void main(){
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  vAlpha = aAlpha;
}
`;

const TRAIL_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
varying float vAlpha;
void main(){ gl_FragColor = vec4(uColor, vAlpha); }
`;

type Drop = { active: boolean; from: THREE.Vector3; to: THREE.Vector3; start: number; duration: number };

export type MemoryFieldHandle = { dispose: () => void };

function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const vec3 = (c: number[]) => new THREE.Vector3(c[0], c[1], c[2]);

export function mountMemoryField(
  container: HTMLElement,
  interactionRoot: HTMLElement,
  options: { reducedMotion: boolean },
): MemoryFieldHandle {
  const rand = mulberry32(20260929);
  const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: "high-performance" });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.75);
  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(new THREE.Color("#003135"), 1);
  renderer.domElement.setAttribute("aria-hidden", "true");
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 80);
  const cameraBase = new THREE.Vector3(0, 2.3, 10.5);
  const lookTarget = new THREE.Vector3(0, 1.3, 0);

  const additive = {
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  } as const;

  /* ---------- surface ---------- */
  const cols = 240;
  const rows = 150;
  const surfacePositions = new Float32Array(cols * rows * 3);
  const surfaceSeeds = new Float32Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      const x = -19 + (c / (cols - 1)) * 38 + (rand() - 0.5) * 0.08;
      const z = -24 + (r / (rows - 1)) * 32 + (rand() - 0.5) * 0.08;
      surfacePositions[i * 3] = x;
      surfacePositions[i * 3 + 1] = 0;
      surfacePositions[i * 3 + 2] = z;
      surfaceSeeds[i] = rand();
    }
  }
  const surfaceGeometry = new THREE.BufferGeometry();
  surfaceGeometry.setAttribute("position", new THREE.BufferAttribute(surfacePositions, 3));
  surfaceGeometry.setAttribute("aSeed", new THREE.BufferAttribute(surfaceSeeds, 1));
  const ripples = Array.from({ length: MAX_RIPPLES }, () => new THREE.Vector4(0, 0, -1000, 0));
  let rippleCursor = 0;
  const surfaceMaterial = new THREE.ShaderMaterial({
    vertexShader: SURFACE_VERTEX,
    fragmentShader: SURFACE_FRAGMENT,
    uniforms: {
      uTime: { value: 0 },
      uPixelRatio: { value: pixelRatio },
      uRipples: { value: ripples },
      uCore: { value: new THREE.Vector2() },
      uDeep: { value: vec3(DEEP_2) },
      uTeal: { value: vec3(TEAL) },
      uMist: { value: vec3(MIST) },
    },
    ...additive,
  });
  const surface = new THREE.Points(surfaceGeometry, surfaceMaterial);
  surface.frustumCulled = false;
  scene.add(surface);

  /* ---------- core ---------- */
  const core = new THREE.Group();
  scene.add(core);
  const coreCount = 2400;
  const radius = 1.45;
  const corePositions = new Float32Array(coreCount * 3);
  const coreSeeds = new Float32Array(coreCount);
  const coreKinds = new Float32Array(coreCount);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < coreCount; i++) {
    const y = 1 - (i / (coreCount - 1)) * 2;
    const ring = Math.sqrt(1 - y * y);
    const theta = golden * i;
    const jitter = 1 + (rand() - 0.5) * 0.05;
    corePositions[i * 3] = Math.cos(theta) * ring * radius * jitter;
    corePositions[i * 3 + 1] = y * radius * jitter;
    corePositions[i * 3 + 2] = Math.sin(theta) * ring * radius * jitter;
    coreSeeds[i] = rand();
    coreKinds[i] = rand() < 0.035 ? 1 : 0;
  }
  const coreGeometry = new THREE.BufferGeometry();
  coreGeometry.setAttribute("position", new THREE.BufferAttribute(corePositions, 3));
  coreGeometry.setAttribute("aSeed", new THREE.BufferAttribute(coreSeeds, 1));
  coreGeometry.setAttribute("aKind", new THREE.BufferAttribute(coreKinds, 1));
  const coreUniforms = {
    uTime: { value: 0 },
    uPixelRatio: { value: pixelRatio },
    uTeal: { value: vec3(TEAL) },
    uMist: { value: vec3(MIST) },
    uRust: { value: vec3(RUST) },
  };
  const coreMaterial = new THREE.ShaderMaterial({ vertexShader: CORE_VERTEX, fragmentShader: CORE_FRAGMENT, uniforms: coreUniforms, ...additive });
  const mirrorMaterial = new THREE.ShaderMaterial({ vertexShader: CORE_VERTEX, fragmentShader: CORE_FRAGMENT, uniforms: coreUniforms, defines: { MIRROR: "" }, ...additive });
  const corePoints = new THREE.Points(coreGeometry, coreMaterial);
  const mirrorPoints = new THREE.Points(coreGeometry, mirrorMaterial);
  corePoints.frustumCulled = false;
  mirrorPoints.frustumCulled = false;
  core.add(corePoints, mirrorPoints);

  /* Links: a sparse subset of surface points, each tied to its nearest few. */
  const hubs: THREE.Vector3[] = [];
  for (let i = 0; i < 170; i++) {
    const idx = Math.floor(rand() * coreCount);
    hubs.push(new THREE.Vector3(corePositions[idx * 3], corePositions[idx * 3 + 1], corePositions[idx * 3 + 2]));
  }
  const linkPositions: number[] = [];
  const linkT: number[] = [];
  const linkSeeds: number[] = [];
  hubs.forEach((a, i) => {
    const nearest = hubs
      .map((b, j) => ({ j, d: a.distanceToSquared(b) }))
      .filter(({ j }) => j > i)
      .sort((x, y) => x.d - y.d)
      .slice(0, 2);
    nearest.forEach(({ j, d }) => {
      if (d > 0.55) return;
      const b = hubs[j];
      const seed = rand();
      /* Subdivide so the shared displacement bends each edge with the surface. */
      const steps = 6;
      for (let s = 0; s < steps; s++) {
        const t0 = s / steps;
        const t1 = (s + 1) / steps;
        const p0 = a.clone().lerp(b, t0).setLength(radius);
        const p1 = a.clone().lerp(b, t1).setLength(radius);
        linkPositions.push(p0.x, p0.y, p0.z, p1.x, p1.y, p1.z);
        linkT.push(t0, t1);
        linkSeeds.push(seed, seed);
      }
    });
  });
  const linkGeometry = new THREE.BufferGeometry();
  linkGeometry.setAttribute("position", new THREE.Float32BufferAttribute(linkPositions, 3));
  linkGeometry.setAttribute("aT", new THREE.Float32BufferAttribute(linkT, 1));
  linkGeometry.setAttribute("aSeed", new THREE.Float32BufferAttribute(linkSeeds, 1));
  const linkMaterial = new THREE.ShaderMaterial({
    vertexShader: LINK_VERTEX,
    fragmentShader: LINK_FRAGMENT,
    uniforms: { uTime: coreUniforms.uTime, uTeal: coreUniforms.uTeal, uMist: coreUniforms.uMist },
    ...additive,
  });
  const links = new THREE.LineSegments(linkGeometry, linkMaterial);
  links.frustumCulled = false;
  core.add(links);

  /* Orbits: the sources memory is read from, circling the core. */
  const orbitGroup = new THREE.Group();
  core.add(orbitGroup);
  const orbitSpecs = [
    { r: 2.25, tilt: 0.42, yaw: 0.2, speed: 0.16 },
    { r: 2.7, tilt: -0.28, yaw: 1.3, speed: -0.11 },
    { r: 3.15, tilt: 0.12, yaw: 2.4, speed: 0.08 },
  ];
  const orbitMatrices = orbitSpecs.map((o) => new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(Math.PI / 2 + o.tilt, o.yaw, 0)));
  const orbitPoints: number[] = [];
  orbitSpecs.forEach((o, k) => {
    for (let i = 0; i < 360; i++) {
      if (i % 3 === 2) continue;
      const a = (i / 360) * Math.PI * 2;
      const v = new THREE.Vector3(Math.cos(a) * o.r, Math.sin(a) * o.r, 0).applyMatrix4(orbitMatrices[k]);
      orbitPoints.push(v.x, v.y, v.z);
    }
  });
  const orbitGeometry = new THREE.BufferGeometry();
  orbitGeometry.setAttribute("position", new THREE.Float32BufferAttribute(orbitPoints, 3));
  const orbitCount = orbitPoints.length / 3;
  orbitGeometry.setAttribute("aSize", new THREE.Float32BufferAttribute(new Array(orbitCount).fill(1.1), 1));
  orbitGeometry.setAttribute("aAlpha", new THREE.Float32BufferAttribute(new Array(orbitCount).fill(0.16), 1));
  const orbitMaterial = new THREE.ShaderMaterial({
    vertexShader: SPARK_VERTEX,
    fragmentShader: SPARK_FRAGMENT,
    uniforms: { uPixelRatio: { value: pixelRatio }, uColor: { value: vec3(MIST) } },
    ...additive,
  });
  const orbits = new THREE.Points(orbitGeometry, orbitMaterial);
  orbits.frustumCulled = false;
  orbitGroup.add(orbits);

  /* ---------- sparks: satellites on the orbits and falling memories ---------- */
  const sparkCount = orbitSpecs.length + MAX_DROPS;
  const sparkPositions = new Float32Array(sparkCount * 3);
  const sparkSizes = new Float32Array(sparkCount);
  const sparkAlphas = new Float32Array(sparkCount);
  const sparkGeometry = new THREE.BufferGeometry();
  const sparkPositionAttr = new THREE.BufferAttribute(sparkPositions, 3);
  const sparkSizeAttr = new THREE.BufferAttribute(sparkSizes, 1);
  const sparkAlphaAttr = new THREE.BufferAttribute(sparkAlphas, 1);
  sparkGeometry.setAttribute("position", sparkPositionAttr);
  sparkGeometry.setAttribute("aSize", sparkSizeAttr);
  sparkGeometry.setAttribute("aAlpha", sparkAlphaAttr);
  const sparkMaterial = new THREE.ShaderMaterial({
    vertexShader: SPARK_VERTEX,
    fragmentShader: SPARK_FRAGMENT,
    uniforms: { uPixelRatio: { value: pixelRatio }, uColor: { value: vec3(MIST) } },
    ...additive,
  });
  const sparks = new THREE.Points(sparkGeometry, sparkMaterial);
  sparks.frustumCulled = false;
  scene.add(sparks);

  const trailPositions = new Float32Array(MAX_DROPS * 2 * 3);
  const trailAlphas = new Float32Array(MAX_DROPS * 2);
  const trailGeometry = new THREE.BufferGeometry();
  const trailPositionAttr = new THREE.BufferAttribute(trailPositions, 3);
  const trailAlphaAttr = new THREE.BufferAttribute(trailAlphas, 1);
  trailGeometry.setAttribute("position", trailPositionAttr);
  trailGeometry.setAttribute("aAlpha", trailAlphaAttr);
  const trailMaterial = new THREE.ShaderMaterial({
    vertexShader: TRAIL_VERTEX,
    fragmentShader: TRAIL_FRAGMENT,
    uniforms: { uColor: { value: vec3(MIST) } },
    ...additive,
  });
  const trails = new THREE.LineSegments(trailGeometry, trailMaterial);
  trails.frustumCulled = false;
  scene.add(trails);

  /* ---------- dust ---------- */
  const dustCount = 520;
  const dustPositions = new Float32Array(dustCount * 3);
  const dustSeeds = new Float32Array(dustCount);
  for (let i = 0; i < dustCount; i++) {
    dustPositions[i * 3] = (rand() - 0.5) * 30;
    dustPositions[i * 3 + 1] = rand() * 7;
    dustPositions[i * 3 + 2] = -20 + rand() * 26;
    dustSeeds[i] = rand();
  }
  const dustGeometry = new THREE.BufferGeometry();
  dustGeometry.setAttribute("position", new THREE.BufferAttribute(dustPositions, 3));
  dustGeometry.setAttribute("aSeed", new THREE.BufferAttribute(dustSeeds, 1));
  const dustMaterial = new THREE.ShaderMaterial({
    vertexShader: DUST_VERTEX,
    fragmentShader: DUST_FRAGMENT,
    uniforms: { uTime: coreUniforms.uTime, uPixelRatio: { value: pixelRatio }, uMist: coreUniforms.uMist },
    ...additive,
  });
  const dust = new THREE.Points(dustGeometry, dustMaterial);
  dust.frustumCulled = false;
  scene.add(dust);

  /* ---------- behaviour ---------- */
  let time = 0;
  const coreHome = new THREE.Vector3();
  const drops: Drop[] = Array.from({ length: MAX_DROPS }, () => ({
    active: false,
    from: new THREE.Vector3(),
    to: new THREE.Vector3(),
    start: 0,
    duration: 1,
  }));
  let nextDrop = 1.2;

  function addRipple(x: number, z: number, strength: number) {
    ripples[rippleCursor].set(x, z, time, strength);
    rippleCursor = (rippleCursor + 1) % MAX_RIPPLES;
  }

  function releaseMemory() {
    const drop = drops.find((d) => !d.active);
    if (!drop) return;
    const idx = Math.floor(rand() * coreCount);
    drop.from.set(corePositions[idx * 3], corePositions[idx * 3 + 1], corePositions[idx * 3 + 2]);
    if (drop.from.y > 0) drop.from.y *= -1; // leave from the underside
    drop.from.applyMatrix4(core.matrixWorld);
    drop.to.set(drop.from.x + (rand() - 0.5) * 3.2, 0, drop.from.z + (rand() - 0.2) * 3.4);
    drop.start = time;
    drop.duration = 1.5 + rand() * 0.6;
    drop.active = true;
  }

  function layout() {
    const width = container.clientWidth || 1;
    const height = container.clientHeight || 1;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    const wide = camera.aspect > 1.15;
    coreHome.set(wide ? 3.1 : 0, wide ? 2.2 : 4.1, wide ? -0.6 : -1.2);
    cameraBase.set(0, wide ? 3.0 : 3.1, wide ? 10.5 : 13.5);
    lookTarget.set(0, wide ? 0.9 : 1.6, 0);
    camera.fov = wide ? 40 : 46;
    camera.updateProjectionMatrix();
  }

  const pointer = new THREE.Vector2(0, 0);
  const pointerEased = new THREE.Vector2(0, 0);
  const raycaster = new THREE.Raycaster();
  const waterPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const hit = new THREE.Vector3();
  const lastRipple = new THREE.Vector3(999, 0, 999);
  let lastRippleAt = -10;
  let scrollProgress = 0;

  function toWater(event: PointerEvent) {
    const rect = container.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    return raycaster.ray.intersectPlane(waterPlane, hit) ? hit : null;
  }

  function onPointerMove(event: PointerEvent) {
    const rect = container.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, ((event.clientY - rect.top) / rect.height) * 2 - 1);
    if (event.pointerType !== "mouse") return;
    const point = toWater(event);
    if (point && time - lastRippleAt > 0.22 && point.distanceTo(lastRipple) > 1.1) {
      addRipple(point.x, point.z, 0.32);
      lastRipple.copy(point);
      lastRippleAt = time;
    }
  }

  function onPointerDown(event: PointerEvent) {
    if ((event.target as HTMLElement | null)?.closest("a,button,input,textarea,select,label,form")) return;
    const point = toWater(event);
    if (point) addRipple(point.x, point.z, 1.25);
    releaseMemory();
  }

  function onScroll() {
    const h = container.clientHeight || 1;
    scrollProgress = Math.min(1, Math.max(0, window.scrollY / h));
  }

  function update(dt: number) {
    time += dt;
    surfaceMaterial.uniforms.uTime.value = time;
    coreUniforms.uTime.value = time;

    pointerEased.lerp(pointer, Math.min(1, dt * 2.2));
    camera.position.set(
      cameraBase.x + pointerEased.x * 0.7,
      cameraBase.y - pointerEased.y * 0.35 + scrollProgress * 1.6,
      cameraBase.z - scrollProgress * 1.2,
    );
    camera.lookAt(lookTarget.x + pointerEased.x * 0.25, lookTarget.y + scrollProgress * 0.4, lookTarget.z);

    core.position.set(coreHome.x, coreHome.y + Math.sin(time * 0.5) * 0.12, coreHome.z);
    core.rotation.y = time * 0.07;
    core.rotation.z = Math.sin(time * 0.13) * 0.08;
    orbitGroup.rotation.y = time * 0.03;
    core.updateMatrixWorld();
    surfaceMaterial.uniforms.uCore.value.set(core.position.x, core.position.z);

    /* satellites */
    orbitSpecs.forEach((o, k) => {
      const a = time * o.speed * Math.PI * 2 * 0.25 + k * 2.1;
      const v = new THREE.Vector3(Math.cos(a) * o.r, Math.sin(a) * o.r, 0).applyMatrix4(orbitMatrices[k]).applyMatrix4(orbitGroup.matrixWorld);
      sparkPositions.set([v.x, v.y, v.z], k * 3);
      sparkSizes[k] = 4.6;
      sparkAlphas[k] = 0.9;
    });

    /* falling memories */
    if (time >= nextDrop) {
      releaseMemory();
      nextDrop = time + 2.1 + rand() * 1.5;
    }
    drops.forEach((drop, i) => {
      const s = orbitSpecs.length + i;
      if (!drop.active) {
        sparkAlphas[s] = 0;
        trailAlphas[i * 2] = trailAlphas[i * 2 + 1] = 0;
        return;
      }
      const t = (time - drop.start) / drop.duration;
      if (t >= 1) {
        drop.active = false;
        addRipple(drop.to.x, drop.to.z, 1.0);
        sparkAlphas[s] = 0;
        trailAlphas[i * 2] = trailAlphas[i * 2 + 1] = 0;
        return;
      }
      const at = (u: number) => {
        const e = u * u; // accelerate like a falling drop
        return new THREE.Vector3(
          THREE.MathUtils.lerp(drop.from.x, drop.to.x, u),
          THREE.MathUtils.lerp(drop.from.y, drop.to.y, e),
          THREE.MathUtils.lerp(drop.from.z, drop.to.z, u),
        );
      };
      const head = at(t);
      const tail = at(Math.max(0, t - 0.14));
      sparkPositions.set([head.x, head.y, head.z], s * 3);
      sparkSizes[s] = 5.5;
      sparkAlphas[s] = Math.min(1, t * 6);
      trailPositions.set([tail.x, tail.y, tail.z, head.x, head.y, head.z], i * 6);
      trailAlphas[i * 2] = 0;
      trailAlphas[i * 2 + 1] = 0.55 * Math.min(1, t * 6);
    });
    sparkPositionAttr.needsUpdate = true;
    sparkSizeAttr.needsUpdate = true;
    sparkAlphaAttr.needsUpdate = true;
    trailPositionAttr.needsUpdate = true;
    trailAlphaAttr.needsUpdate = true;
  }

  function render() {
    renderer.render(scene, camera);
  }

  layout();

  let frame = 0;
  let last = performance.now();
  let visible = true;
  const loop = (now: number) => {
    frame = requestAnimationFrame(loop);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!visible) return;
    update(dt);
    render();
  };

  const resizeObserver = new ResizeObserver(() => {
    layout();
    if (options.reducedMotion) render();
  });
  resizeObserver.observe(container);

  const intersection = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    last = performance.now();
  });
  intersection.observe(container);

  if (options.reducedMotion) {
    /* One composed still: a settled core with two rings spreading beneath it. */
    time = 6;
    core.position.copy(coreHome);
    core.updateMatrixWorld();
    addRipple(coreHome.x - 0.6, coreHome.z + 1.2, 1.0);
    ripples[0].z = 3.4;
    addRipple(coreHome.x + 1.2, coreHome.z - 0.4, 0.8);
    ripples[1].z = 4.6;
    update(0);
    render();
  } else {
    /* Seed the surface so it is alive on the first frame. */
    addRipple(coreHome.x - 0.8, coreHome.z + 1.4, 0.9);
    ripples[0].z = -1.6;
    addRipple(coreHome.x + 1.4, coreHome.z - 0.6, 0.7);
    ripples[1].z = -3.2;
    interactionRoot.addEventListener("pointermove", onPointerMove, { passive: true });
    interactionRoot.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    frame = requestAnimationFrame(loop);
  }

  return {
    dispose() {
      cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      intersection.disconnect();
      interactionRoot.removeEventListener("pointermove", onPointerMove);
      interactionRoot.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onScroll);
      [surfaceGeometry, coreGeometry, linkGeometry, orbitGeometry, sparkGeometry, trailGeometry, dustGeometry].forEach((g) => g.dispose());
      [surfaceMaterial, coreMaterial, mirrorMaterial, linkMaterial, orbitMaterial, sparkMaterial, trailMaterial, dustMaterial].forEach((m) => m.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
