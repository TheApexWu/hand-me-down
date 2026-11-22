// The live arm, drawn as ink on the screen's paper.
//
//   import { mountArm } from '/arm/arm.js';
//   const arm = mountArm(document.querySelector('#arm'));     // arm.dispose() takes it down
//
// It builds the SO-101 from scene.json (written by card/arm_scene.py), polls the pose the arm
// laptop posts, and turns each joint by its angle in degrees about the model's own axis, from
// the model's zero. three.js is vendored next to this file, so there is no build step and no
// network. The only words it ever shows are ARM OFFLINE.
//
// Options: sceneUrl (the scene.json beside this file, so /arm/scene.json), meshBase
// ('/sim/so101/assets/'), poseUrl ('/pose.json'), pollMs (100), staleMs (2000), and getPose,
// a function to ask for the pose instead of fetching it (demo.html uses it).
// Returns { ready, dispose, scene }: ready resolves true once the arm is drawn, false if it could not be.
import * as THREE from './vendor/three.module.min.js';
import { STLLoader } from './vendor/STLLoader.js';

const INK = '#1d1b18', MARK = '#c2402d';
const PART = '#fbf8f0', MOTOR = '#d6cebf', BOX = '#e7dcc6', CARD = '#fffefa';
const MM = 1000;            // scene.json is in metres; drawing in mm keeps three's edge finder exact on small parts
const CREASE_DEG = 32;      // only folds sharper than this get an ink line
const EASE_S = 0.12;        // how quickly the drawing closes on the latest pose
const OFFLINE_OPACITY = 0.35;
// One fixed three-quarter view, from the side the wrist camera is not on, in base-frame mm (z up).
// halfWidth and halfHeight are what must stay in the picture around the target, whatever the panel's shape.
const VIEW = { target: [213, 39, 150], azimuth: -62, elevation: 30, halfWidth: 250, halfHeight: 210, fov: 24 };

