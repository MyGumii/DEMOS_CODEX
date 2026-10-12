import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { buildLayout, createCollisionIndex, canStand } from './layout.js';

export const LANGUAGE_COLORS = {
  Python: '#80d5be', TypeScript: '#88bcf2', JavaScript: '#e8c779',
  HTML: '#e59f88', CSS: '#b4a1e7', Markdown: '#91a8bb',
  JSON: '#a0b3ce', Shell: '#b9d394', Other: '#b5bbbf',
};
export const DISTRICT_COLORS = ['#8ee0c4', '#85bbf1', '#bcacef', '#e5bd80', '#87cdd9',
  '#a4bacd', '#d4b2be', '#a4d09b', '#e19e87', '#a1b0f0'];

const EYE_HEIGHT = 2.5;
const up = new THREE.Vector3(0, 1, 0);
const forward = new THREE.Vector3();
const right = new THREE.Vector3();
const lookEuler = new THREE.Euler(0, 0, 0, 'YXZ');

function facadeTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 128; canvas.height = 256;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#9ca6aa'; ctx.fillRect(0, 0, 128, 256);
  for (let row = 0; row < 16; row++) for (let col = 0; col < 6; col++) {
    ctx.fillStyle = (row * 11 + col * 7) % 9 < 3 ? '#dce9e7' : '#747f88';
    ctx.fillRect(7 + col * 20, row * 16 + 5, 9, 5);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.magFilter = THREE.LinearFilter;
  texture.anisotropy = 4;
  return texture;
}

function addLine(vertices, colors, a, b, color) {
  vertices.push(...a, ...b);
  const c = new THREE.Color(color);
  colors.push(c.r, c.g, c.b, c.r, c.g, c.b);
}

export class AtlasWorld {
  constructor(host, data, callbacks = {}) {
    this.host = host; this.data = data; this.callbacks = callbacks;
    this.layout = buildLayout(data);
    this.collision = createCollisionIndex(this.layout.buildings);
    this.mode = 'orbit'; this.selected = null; this.hovered = null;
    this.keys = new Set(); this.transition = null; this.dragStart = null;
    this.metrics = { fps: 0, frames: 0, drawCalls: 0, triangles: 0 };
    this.walkSpawn = null; this.linkObjects = []; this.visibleDistrict = null;
    this.dragWalking = false; this.lookPointer = null;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#0c1721');
    this.scene.fog = new THREE.FogExp2('#0c1721', 0.0011);
    this.camera = new THREE.PerspectiveCamera(43, 1, 0.15, 3500);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.25;
    this.renderer.domElement.setAttribute('aria-label', '실제 저장소의 파일을 건물로 표현한 3D 지도');
    this.renderer.domElement.tabIndex = 0;
    host.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'world-labels';
    host.appendChild(this.labels.domElement);
    this.orbit = new OrbitControls(this.camera, this.renderer.domElement);
    this.orbit.enableDamping = true; this.orbit.dampingFactor = 0.075;
    this.orbit.maxPolarAngle = Math.PI / 2 - 0.04;
    this.orbit.minDistance = 10;
    this.orbit.maxDistance = Math.max(this.layout.width, this.layout.depth) * 5;
    this.orbit.target.set(0, 0, 0);
    this.walk = new PointerLockControls(this.camera, this.renderer.domElement);
    this.walk.minPolarAngle = 0.16; this.walk.maxPolarAngle = Math.PI - 0.16;
    this.walk.pointerSpeed = 0.8;
    this.walk.addEventListener('lock', () => {
      this.dragWalking = false;
      this.keys.clear(); this.callbacks.onLock?.(true);
    });
    this.walk.addEventListener('unlock', () => {
      this.dragWalking = false;
      this.keys.clear(); this.callbacks.onLock?.(false);
    });
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.originalColors = [];
    this.renderCity(); this.bindEvents(); this.resize(); this.overview(false);
    this.timer = new THREE.Timer(); this.timer.connect(document); this.lastMetricAt = performance.now();
    this.metricFrames = 0; this.running = true;
    this.renderer.setAnimationLoop(() => this.tick());
  }

