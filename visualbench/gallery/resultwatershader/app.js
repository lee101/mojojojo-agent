import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const params = new URLSearchParams(location.search);
const host = document.querySelector("#scene");
const loading = document.querySelector("#loading");
const fallback = document.querySelector("#fallback");
const prefersReducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fixedTime = Number.parseFloat(params.get("time") ?? "0");
let running = params.get("motion") !== "0" && !prefersReducedMotion;
let elapsed = Number.isFinite(fixedTime) ? fixedTime : 0;
let previous = performance.now();
let audioContext;
let oceanGain;

const presets = {
  calm: { label: "Morning glass", swell: 0.42, chop: 0.25, light: 31 },
  open: { label: "Open water", swell: 1, chop: 0.65, light: 18 },
  storm: { label: "Rising squall", swell: 1.55, chop: 1.15, light: 7 }
};

const clampParam = (name, fallbackValue, min, max) => {
  const value = Number.parseFloat(params.get(name) ?? fallbackValue);
  return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallbackValue;
};

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
} catch (error) {
  loading.classList.add("done");
  fallback.hidden = false;
  console.error(error);
  throw error;
}

renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(host.clientWidth, host.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
host.append(renderer.domElement);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x0a2830, 0.009);

const camera = new THREE.PerspectiveCamera(43, host.clientWidth / host.clientHeight, 0.1, 400);
const defaultCamera = new THREE.Vector3(-1.5, 7.2, 17.5);
camera.position.copy(defaultCamera);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.045;
controls.enablePan = false;
controls.minDistance = 8;
controls.maxDistance = 31;
controls.minPolarAngle = Math.PI * 0.29;
controls.maxPolarAngle = Math.PI * 0.48;
controls.target.set(0, 0.4, -5);

const uniforms = {
  uTime: { value: elapsed },
  uSwell: { value: 1 },
  uChop: { value: 0.65 },
  uSun: { value: new THREE.Vector3() },
  uCamera: { value: camera.position }
};

const waterVertex = /* glsl */`
  uniform float uTime;
  uniform float uSwell;
  uniform float uChop;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vHeight;
  varying float vCrest;

  void addWave(in vec2 p, in vec2 dir, in float frequency, in float speed, in float amplitude,
               inout float height, inout vec2 slope, inout vec2 drift) {
    dir = normalize(dir);
    float phase = dot(p, dir) * frequency + uTime * speed;
    float wave = sin(phase);
    height += wave * amplitude;
    slope += cos(phase) * amplitude * frequency * dir;
    drift += cos(phase) * amplitude * dir;
  }

  void main() {
    vec3 p = position;
    float height = 0.0;
    vec2 slope = vec2(0.0);
    vec2 drift = vec2(0.0);
    addWave(p.xz, vec2(1.0, .22), .34, .74, .68, height, slope, drift);
    addWave(p.xz, vec2(.46, 1.0), .57, 1.08, .31, height, slope, drift);
    addWave(p.xz, vec2(-.82, .35), .91, 1.43, .16, height, slope, drift);
    addWave(p.xz, vec2(.24, -.96), 1.48, 1.87, .075, height, slope, drift);
    addWave(p.xz, vec2(.91, -.42), 2.24, 2.35, .032, height, slope, drift);
    addWave(p.xz, vec2(-.3, -1.0), 3.7, 3.1, .012, height, slope, drift);
    height *= uSwell;
    slope *= uSwell;
    p.xz += drift * uChop * .28;
    p.y += height;
    vHeight = height;
    vCrest = length(slope);
    vNormal = normalize(mat3(modelMatrix) * vec3(-slope.x, 1.0, -slope.y));
    vec4 world = modelMatrix * vec4(p, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const waterFragment = /* glsl */`
  precision highp float;
  uniform float uTime;
  uniform float uSwell;
  uniform float uChop;
  uniform vec3 uSun;
  uniform vec3 uCamera;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vHeight;
  varying float vCrest;

  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }

  void main() {
    vec3 N = normalize(vNormal);
    vec3 V = normalize(uCamera - vWorld);
    vec3 L = normalize(uSun);
    float fresnel = pow(1.0 - max(dot(N, V), 0.0), 4.2);
    float facing = clamp(N.y, 0.0, 1.0);
    vec3 deep = vec3(.008, .075, .105);
    vec3 jade = vec3(.035, .23, .255);
    vec3 horizon = vec3(.30, .48, .47);
    vec3 color = mix(deep, jade, facing * .74 + vHeight * .08);
    color = mix(color, horizon, fresnel * .74);

    vec3 H = normalize(L + V);
    float spec = pow(max(dot(N, H), 0.0), mix(115.0, 52.0, uChop / 1.4));
    float broad = pow(max(dot(reflect(-L, N), V), 0.0), 9.0);
    color += vec3(.68, .88, .77) * spec * 2.1;
    color += vec3(.20, .33, .28) * broad * .32;

    float breakup = hash(floor(vWorld.xz * 1.8 + uTime * .2));
    float foam = smoothstep(1.15, 2.25, vCrest + vHeight * .25 + breakup * .23);
    foam *= smoothstep(.55, 1.25, uSwell);
    color = mix(color, vec3(.68, .84, .76), foam * .42);

    float distanceFog = 1.0 - exp(-length(vWorld.xz - uCamera.xz) * .008);
    color = mix(color, vec3(.055, .16, .18), distanceFog * .48);
    gl_FragColor = vec4(color, 1.0);
  }