export function mountArm(container, options = {}) {
  const o = { sceneUrl: new URL('./scene.json', import.meta.url).href, meshBase: '/sim/so101/assets/',
              poseUrl: '/pose.json', pollMs: 100, staleMs: 2000, getPose: null, ...options };
  const still = window.matchMedia('(prefers-reduced-motion: reduce)');
  const fade = () => (still.matches ? 'none' : 'opacity .6s ease');

  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:relative;width:100%;height:100%;overflow:hidden';
  const label = document.createElement('div');
  label.textContent = 'ARM OFFLINE';
  label.style.cssText = `position:absolute;left:0;right:0;bottom:4%;text-align:center;text-indent:.22em;color:${INK};` +
    'font:600 clamp(10px,1.1vw,14px) ui-monospace,Menlo,monospace;letter-spacing:.22em;opacity:0;pointer-events:none';
  container.appendChild(wrap);
  if (container.isConnected && wrap.clientHeight < 2) wrap.style.cssText += ';height:auto;aspect-ratio:4/3';   // a container with no height of its own

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (err) {
    console.error('arm: no WebGL', err);
    label.style.opacity = 1;
    wrap.appendChild(label);
    return { ready: Promise.resolve(false), scene: null, dispose: () => wrap.remove() };
  }
  renderer.setClearColor(0x000000, 0);
  const canvas = renderer.domElement;
  canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;opacity:0';
  wrap.append(canvas, label);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(VIEW.fov, 1, 20, 6000);
  camera.up.set(0, 0, 1);   // the scene keeps MuJoCo's frame: x forward from the base, z up
  const sky = new THREE.HemisphereLight('#ffffff', '#cfc5b3', 2.5);
  sky.position.set(0, 0, 1);
  const sun = new THREE.DirectionalLight('#ffffff', 0.75);
  sun.position.set(-0.3, -0.7, 1);
  scene.add(sky, sun);

  const fills = new Map();
  const fill = (color) => fills.get(color) || fills.set(color, new THREE.MeshLambertMaterial({
    color, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })).get(color);
  const ink = new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.78 });
  const joints = new Map();   // name -> { hinge, axis, deg, posed }
  const want = {};            // name -> degrees, from the latest pose
  let held = {};              // joints the pose does not drive (the jaw), in degrees
  let dirty = true, built = false, disposed = false, attached = false;
  let raf = 0, timer = 0, pending = null, last = 0;
  let online = null, fresh = -Infinity, seenT = null;

  const quat = (q) => new THREE.Quaternion(q[1], q[2], q[3], q[0]);       // scene.json is w x y z
  const at = (v) => new THREE.Vector3(v[0], v[1], v[2]).multiplyScalar(MM);

  function drawn(geometry, color) {
    const g = new THREE.Group();
    g.add(new THREE.Mesh(geometry, fill(color)));
    if (!geometry.userData.edges) geometry.userData.edges = new THREE.EdgesGeometry(geometry, CREASE_DEG);
    g.add(new THREE.LineSegments(geometry.userData.edges, ink));
    return g;
  }

  function shape(g) {         // MuJoCo sizes are half-extents; its cylinders run along z
    const s = g.size.map((v) => v * MM);
    if (g.shape === 'box') return new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]);
    if (g.shape === 'cylinder') return new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 32).rotateX(Math.PI / 2);
    return new THREE.SphereGeometry(s[0], 24, 16);
  }

  function body(b, meshes) {
    const frame = new THREE.Group();
    frame.position.copy(at(b.pos));
    frame.quaternion.copy(quat(b.quat));
    let inner = frame;
    if (b.joint) {            // the hinge turns the body about an axis through the joint's anchor
      const hinge = new THREE.Group();
      hinge.position.copy(at(b.joint.pos));
      inner = new THREE.Group();
      inner.position.copy(at(b.joint.pos)).negate();
      frame.add(hinge.add(inner));
      joints.set(b.joint.name, { hinge, axis: new THREE.Vector3(...b.joint.axis).normalize(), deg: 0, posed: false });
    }
    inner.name = b.name;      // the body's frame after its hinge, as MuJoCo reports it
    for (const g of b.geoms) {
      const geometry = g.mesh ? meshes.get(g.mesh + '|' + g.scale) : shape(g);
      if (!geometry) continue;
      const dark = 0.2126 * g.rgba[0] + 0.7152 * g.rgba[1] + 0.0722 * g.rgba[2] < 0.3;
      const part = drawn(geometry, dark ? MOTOR : PART);
      part.position.copy(at(g.pos));
      part.quaternion.copy(quat(g.quat));
      inner.add(part);
    }
    for (const child of b.children) inner.add(body(child, meshes));
    return frame;
  }

  function pen(p) {           // a plain dark pen whose point is exactly where the arm side measured it
    const g = new THREE.Group();
    g.name = 'pen_tip';
    g.position.copy(at(p.tip));
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...p.axis).normalize().negate());
    const r = 3.6, nib = 11, len = p.length * MM + 52;
    const dark = new THREE.MeshLambertMaterial({ color: '#2b2824' });
    g.add(new THREE.Mesh(new THREE.CylinderGeometry(r, 0.5, nib, 20).rotateX(Math.PI / 2).translate(0, 0, nib / 2), dark));
    g.add(new THREE.Mesh(new THREE.CylinderGeometry(r, r, len - nib, 20).rotateX(Math.PI / 2).translate(0, 0, (len + nib) / 2), dark));
    return g;
  }

  function contact(x, y, w, h, yawDeg) {      // a faint smudge where something stands on the paper
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const ctx = c.getContext('2d'), grad = ctx.createRadialGradient(64, 64, 8, 64, 64, 64);
    grad.addColorStop(0, 'rgba(29,27,24,.20)');
    grad.addColorStop(0.55, 'rgba(29,27,24,.09)');
    grad.addColorStop(1, 'rgba(29,27,24,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 128, 128);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({
      map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
    m.position.set(x, y, 0.2);
    m.rotation.z = (yawDeg || 0) * Math.PI / 180;
    m.renderOrder = -1;
    return m;
  }

  function table(box, card) {
    const g = new THREE.Group();
    const [bx, by, bz] = box.size.map((v) => v * MM);
    const riser = drawn(new THREE.BoxGeometry(bx, by, bz), BOX);
    riser.position.copy(at(box.pos));
    riser.quaternion.copy(quat(box.quat));
    // The card lies a hair above the box so the box's own edge cannot show through it; its outline
    // and its red line (on the side away from the arm) lie a hair above the card.
    const [cx, cy] = card.size.map((v) => v * MM);
    const paper = new THREE.PlaneGeometry(cx, cy);
    const sheet = new THREE.Group();
    sheet.position.copy(at(card.pos)).add(new THREE.Vector3(0, 0, 0.3));
    sheet.quaternion.copy(quat(card.quat));
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(paper), ink);
    const x = card.red_line_x * MM;
    const rule = new THREE.Line(new THREE.BufferGeometry().setFromPoints(
      [new THREE.Vector3(x, -cy / 2, 0), new THREE.Vector3(x, cy / 2, 0)]), new THREE.LineBasicMaterial({ color: MARK }));
    outline.position.z = rule.position.z = 0.1;
    sheet.add(new THREE.Mesh(paper, new THREE.MeshLambertMaterial({ color: CARD })), outline, rule);
    g.add(riser, sheet, contact(box.pos[0] * MM, box.pos[1] * MM, bx * 1.7, by * 1.45, box.yaw_deg), contact(8, 0, 190, 170));
    return g;
  }

  function fit() {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const az = VIEW.azimuth * Math.PI / 180, el = VIEW.elevation * Math.PI / 180;
    const target = new THREE.Vector3(...VIEW.target);
    const far = Math.max(VIEW.halfHeight, VIEW.halfWidth / camera.aspect) / Math.tan(VIEW.fov / 2 * Math.PI / 180);
    camera.position.set(Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)).multiplyScalar(far).add(target);
    camera.lookAt(target);
    camera.updateProjectionMatrix();
    if (built) renderer.render(scene, camera);    // straight away: resizing clears the canvas
  }

  function turn(j) {
    j.hinge.quaternion.setFromAxisAngle(j.axis, j.deg * Math.PI / 180);
  }

  function show() {           // full strength while poses arrive; faded, and labeled, when they stop
    if (!built || online === null) return;
    canvas.style.transition = label.style.transition = fade();
    canvas.style.opacity = online ? 1 : OFFLINE_OPACITY;
    label.style.opacity = online ? 0 : 1;
  }

  function take(pose) {
    const now = performance.now();
    const pos = pose && typeof pose.pos === 'object' && pose.pos;
    if (pos) {
      for (const name in pos) if (typeof pos[name] === 'number' && Number.isFinite(pos[name])) want[name] = pos[name];
      if (typeof pose.t === 'number') {
        const age = Date.now() / 1000 - pose.t;   // by the two laptops' clocks
        if (Math.abs(age) * 1000 <= o.staleMs) fresh = Math.max(fresh, now - Math.max(0, age) * 1000);
        if (seenT !== null && pose.t !== seenT) fresh = now;   // a new pose came in, whatever the clocks say
        seenT = pose.t;
      }
    }
    const was = online;
    online = now - fresh <= o.staleMs;
    if (online !== was) show();
  }

  async function fetchPose() {
    pending = new AbortController();
    const giveUp = setTimeout(() => pending.abort(), Math.max(1000, o.pollMs * 5));
    try {
      const r = await fetch(o.poseUrl, { cache: 'no-store', signal: pending.signal });
      return r.ok ? await r.json() : null;
    } finally {
      clearTimeout(giveUp);
    }
  }

  async function poll() {
    if (gone()) return;
    let pose = null;
    try {
      pose = await (o.getPose ? o.getPose() : fetchPose());
    } catch (err) {
      pose = null;            // a failed fetch is no pose
    }
    if (disposed) return;
    take(pose);
    timer = setTimeout(poll, o.pollMs);
  }

  function frame(ts) {
    if (gone()) return;
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, Math.max(0, (ts - last) / 1000));
    last = ts;
    const k = still.matches ? 1 : 1 - Math.exp(-dt / EASE_S);
    for (const [name, j] of joints) {
      const goal = name in held ? undefined : want[name];
      if (goal === undefined || goal === j.deg) continue;
      // ease toward the pose; the first pose is not a movement, and reduced motion means no easing at all
      j.deg = !j.posed || Math.abs(goal - j.deg) < 0.01 ? goal : j.deg + (goal - j.deg) * k;
      j.posed = true;
      dirty = true;
    }
    if (!dirty || !built) return;
    for (const j of joints.values()) turn(j);
    renderer.render(scene, camera);
    dirty = false;
  }

  function gone() {           // the container was taken out of the page: stop everything
    if (disposed) return true;
    if (container.isConnected && wrap.isConnected) attached = true;
    else if (attached) dispose();
    return disposed;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(raf);
    clearTimeout(timer);
    if (pending) pending.abort();
    resize.disconnect();
    still.removeEventListener('change', show);
    scene.traverse((obj) => {
      if (obj.geometry) {
        if (obj.geometry.userData.edges) obj.geometry.userData.edges.dispose();
        obj.geometry.dispose();
      }
      for (const m of [].concat(obj.material || [])) {
        if (m.map) m.map.dispose();
        m.dispose();
      }
    });
    renderer.dispose();
    renderer.forceContextLoss();
    wrap.remove();
  }

  async function build() {
    const r = await fetch(o.sceneUrl, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`${o.sceneUrl}: ${r.status}`);
    const spec = await r.json();
    const wanted = new Map();
    (function collect(b) {
      for (const g of b.geoms) if (g.mesh) wanted.set(g.mesh + '|' + g.scale, g);
      b.children.forEach(collect);
    })(spec.world);
    const loader = new STLLoader(), meshes = new Map();
    await Promise.all([...wanted].map(async ([key, g]) => {
      try {
        const geometry = await loader.loadAsync(o.meshBase + g.mesh);
        meshes.set(key, geometry.scale(g.scale[0] * MM, g.scale[1] * MM, g.scale[2] * MM));
      } catch (err) {
        console.error('arm: mesh not loaded', o.meshBase + g.mesh, err);
      }
    }));
    if (disposed) return false;
    held = spec.hold_deg || {};
    const world = body(spec.world, meshes);
    const hand = spec.pen && world.getObjectByName(spec.pen.body);
    if (hand) hand.add(pen(spec.pen));
    scene.add(world, table(spec.box, spec.card));
    for (const [name, j] of joints) {             // until a pose arrives: the jaw closed, the arm at home
      j.deg = name in held ? held[name] : (spec.home_deg || {})[name] || 0;
      turn(j);
    }
    built = dirty = true;
    fit();
    show();
    return true;
  }

  const resize = new ResizeObserver(fit);
  resize.observe(wrap);
  still.addEventListener('change', show);
  fit();
  raf = requestAnimationFrame(frame);
  poll();
  const ready = build().catch((err) => {
    console.error('arm: not drawn', err);
    label.style.opacity = 1;
    return false;
  });
  return { ready, scene, dispose };
}