  renderCity() {
    const extent = Math.max(this.layout.width, this.layout.depth);
    this.scene.add(new THREE.HemisphereLight('#d9eff6', '#173a3c', 2.2));
    const key = new THREE.DirectionalLight('#e5f7ff', 2.7);
    key.position.set(-120, 260, 100); this.scene.add(key);
    const fill = new THREE.DirectionalLight('#7199d8', 1.1);
    fill.position.set(160, 80, -100); this.scene.add(fill);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(extent * 4, extent * 4),
      new THREE.MeshStandardMaterial({ color: '#14212b', roughness: 1 }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -0.12; this.scene.add(floor);
    const grid = new THREE.GridHelper(extent * 3, Math.min(160, Math.ceil(extent / 3)), '#31424a', '#23333c');
    grid.material.transparent = true; grid.material.opacity = 0.33;
    grid.position.y = -0.08; this.scene.add(grid);

    const vertices = [], colors = [];
    this.districtLabels = [];
    for (const district of this.layout.districts) {
      const color = DISTRICT_COLORS[district.index % DISTRICT_COLORS.length];
      const platform = new THREE.Mesh(new THREE.BoxGeometry(district.width, 0.35, district.depth),
        new THREE.MeshStandardMaterial({ color: district.index % 2 ? '#22343d' : '#20303b', roughness: 0.9 }));
      platform.position.set(district.center.x, 0.07, district.center.z); this.scene.add(platform);
      const x0 = district.x, x1 = x0 + district.width, z0 = district.z, z1 = z0 + district.depth;
      for (const [a, b] of [[[x0, .28, z0], [x1, .28, z0]], [[x1, .28, z0], [x1, .28, z1]],
        [[x1, .28, z1], [x0, .28, z1]], [[x0, .28, z1], [x0, .28, z0]]]) {
        addLine(vertices, colors, a, b, color);
      }
      // Five-building blocks leave broad cross streets for first-person walking.
      for (let col = 5; col < district.columns; col += 5) {
        const x = district.x + 9 + col * 6.4 + Math.floor(col / 5) * 5.6 - 6;
        addLine(vertices, colors, [x, .3, z0 + 2], [x, .3, z1 - 2], '#48626d');
      }
      for (let row = 5; row < district.rows; row += 5) {
        const z = district.z + 9 + row * 6.4 + Math.floor(row / 5) * 5.6 - 6;
        addLine(vertices, colors, [x0 + 2, .3, z], [x1 - 2, .3, z], '#48626d');
      }
      const element = document.createElement('button');
      element.className = 'district-label';
      element.style.setProperty('--district-color', color);
      const num = document.createElement('span'); num.className = 'label-number';
      num.textContent = `${district.index + 1}`.padStart(2, '0');
      const title = document.createElement('strong'); title.textContent = district.label;
      const count = document.createElement('small'); count.textContent = `${district.files.length.toLocaleString()} files`;
      element.append(num, title, count);
      element.addEventListener('click', event => {
        event.stopPropagation(); this.callbacks.onDistrict?.(district.id);
      });
      const label = new CSS2DObject(element);
      label.position.set(district.center.x, 1.3, z1 + 4.5);
      this.scene.add(label);
      this.districtLabels.push({ district, label, element });
    }
    const lines = new THREE.BufferGeometry();
    lines.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    lines.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    this.scene.add(new THREE.LineSegments(lines,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.6 })));

    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const windowTex = facadeTexture();
    const material = new THREE.MeshStandardMaterial({ map: windowTex, roughness: .68, metalness: .18 });
    this.buildings = new THREE.InstancedMesh(geometry, material, this.layout.buildings.length);
    this.buildings.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.roofs = new THREE.InstancedMesh(geometry,
      new THREE.MeshStandardMaterial({ roughness: .65, metalness: .15 }), this.layout.buildings.length);
    const dummy = new THREE.Object3D();
    for (const building of this.layout.buildings) {
      const color = new THREE.Color(LANGUAGE_COLORS[building.file.language] || LANGUAGE_COLORS.Other);
      this.originalColors.push(color.clone());
      dummy.position.set(building.x, building.height / 2 + .26, building.z);
      dummy.scale.set(building.width, building.height, building.depth); dummy.updateMatrix();
      this.buildings.setMatrixAt(building.index, dummy.matrix);
      this.buildings.setColorAt(building.index, color);
      dummy.position.y = building.height + .3;
      dummy.scale.set(building.width + .12, .3, building.depth + .12); dummy.updateMatrix();
      this.roofs.setMatrixAt(building.index, dummy.matrix);
      this.roofs.setColorAt(building.index, color.clone().multiplyScalar(.77));
    }
    this.buildings.instanceMatrix.needsUpdate = true;
    this.buildings.instanceColor.needsUpdate = true;
    this.roofs.instanceMatrix.needsUpdate = true;
    this.roofs.instanceColor.needsUpdate = true;
    this.buildings.computeBoundingSphere(); this.roofs.computeBoundingSphere();
    this.scene.add(this.buildings, this.roofs);
    this.selection = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: '#effff4' }));
    this.selection.visible = false; this.scene.add(this.selection);
    this.beacon = new THREE.Mesh(new THREE.RingGeometry(2.7, 3.15, 40),
      new THREE.MeshBasicMaterial({ color: '#b5ffe0', side: THREE.DoubleSide, transparent: true, opacity: .9 }));
    this.beacon.rotation.x = -Math.PI / 2; this.beacon.visible = false; this.scene.add(this.beacon);
    this.fileLabelEl = document.createElement('div'); this.fileLabelEl.className = 'file-label';
    this.fileLabel = new CSS2DObject(this.fileLabelEl); this.fileLabel.visible = false;
    this.scene.add(this.fileLabel);
  }

  bindEvents() {
    const canvas = this.renderer.domElement;
    canvas.addEventListener('pointerdown', e => {
      this.transition = null; this.dragStart = { x: e.clientX, y: e.clientY };
      this.lookPointer = { x: e.clientX, y: e.clientY };
    });
    canvas.addEventListener('pointerup', e => {
      const start = this.dragStart; this.dragStart = null; this.lookPointer = null;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 5 && this.mode === 'orbit') {
        this.pick(e.clientX, e.clientY, true);
      } else if (this.mode === 'walk' && this.isWalking && start &&
        Math.hypot(e.clientX - start.x, e.clientY - start.y) < 5) this.pick(0, 0, true);
    });
    canvas.addEventListener('pointermove', e => {
      if (this.mode === 'orbit' && !this.dragStart) this.pick(e.clientX, e.clientY, false);
      if (this.mode === 'walk' && this.dragWalking && this.dragStart && this.lookPointer) {
        lookEuler.setFromQuaternion(this.camera.quaternion);
        lookEuler.y -= (e.clientX - this.lookPointer.x) * .003;
        lookEuler.x = Math.max(-1.35, Math.min(1.35,
          lookEuler.x - (e.clientY - this.lookPointer.y) * .003));
        this.camera.quaternion.setFromEuler(lookEuler);
        this.lookPointer = { x: e.clientX, y: e.clientY };
      }
    });
    canvas.addEventListener('pointerleave', () => {
      if (!this.selected) this.fileLabel.visible = false;
      this.callbacks.onHover?.(null);
    });
    canvas.addEventListener('click', () => {
      if (this.mode === 'walk' && !this.isWalking && !this.selected) this.lockWalk();
    });
    document.addEventListener('keydown', e => {
      if (e.target.matches('input, textarea, select')) return;
      if (e.code === 'Escape' && this.dragWalking) { this.unlock(); return; }
      if (this.mode === 'walk' && this.isWalking &&
          ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.code)) {
        e.preventDefault(); this.keys.add(e.code);
      }
    });
    document.addEventListener('keyup', e => this.keys.delete(e.code));
    window.addEventListener('blur', () => this.keys.clear());
    document.addEventListener('visibilitychange', () => this.keys.clear());
    document.addEventListener('pointerlockerror', () => {
      if (this.mode !== 'walk') return;
      this.dragWalking = true; this.callbacks.onLock?.(true);
      this.callbacks.onToast?.('WASD 이동 · 마우스 드래그로 시점 전환 · Esc 해제');
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.host);
  }

  resize() {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    this.camera.aspect = width / Math.max(1, height); this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height); this.labels.setSize(width, height);
  }

  pick(clientX, clientY, select) {
    if (this.mode === 'walk') this.pointer.set(0, 0);
    else {
      const bounds = this.renderer.domElement.getBoundingClientRect();
      this.pointer.set(((clientX - bounds.left) / bounds.width) * 2 - 1,
        -((clientY - bounds.top) / bounds.height) * 2 + 1);
    }
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.buildings, false)[0];
    const building = hit ? this.layout.buildings[hit.instanceId] : null;
    if (building && this.visibleDistrict && building.district !== this.visibleDistrict) return;
    this.hovered = building;
    this.renderer.domElement.style.cursor = building && this.mode === 'orbit' ? 'pointer' : 'grab';
    if (building) {
      this.showFileLabel(building);
      this.callbacks.onHover?.(building.file);
      if (select) {
        this.unlock(); this.callbacks.onSelect?.(building.file.id);
      }
    } else if (!this.selected) {
      this.fileLabel.visible = false; this.callbacks.onHover?.(null);
    }
  }

  showFileLabel(building) {
    this.fileLabelEl.textContent = building.file.name;
    this.fileLabel.position.set(building.x, building.height + 3.1, building.z);
    this.fileLabel.visible = true;
  }

  selectFile(id, focus = true) {
    const building = this.layout.byId.get(id);
    if (!building) return;
    this.selected = building; this.unlock();
    this.selection.position.set(building.x, building.height / 2 + .26, building.z);
    this.selection.scale.set(building.width + .24, building.height + .3, building.depth + .24);
    this.selection.visible = true;
    this.beacon.position.set(building.x, .5, building.z); this.beacon.visible = true;
    this.showFileLabel(building);
    this.updateColors(); this.renderConnections(id);
    if (focus) {
      if (this.mode === 'walk') this.setMode('orbit');
      const height = building.height;
      this.flyTo(new THREE.Vector3(building.x + 25, height + 22, building.z + 38),
        new THREE.Vector3(building.x, height * .45, building.z));
    }
    this.walkSpawn = { x: building.x - 3.2, z: building.z + 3.2,
      targetX: building.x - 3.2, targetZ: building.z - 16 };
    this.callbacks.onState?.();
  }

  clearSelection() {
    this.selected = null; this.selection.visible = false; this.beacon.visible = false;
    this.fileLabel.visible = false; this.clearConnections(); this.updateColors(); this.callbacks.onState?.();
  }

  updateColors() {
    const selectedId = this.selected?.file.id;
    const neighbors = new Set(this.data.edges.filter(e => e.source === selectedId || e.target === selectedId)
      .flatMap(e => [e.source, e.target]));
    for (const building of this.layout.buildings) {
      const base = this.originalColors[building.index];
      const muted = (this.visibleDistrict && building.district !== this.visibleDistrict) ||
        (selectedId && !neighbors.has(building.file.id) && building.file.id !== selectedId);
      const color = muted ? base.clone().multiplyScalar(.42) : base;
      this.buildings.setColorAt(building.index, color);
      this.roofs.setColorAt(building.index, color.clone().multiplyScalar(.85));
    }
    this.buildings.instanceColor.needsUpdate = true; this.roofs.instanceColor.needsUpdate = true;
  }

  clearConnections() {
    for (const object of this.linkObjects) {
      this.scene.remove(object); object.geometry.dispose(); object.material.dispose();
    }
    this.linkObjects = [];
  }

  renderConnections(id) {
    this.clearConnections();
    const edges = [...new Map(this.data.edges.filter(edge => edge.source === id || edge.target === id)
      .map(edge => [`${edge.source}\0${edge.target}`, edge])).values()].slice(0, 60);
    for (const edge of edges) {
      const a = this.layout.byId.get(edge.source), b = this.layout.byId.get(edge.target);
      if (!a || !b) continue;
      const start = new THREE.Vector3(a.x, a.height + 1, a.z);
      const end = new THREE.Vector3(b.x, b.height + 1, b.z);
      const distance = start.distanceTo(end);
      const middle = start.clone().lerp(end, .5); middle.y += Math.min(35, distance * .18) + 4;
      const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
      const color = edge.source === id ? '#a0efd1' : '#a9c5ff';
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(24)),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: .66 }));
      this.scene.add(line); this.linkObjects.push(line);
      const tip = curve.getPoint(.76), direction = curve.getTangent(.76).normalize();
      const arrow = new THREE.Mesh(new THREE.ConeGeometry(.65, 1.8, 5), new THREE.MeshBasicMaterial({ color }));
      arrow.position.copy(tip); arrow.quaternion.setFromUnitVectors(up, direction);
      this.scene.add(arrow); this.linkObjects.push(arrow);
    }
  }

  // Runtime stack edges are separate from static import relationships.
  renderExecution(event) {
    const chain = (event.stack || []).map(frame => frame.fileId)
      .filter((id, index, ids) => this.layout.byId.has(id) && id !== ids[index - 1]);
    if (!chain.length && this.layout.byId.has(event.fileId)) chain.push(event.fileId);
    const signature = chain.join('|');
    if (this.executionSignature === signature) return;
    this.executionSignature = signature;
    this.clearConnections();
    for (let index = 1; index < chain.length; index++) {
      const a = this.layout.byId.get(chain[index - 1]), b = this.layout.byId.get(chain[index]);
      if (!a || !b || a === b) continue;
      const start = new THREE.Vector3(a.x, a.height + 2, a.z);
      const end = new THREE.Vector3(b.x, b.height + 2, b.z);
      const middle = start.clone().lerp(end, .5);
      middle.y += Math.min(40, start.distanceTo(end) * .18) + 7;
      const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
      const color = '#f3cf76';
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(30)),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: .95 }));
      const arrow = new THREE.Mesh(new THREE.ConeGeometry(.9, 2.6, 6), new THREE.MeshBasicMaterial({ color }));
      arrow.position.copy(curve.getPoint(.78));
      arrow.quaternion.setFromUnitVectors(up, curve.getTangent(.78).normalize());
      this.scene.add(line, arrow); this.linkObjects.push(line, arrow);
    }
  }

  clearExecution() {
    this.executionSignature = null;
    this.clearConnections();
  }

  focusDistrict(id) {
    this.unlock(); this.setMode('orbit'); this.clearSelection();
    const district = this.layout.districts.find(d => d.id === id);
    this.visibleDistrict = id || null; this.updateColors();
    if (!district) { this.overview(); return; }
    const size = Math.max(district.width, district.depth);
    this.flyTo(new THREE.Vector3(district.center.x + size * .72, size * .85 + 35,
      district.center.z + size * .94), new THREE.Vector3(district.center.x, 3, district.center.z));
    this.walkSpawn = { x: district.center.x, z: district.z + district.depth + 11 };
    this.callbacks.onState?.();
  }

  overview(animate = true) {
    this.unlock(); this.setMode('orbit'); this.visibleDistrict = null;
    this.clearSelection();
    const size = Math.max(this.layout.width, this.layout.depth);
    const aspectFactor = this.camera.aspect < 1.25 ? 1.25 / this.camera.aspect : 1;
    const position = new THREE.Vector3(size * .58, size * .8, size * .87).multiplyScalar(aspectFactor * 1.09);
    const target = new THREE.Vector3(0, 0, 0);
    if (animate) this.flyTo(position, target);
    else { this.camera.position.copy(position); this.orbit.target.copy(target); this.camera.lookAt(target); }
    const district = this.layout.districts[0];
    this.walkSpawn = { x: district.center.x, z: district.z + district.depth + 11 };
    this.callbacks.onState?.();
  }

  flyTo(position, target) {
    this.transition = { startedAt: performance.now(), from: this.camera.position.clone(),
      fromTarget: this.orbit.target.clone(), to: position, target, duration: 1000 };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) this.transition.duration = 1;
  }

  setMode(mode) {
    if (this.mode === mode) return;
    this.unlock(); this.transition = null; this.mode = mode;
    this.orbit.enabled = mode === 'orbit';
    if (mode === 'walk') {
      const spawn = this.walkSpawn;
      this.camera.position.set(spawn.x, EYE_HEIGHT, spawn.z);
      const district = this.layout.districts.find(d => d.id === this.visibleDistrict) || this.layout.districts[0];
      this.camera.lookAt(spawn.targetX ?? district.center.x, EYE_HEIGHT + .4, spawn.targetZ ?? district.center.z);
      this.callbacks.onToast?.('지도 안을 클릭하면 걷기 시작 · WASD 이동 · Esc 해제');
    } else {
      this.orbit.target.copy(this.camera.position).add(new THREE.Vector3(0, 0, -15));
      this.orbit.target.y = 0;
    }
    this.keys.clear(); this.callbacks.onMode?.(mode); this.callbacks.onState?.();
  }

  startWalk() {
    this.clearSelection(); this.setMode('walk'); this.lockWalk();
  }

  lockWalk() {
    if (this.mode === 'walk' && !this.isWalking) {
      this.dragWalking = true; this.keys.clear(); this.callbacks.onLock?.(true);
      try { this.walk.lock(); } catch { this.callbacks.onToast?.('WASD 이동 · 마우스 드래그로 시점 전환'); }
    }
  }

  get isWalking() { return this.walk.isLocked || this.dragWalking; }

  unlock() {
    if (this.walk.isLocked) this.walk.unlock();
    if (this.dragWalking) {
      this.dragWalking = false; this.keys.clear(); this.callbacks.onLock?.(false);
    }
  }

  move(delta) {
    if (!this.isWalking || !this.keys.size) return;
    let longitudinal = (this.keys.has('KeyW') || this.keys.has('ArrowUp') ? 1 : 0) -
      (this.keys.has('KeyS') || this.keys.has('ArrowDown') ? 1 : 0);
    let lateral = (this.keys.has('KeyD') || this.keys.has('ArrowRight') ? 1 : 0) -
      (this.keys.has('KeyA') || this.keys.has('ArrowLeft') ? 1 : 0);
    const magnitude = Math.hypot(longitudinal, lateral);
    if (!magnitude) return;
    longitudinal /= magnitude; lateral /= magnitude;
    this.camera.getWorldDirection(forward); forward.y = 0; forward.normalize();
    right.crossVectors(forward, up).normalize();
    const velocity = delta * 11;
    const dx = (forward.x * longitudinal + right.x * lateral) * velocity;
    const dz = (forward.z * longitudinal + right.z * lateral) * velocity;
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dz)) / .35));
    for (let step = 0; step < steps; step++) {
      if (canStand(this.camera.position.x + dx / steps, this.camera.position.z, this.collision, this.layout.bounds)) {
        this.camera.position.x += dx / steps;
      }
      if (canStand(this.camera.position.x, this.camera.position.z + dz / steps, this.collision, this.layout.bounds)) {
        this.camera.position.z += dz / steps;
      }
    }
    this.camera.position.y = EYE_HEIGHT;
  }

  tick() {
    this.timer.update();
    const delta = Math.min(this.timer.getDelta(), .05);
    if (this.transition) {
      const t = Math.min(1, (performance.now() - this.transition.startedAt) / this.transition.duration);
      const eased = 1 - (1 - t) ** 3;
      this.camera.position.lerpVectors(this.transition.from, this.transition.to, eased);
      this.orbit.target.lerpVectors(this.transition.fromTarget, this.transition.target, eased);
      if (t === 1) this.transition = null;
    }
    if (this.mode === 'orbit') this.orbit.update();
    else this.move(delta);
    for (const item of this.districtLabels) {
      item.label.visible = this.mode === 'orbit' || this.camera.position.distanceTo(item.label.position) < 100;
    }
    this.renderer.render(this.scene, this.camera); this.labels.render(this.scene, this.camera);
    this.renderer.domElement.dataset.mode = this.mode;
    this.renderer.domElement.dataset.walking = String(this.isWalking);
    this.renderer.domElement.dataset.position = this.camera.position.toArray().map(v => v.toFixed(3)).join(',');
    this.metricFrames++; this.metrics.frames++;
    const now = performance.now();
    if (now - this.lastMetricAt >= 1000) {
      this.metrics.fps = Math.round(this.metricFrames * 1000 / (now - this.lastMetricAt));
      this.metrics.drawCalls = this.renderer.info.render.calls;
      this.metrics.triangles = this.renderer.info.render.triangles;
      this.metricFrames = 0; this.lastMetricAt = now;
      this.callbacks.onMetrics?.(this.metrics);
    }
    this.callbacks.onFrame?.();
  }

}