`;

const waterGeometry = new THREE.PlaneGeometry(180, 180, 260, 260);
waterGeometry.rotateX(-Math.PI / 2);
const water = new THREE.Mesh(waterGeometry, new THREE.ShaderMaterial({
  vertexShader: waterVertex,
  fragmentShader: waterFragment,
  uniforms
}));
water.position.y = -0.2;
scene.add(water);

const skyMaterial = new THREE.ShaderMaterial({
  side: THREE.BackSide,
  depthWrite: false,
  uniforms: { uSun: uniforms.uSun },
  vertexShader: `varying vec3 vDirection; void main(){ vDirection = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `
    precision highp float;
    varying vec3 vDirection;
    uniform vec3 uSun;
    void main() {
      float h = smoothstep(-.18, .72, vDirection.y);
      vec3 bottom = vec3(.035, .15, .18);
      vec3 top = vec3(.012, .038, .075);
      vec3 color = mix(bottom, top, h);
      float glow = pow(max(dot(normalize(vDirection), normalize(uSun)), 0.0), 38.0);
      float disc = smoothstep(.9993, .99965, dot(normalize(vDirection), normalize(uSun)));
      color += vec3(.45, .58, .47) * glow * .7 + vec3(.82, .95, .73) * disc;
      gl_FragColor = vec4(color, 1.0);
    }
  `
});
scene.add(new THREE.Mesh(new THREE.SphereGeometry(190, 32, 18), skyMaterial));

function setSun(degrees) {
  const altitude = THREE.MathUtils.degToRad(degrees);
  uniforms.uSun.value.set(Math.cos(altitude) * .72, Math.sin(altitude), -.62).normalize();
}

const fields = {
  swell: document.querySelector("#swell"),
  chop: document.querySelector("#chop"),
  light: document.querySelector("#light")
};

function updateFields() {
  uniforms.uSwell.value = Number(fields.swell.value);
  uniforms.uChop.value = Number(fields.chop.value);
  setSun(Number(fields.light.value));
  document.querySelector("#swellValue").value = Number(fields.swell.value).toFixed(2);
  document.querySelector("#chopValue").value = Number(fields.chop.value).toFixed(2);
  document.querySelector("#lightValue").value = `${fields.light.value}°`;
}

function applyPreset(name) {
  const preset = presets[name] || presets.open;
  fields.swell.value = clampParam("swell", preset.swell, .25, 1.8);
  fields.chop.value = clampParam("chop", preset.chop, 0, 1.4);
  fields.light.value = clampParam("light", preset.light, 2, 48);
  document.querySelector("#presetName").textContent = preset.label;
  document.querySelectorAll("[data-preset]").forEach(button => button.classList.toggle("active", button.dataset.preset === name));
  updateFields();
}

Object.values(fields).forEach(field => field.addEventListener("input", updateFields));
document.querySelectorAll("[data-preset]").forEach(button => button.addEventListener("click", () => applyPreset(button.dataset.preset)));

const pauseButton = document.querySelector("#pauseButton");
pauseButton.addEventListener("click", () => {
  running = !running;
  pauseButton.setAttribute("aria-pressed", String(!running));
  pauseButton.innerHTML = running ? '<span aria-hidden="true">Ⅱ</span> Pause' : '<span aria-hidden="true">▶</span> Drift';
});

document.querySelector("#resetButton").addEventListener("click", () => {
  camera.position.copy(defaultCamera);
  controls.target.set(0, .4, -5);
  controls.update();
});

document.querySelector("#soundToggle").addEventListener("click", async event => {
  const button = event.currentTarget;
  if (!audioContext) {
    audioContext = new AudioContext();
    const bufferSize = audioContext.sampleRate * 2;
    const buffer = audioContext.createBuffer(1, bufferSize, audioContext.sampleRate);
    const channel = buffer.getChannelData(0);
    let last = 0;
    for (let i = 0; i < bufferSize; i++) {
      last = last * .985 + (Math.random() * 2 - 1) * .015;
      channel[i] = last;
    }
    const source = audioContext.createBufferSource();
    const filter = audioContext.createBiquadFilter();
    oceanGain = audioContext.createGain();
    source.buffer = buffer;
    source.loop = true;
    filter.type = "lowpass";
    filter.frequency.value = 420;
    oceanGain.gain.value = 0;
    source.connect(filter).connect(oceanGain).connect(audioContext.destination);
    source.start();
  }
  await audioContext.resume();
  const enable = button.getAttribute("aria-pressed") !== "true";
  oceanGain.gain.setTargetAtTime(enable ? .16 : 0, audioContext.currentTime, .3);
  button.setAttribute("aria-pressed", String(enable));
  button.querySelector(".button-label").textContent = enable ? "SOUND ON" : "SOUND OFF";
});

function resize() {
  const width = host.clientWidth;
  const height = host.clientHeight;
  renderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);

function render(now) {
  const delta = Math.min((now - previous) / 1000, .05);
  previous = now;
  if (running) elapsed += delta;
  uniforms.uTime.value = elapsed;
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(render);
}

const initialPreset = presets[params.get("preset")] ? params.get("preset") : "open";
applyPreset(initialPreset);
pauseButton.setAttribute("aria-pressed", String(!running));
if (!running) pauseButton.innerHTML = '<span aria-hidden="true">▶</span> Drift';
renderer.compile(scene, camera);
renderer.render(scene, camera);
document.body.dataset.ready = "true";
window.__VISUALBENCH_READY__ = true;
window.dispatchEvent(new Event("visualbench-ready"));
requestAnimationFrame(() => loading.classList.add("done"));
requestAnimationFrame(render);
