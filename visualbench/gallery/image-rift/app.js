(() => {
  "use strict";

  const canvas = document.querySelector("#canvas");
  const fallback = document.querySelector("#fallback");
  const fallbackImage = document.querySelector("#fallbackImage");
  const params = new URLSearchParams(location.search);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const modes = ["prism", "relief", "mercury", "halftone"];
  let gl;

  try {
    gl = canvas.getContext("webgl", { antialias: false, preserveDrawingBuffer: true, alpha: false });
    if (!gl) throw new Error("WebGL is not supported");
  } catch (error) {
    canvas.hidden = true;
    fallbackImage.hidden = false;
    fallback.hidden = false;
    document.body.dataset.ready = "fallback";
    window.__VISUALBENCH_READY__ = true;
    console.error(error);
    return;
  }

  const vertexSource = `
    attribute vec2 aPosition;
    varying vec2 vUv;
    void main() {
      vUv = aPosition * .5 + .5;
      gl_Position = vec4(aPosition, 0.0, 1.0);
    }
  `;

  const fragmentSource = `
    precision highp float;
    varying vec2 vUv;
    uniform sampler2D uImage;
    uniform vec2 uResolution;
    uniform vec2 uImageSize;
    uniform vec2 uPointer;
    uniform float uTime;
    uniform float uIntensity;
    uniform float uDetail;
    uniform float uHue;
    uniform float uSeed;
    uniform int uMode;

    #define PI 3.14159265359

    float hash(vec2 p) {
      p += uSeed;
      return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
    }

    vec3 hueRotate(vec3 color, float angle) {
      vec3 k = normalize(vec3(1.0));
      return color * cos(angle) + cross(k, color) * sin(angle) + k * dot(k, color) * (1.0 - cos(angle));
    }

    vec2 imageUv(vec2 screenUv) {
      vec2 p = screenUv - .5;
      float screenAspect = uResolution.x / uResolution.y;
      float imageAspect = uImageSize.x / uImageSize.y;
      if (screenAspect > imageAspect) p.x *= screenAspect / imageAspect;
      else p.y *= imageAspect / screenAspect;
      return p + .5;
    }

    float inside(vec2 uv) {
      vec2 bounds = step(vec2(0.0), uv) * step(uv, vec2(1.0));
      return bounds.x * bounds.y;
    }

    vec4 source(vec2 uv) {
      float mask = inside(uv);
      vec4 texel = texture2D(uImage, clamp(uv, .001, .999));
      return texel * mask;
    }

    float luminance(vec3 color) { return dot(color, vec3(.299, .587, .114)); }

    vec3 backdrop(vec2 uv) {
      float halo = exp(-3.2 * length(uv - vec2(.5, .48)));
      float grain = hash(floor(gl_FragCoord.xy * .5)) - .5;
      return vec3(.018, .019, .019) + vec3(.035, .04, .037) * halo + grain * .008;
    }

    vec4 prism(vec2 uv) {
      vec2 p = uv - .5;
      float radius = length(p);
      float angle = atan(p.y, p.x);
      float bands = sin(angle * (5.0 + uDetail * 4.0) + radius * 25.0 - uTime * 1.4);
      vec2 cursor = (uPointer - .5) * vec2(1.0, -1.0);
      vec2 warp = normalize(p + .0001) * bands * .018 * uIntensity;
      warp += cursor * exp(-9.0 * length(p - cursor * .35)) * .035 * uIntensity;
      float split = (.004 + radius * .018) * uIntensity;
      vec4 center = source(uv + warp);
      float r = source(uv + warp + vec2(split, -split * .35)).r;
      float g = center.g;
      float b = source(uv + warp - vec2(split, -split * .35)).b;
      vec3 color = hueRotate(vec3(r, g, b), uHue);
      color += vec3(.16, .02, .22) * max(bands, 0.0) * .25 * uIntensity;
      color *= .93 + .07 * sin(gl_FragCoord.y * 1.6);
      vec2 splitVector = vec2(split, -split * .35);
      return vec4(color, max(center.a, max(source(uv + warp + splitVector).a, source(uv + warp - splitVector).a)));
    }

    vec4 relief(vec2 uv) {
      vec2 texel = 1.0 / uImageSize * (1.0 + uDetail * 2.2);
      float tl = luminance(source(uv + texel * vec2(-1., 1.)).rgb);
      float tc = luminance(source(uv + texel * vec2( 0., 1.)).rgb);
      float tr = luminance(source(uv + texel * vec2( 1., 1.)).rgb);
      float ml = luminance(source(uv + texel * vec2(-1., 0.)).rgb);
      float mr = luminance(source(uv + texel * vec2( 1., 0.)).rgb);
      float bl = luminance(source(uv + texel * vec2(-1.,-1.)).rgb);
      float bc = luminance(source(uv + texel * vec2( 0.,-1.)).rgb);
      float br = luminance(source(uv + texel * vec2( 1.,-1.)).rgb);
      vec2 sobel = vec2(-tl - 2.0*ml - bl + tr + 2.0*mr + br, tl + 2.0*tc + tr - bl - 2.0*bc - br);
      float edge = length(sobel);
      vec4 base = source(uv);
      float value = luminance(base.rgb);
      float levels = mix(4.0, 13.0, uDetail);
      float contour = smoothstep(.42, .53, fract(value * levels + uTime * .08 * uIntensity));
      vec3 low = hueRotate(vec3(.025, .07, .11), uHue);
      vec3 high = hueRotate(vec3(.78, 1.0, .20), uHue);
      vec3 color = mix(low, high, floor(value * levels) / levels);
      color = mix(color, vec3(.94, .97, .87), contour * .2);
      color = mix(color, vec3(.015), smoothstep(.08, .42, edge) * uIntensity);
      color += vec3(.34, .58, 1.0) * smoothstep(.3, 1.15, edge) * .35;
      return vec4(color, max(base.a, smoothstep(.03, .15, edge)));
    }

    vec4 mercury(vec2 uv) {
      vec2 p = uv - .5;
      vec2 wave = vec2(
        sin(p.y * (12.0 + uDetail * 16.0) + uTime * 1.1),
        cos(p.x * (11.0 + uDetail * 13.0) - uTime * .83)
      );
      wave += sin((p.yx + wave * .06) * 31.0 - uTime) * .35;
      vec2 pointerPull = (uPointer - .5 - p) * exp(-8.0 * length(p - (uPointer - .5)));
      vec2 warped = uv + wave * .018 * uIntensity + pointerPull * .13 * uIntensity;
      vec4 base = source(warped);
      float value = luminance(base.rgb);
      float ridges = .5 + .5 * sin(value * (18.0 + uDetail * 20.0) + wave.x * 3.0);
      float sharp = pow(ridges, 4.0);
      vec3 steel = mix(vec3(.025, .035, .06), vec3(.54, .63, .72), ridges);
      steel += vec3(1.0, .95, .73) * sharp * 1.2;
      steel *= .35 + value * 1.25;
      steel = hueRotate(steel, uHue * .42);
      return vec4(steel, base.a);
    }

    vec4 halftone(vec2 uv) {
      vec4 base = source(uv);
      float value = luminance(base.rgb);
      float cells = mix(42.0, 115.0, uDetail);
      vec2 grid = fract((uv + vec2(uTime * .001 * uIntensity, 0.0)) * cells) - .5;
      float radius = mix(.44, .08, value);
      float dotMask = 1.0 - smoothstep(radius, radius + .055, length(grid));
      vec3 paper = vec3(.91, .88, .78);
      vec3 inkA = hueRotate(vec3(.13, .02, .24), uHue);
      vec3 inkB = hueRotate(vec3(.95, .16, .25), uHue);
      float stripe = step(.5, fract((uv.x + uv.y) * 8.0));
      vec3 ink = mix(inkA, inkB, stripe * uIntensity);
      vec3 color = mix(paper, ink, dotMask * base.a);
      float registration = smoothstep(.48, .5, abs(sin((uv.x - uv.y) * 18.0 + uTime * .1))) * .035;
      color -= registration * uIntensity;
      return vec4(color, base.a);
    }

    void main() {
      vec2 uv = imageUv(vUv);
      vec4 effect;
      if (uMode == 0) effect = prism(uv);
      else if (uMode == 1) effect = relief(uv);
      else if (uMode == 2) effect = mercury(uv);
      else effect = halftone(uv);
      vec3 bg = backdrop(vUv);
      gl_FragColor = vec4(mix(bg, effect.rgb, clamp(effect.a, 0.0, 1.0)), 1.0);
    }
  `;

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(message);
    }
    return shader;
  }

  let program;
  try {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexSource));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
  } catch (error) {
    canvas.hidden = true;
    fallbackImage.hidden = false;
    fallback.hidden = false;
    document.body.dataset.ready = "fallback";
    window.__VISUALBENCH_READY__ = true;
    console.error(error);
    return;
  }

  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

  const uniforms = {};
  ["uImage", "uResolution", "uImageSize", "uPointer", "uTime", "uIntensity", "uDetail", "uHue", "uSeed", "uMode"].forEach(name => {
    uniforms[name] = gl.getUniformLocation(program, name);
  });

  const texture = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.uniform1i(uniforms.uImage, 0);

  const state = {
    mode: Math.max(0, modes.indexOf(params.get("mode"))),
    intensity: 0.72,
    detail: 0.56,
    hue: 18,
    time: Number.parseFloat(params.get("time") || "0") || 0,
    running: params.get("motion") !== "0" && !reducedMotion,
    pointer: [0.5, 0.5],
    pointerTarget: [0.5, 0.5],
    imageSize: [512, 512]
  };
  let previous = performance.now();
  let currentObjectUrl;
  let toastTimer;

  const numberParam = (name, fallbackValue, min, max) => {
    const value = Number.parseFloat(params.get(name));
    return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallbackValue;
  };
  state.intensity = numberParam("intensity", 72, 0, 100) / 100;
  state.detail = numberParam("detail", 56, 0, 100) / 100;
  state.hue = numberParam("hue", 18, -180, 180);

  const controls = {
    intensity: document.querySelector("#intensity"),
    detail: document.querySelector("#detail"),
    hue: document.querySelector("#hue")
  };

  function updateControls() {
    controls.intensity.value = Math.round(state.intensity * 100);
    controls.detail.value = Math.round(state.detail * 100);
    controls.hue.value = Math.round(state.hue);
    document.querySelector("#intensityValue").value = controls.intensity.value;
    document.querySelector("#detailValue").value = controls.detail.value;
    document.querySelector("#hueValue").value = `${controls.hue.value}°`;
  }

  function selectMode(index) {
    state.mode = Math.max(0, Math.min(modes.length - 1, index));
    document.querySelectorAll("[data-mode]").forEach((button, buttonIndex) => {
      button.classList.toggle("active", buttonIndex === state.mode);
      button.setAttribute("aria-pressed", String(buttonIndex === state.mode));
    });
  }

  function showToast(message) {
    const toast = document.querySelector("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 2200);
  }

  function resize() {
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
      document.querySelector("#resolution").textContent = `${width} × ${height}`;
    }
  }

  function uploadImage(image, name, type) {
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    state.imageSize = [image.naturalWidth || image.width, image.naturalHeight || image.height];
    document.querySelector("#fileName").textContent = name;
    document.querySelector("#fileMeta").textContent = `${state.imageSize[0]} × ${state.imageSize[1]} / ${type.replace("image/", "").toUpperCase()}`;
    document.querySelector("#thumbnail").src = image.src;
    fallbackImage.src = image.src;
  }

  function loadUrl(url, name, type = "image/webp", revoke = false) {
    const image = new Image();
    image.onload = () => {
      uploadImage(image, name, type);
      if (currentObjectUrl && currentObjectUrl !== url) URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = revoke ? url : null;
      showToast(`${name.toUpperCase()} LOADED`);
      markReady();
    };
    image.onerror = () => {
      if (revoke) URL.revokeObjectURL(url);
      showToast("IMAGE COULD NOT BE DECODED");
    };
    image.src = url;
  }

  function loadFile(file) {
    if (!file || !file.type.startsWith("image/")) {
      showToast("CHOOSE A PNG, JPEG, WEBP, OR GIF");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      showToast("IMAGE MUST BE SMALLER THAN 25 MB");
      return;
    }
    loadUrl(URL.createObjectURL(file), file.name, file.type, true);
  }

  Object.entries(controls).forEach(([name, control]) => control.addEventListener("input", () => {
    const value = Number(control.value);
    state[name] = name === "hue" ? value : value / 100;
    updateControls();
  }));

  document.querySelectorAll("[data-mode]").forEach((button, index) => button.addEventListener("click", () => selectMode(index)));
  document.querySelector("#fileInput").addEventListener("change", event => loadFile(event.target.files[0]));

  const viewport = document.querySelector(".viewport");
  viewport.addEventListener("pointermove", event => {
    const bounds = viewport.getBoundingClientRect();
    state.pointerTarget = [(event.clientX - bounds.left) / bounds.width, 1 - (event.clientY - bounds.top) / bounds.height];
  });
  viewport.addEventListener("pointerleave", () => { state.pointerTarget = [.5, .5]; });

  const dropTarget = document.querySelector("#dropTarget");
  ["dragenter", "dragover"].forEach(type => addEventListener(type, event => {
    event.preventDefault();
    dropTarget.classList.add("visible");
  }));
  ["dragleave", "drop"].forEach(type => addEventListener(type, event => {
    event.preventDefault();
    if (type === "drop") loadFile(event.dataTransfer.files[0]);
    dropTarget.classList.remove("visible");
  }));

  const pauseButton = document.querySelector("#pauseButton");
  function updatePause() {
    pauseButton.setAttribute("aria-pressed", String(!state.running));
    pauseButton.querySelector("span").textContent = state.running ? "Ⅱ" : "▶";
    pauseButton.querySelector("b").textContent = state.running ? "PAUSE" : "PLAY";
  }
  pauseButton.addEventListener("click", () => { state.running = !state.running; updatePause(); });

  document.querySelector("#resetButton").addEventListener("click", () => {
    state.intensity = .72;
    state.detail = .56;
    state.hue = 18;
    state.pointerTarget = [.5, .5];
    updateControls();
    showToast("PARAMETERS RESET");
  });

  document.querySelector("#exportButton").addEventListener("click", () => {
    render(performance.now(), false);
    const link = document.createElement("a");
    link.download = `image-rift-${modes[state.mode]}.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
    showToast("PNG EXPORTED");
  });

  addEventListener("keydown", event => {
    if (event.target.matches("input")) return;
    if (/^[1-4]$/.test(event.key)) selectMode(Number(event.key) - 1);
    if (event.code === "Space") {
      event.preventDefault();
      state.running = !state.running;
      updatePause();
    }
  });

  addEventListener("resize", resize);
  canvas.addEventListener("webglcontextlost", event => {
    event.preventDefault();
    fallback.hidden = false;
  });

  function render(now, schedule = true) {
    resize();
    const delta = Math.min((now - previous) / 1000, .05);
    previous = now;
    if (state.running) state.time += delta;
    state.pointer[0] += (state.pointerTarget[0] - state.pointer[0]) * .08;
    state.pointer[1] += (state.pointerTarget[1] - state.pointer[1]) * .08;
    gl.uniform2f(uniforms.uResolution, canvas.width, canvas.height);
    gl.uniform2f(uniforms.uImageSize, state.imageSize[0], state.imageSize[1]);
    gl.uniform2f(uniforms.uPointer, state.pointer[0], state.pointer[1]);
    gl.uniform1f(uniforms.uTime, state.time);
    gl.uniform1f(uniforms.uIntensity, state.intensity);
    gl.uniform1f(uniforms.uDetail, state.detail);
    gl.uniform1f(uniforms.uHue, state.hue * Math.PI / 180);
    gl.uniform1f(uniforms.uSeed, numberParam("seed", 7, -10000, 10000));
    gl.uniform1i(uniforms.uMode, state.mode);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    if (schedule) requestAnimationFrame(render);
  }

  function markReady() {
    if (window.__VISUALBENCH_READY__) return;
    render(performance.now(), false);
    document.body.dataset.ready = "true";
    window.__VISUALBENCH_READY__ = true;
    window.dispatchEvent(new Event("visualbench-ready"));
  }

  selectMode(state.mode);
  updateControls();
  updatePause();
  loadUrl("assets/mascot.webp", "mascot.webp");
  requestAnimationFrame(render);
})();
