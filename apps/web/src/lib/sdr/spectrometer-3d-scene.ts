import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import { WATERFALL_PALETTE, dbToPaletteIndex } from "@/lib/sdr/palette";
import { MAX_DB, MIN_DB } from "@/lib/sdr/types";

export const FREQ_BINS = 256;
export const TIME_ROWS = 128;

const MESH_WIDTH = 10;
const MESH_DEPTH = 8;
const HEIGHT_SCALE = 2.4;
const DB_RANGE = MAX_DB - MIN_DB;

type SceneOptions = {
  canvas: HTMLCanvasElement;
  reducedMotion: boolean;
  onTuneFraction?: (fraction: number) => void;
};

/**
 * Live RF heightmap: X = frequency, Y = power (dB), Z = time (newest at Z=0).
 */
export class Spectrometer3DScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene: THREE.Scene;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly mesh: THREE.Mesh;
  private readonly positions: Float32Array;
  private readonly colors: Float32Array;
  private readonly history: Float32Array;
  private writeHead = 0;
  private filled = 0;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly onTuneFraction?: (fraction: number) => void;
  private markers: { center: THREE.Mesh; tune: THREE.Mesh };
  private offsetFraction = 0;
  private bandwidthFraction = 0.1;
  private raf = 0;
  private disposed = false;
  private resizeObserver: ResizeObserver;

  constructor(options: SceneOptions) {
    this.onTuneFraction = options.onTuneFraction;

    this.renderer = new THREE.WebGLRenderer({
      canvas: options.canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor(0x141414, 1);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x141414, 14, 28);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
    this.camera.position.set(0, 5.2, 9.5);

    this.controls = new OrbitControls(this.camera, options.canvas);
    this.controls.target.set(0, 0.6, MESH_DEPTH * 0.2);
    this.controls.enableDamping = !options.reducedMotion;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 4;
    this.controls.maxDistance = 22;
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.minPolarAngle = 0.15;
    this.controls.update();

    this.scene.add(new THREE.AmbientLight(0xb8c4cc, 0.55));
    const key = new THREE.DirectionalLight(0xe8f4f8, 1.15);
    key.position.set(2, 8, 4);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x7a8890, 0.35);
    fill.position.set(-4, 2, -2);
    this.scene.add(fill);

    const { geometry, positions, colors } = buildHeightmapGeometry();
    this.positions = positions;
    this.colors = colors;
    this.history = new Float32Array(TIME_ROWS * FREQ_BINS);
    this.history.fill(MIN_DB);

    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      flatShading: true,
      roughness: 0.72,
      metalness: 0.08,
      side: THREE.DoubleSide,
    });
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.position.set(0, 0, 0);
    this.scene.add(this.mesh);

    this.markers = this.createMarkers();
    this.scene.add(this.markers.center, this.markers.tune);

    const grid = new THREE.GridHelper(12, 12, 0x3a3236, 0x2a2428);
    grid.position.y = -0.02;
    this.scene.add(grid);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(options.canvas.parentElement ?? options.canvas);
    this.resize();

    options.canvas.addEventListener("pointerdown", this.onPointerDown);
    this.loop();
  }

  pushSpectrum(spectrum: Float32Array): void {
    if (this.disposed) return;
    const row = this.writeHead % TIME_ROWS;
    const dest = row * FREQ_BINS;
    downsampleFftShifted(spectrum, this.history, dest);
    this.writeHead += 1;
    this.filled = Math.min(this.filled + 1, TIME_ROWS);
    this.rebuildMesh();
  }

  setTuneMarkers(offsetHz: number, bandwidthHz: number, sampleRate: number): void {
    if (sampleRate <= 0) return;
    this.offsetFraction = offsetHz / sampleRate;
    this.bandwidthFraction = bandwidthHz / sampleRate;
    this.updateMarkers();
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    this.renderer.domElement.removeEventListener("pointerdown", this.onPointerDown);
    this.controls.dispose();
    this.mesh.geometry.dispose();
    (this.mesh.material as THREE.Material).dispose();
    for (const m of [this.markers.center, this.markers.tune]) {
      m.geometry.dispose();
      (m.material as THREE.Material).dispose();
    }
    this.renderer.dispose();
  }

  private createMarkers() {
    const centerMat = new THREE.MeshBasicMaterial({
      color: 0x7fd4e0,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    });
    const tuneMat = new THREE.MeshBasicMaterial({
      color: 0xe07090,
      transparent: true,
      opacity: 0.16,
      depthWrite: false,
    });
    const center = new THREE.Mesh(
      new THREE.BoxGeometry(0.04, HEIGHT_SCALE + 0.4, MESH_DEPTH * 0.7),
      centerMat,
    );
    const tune = new THREE.Mesh(
      new THREE.BoxGeometry(1, HEIGHT_SCALE + 0.4, MESH_DEPTH * 0.7),
      tuneMat,
    );
    return { center, tune };
  }

  private updateMarkers(): void {
    const tuneX = this.offsetFraction * MESH_WIDTH;
    const bw = Math.max(0.08, this.bandwidthFraction * MESH_WIDTH);
    const z = MESH_DEPTH * 0.35;
    const y = HEIGHT_SCALE * 0.45;
    this.markers.center.position.set(0, y, z);
    this.markers.tune.position.set(tuneX, y, z);
    this.markers.tune.scale.set(bw, 1, 1);
  }

  private rebuildMesh(): void {
    const newest = this.writeHead - 1;
    const rows = this.filled;
    for (let t = 0; t < TIME_ROWS; t++) {
      const age = t;
      const histRow =
        age < rows
          ? ((newest - age) % TIME_ROWS + TIME_ROWS) % TIME_ROWS
          : -1;
      for (let f = 0; f < FREQ_BINS; f++) {
        const vi = t * FREQ_BINS + f;
        const db = histRow >= 0 ? this.history[histRow * FREQ_BINS + f] : MIN_DB;
        const y = dbToHeight(db);
        this.positions[vi * 3 + 1] = y;

        const color = WATERFALL_PALETTE[dbToPaletteIndex(db, MIN_DB, MAX_DB)];
        this.colors[vi * 3] = color[0] / 255;
        this.colors[vi * 3 + 1] = color[1] / 255;
        this.colors[vi * 3 + 2] = color[2] / 255;
      }
    }

    const geo = this.mesh.geometry;
    const posAttr = geo.getAttribute("position") as THREE.BufferAttribute;
    const colAttr = geo.getAttribute("color") as THREE.BufferAttribute;
    posAttr.needsUpdate = true;
    colAttr.needsUpdate = true;
    // Flat shading only needs face normals occasionally; every frame is too costly.
    if (this.writeHead % 3 === 0) {
      geo.computeVertexNormals();
    }
  }

  private resize(): void {
    const canvas = this.renderer.domElement;
    const parent = canvas.parentElement;
    const width = parent?.clientWidth || canvas.clientWidth;
    const height = parent?.clientHeight || canvas.clientHeight;
    if (width < 2 || height < 2) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  private loop = (): void => {
    if (this.disposed) return;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.raf = requestAnimationFrame(this.loop);
  };

  private onPointerDown = (event: PointerEvent): void => {
    if (!this.onTuneFraction || event.button !== 0) return;
    // Ignore orbit drag — only tune on short clicks without much movement
    const startX = event.clientX;
    const startY = event.clientY;
    const canvas = this.renderer.domElement;

    const onUp = (up: PointerEvent) => {
      canvas.removeEventListener("pointerup", onUp);
      const dx = Math.abs(up.clientX - startX);
      const dy = Math.abs(up.clientY - startY);
      if (dx > 5 || dy > 5) return;

      const rect = canvas.getBoundingClientRect();
      this.pointer.x = ((up.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((up.clientY - rect.top) / rect.height) * 2 + 1;
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const hits = this.raycaster.intersectObject(this.mesh);
      if (hits.length === 0) return;
      const x = hits[0].point.x;
      const fraction = x / MESH_WIDTH;
      this.onTuneFraction?.(Math.max(-0.5, Math.min(0.5, fraction)));
    };

    canvas.addEventListener("pointerup", onUp);
  };
}

function dbToHeight(db: number): number {
  const t = (Number.isFinite(db) ? db : MIN_DB) - MIN_DB;
  return Math.max(0, Math.min(1, t / DB_RANGE)) * HEIGHT_SCALE;
}

function buildHeightmapGeometry(): {
  geometry: THREE.BufferGeometry;
  positions: Float32Array;
  colors: Float32Array;
} {
  const vertexCount = FREQ_BINS * TIME_ROWS;
  const positions = new Float32Array(vertexCount * 3);
  const colors = new Float32Array(vertexCount * 3);
  const indices: number[] = [];

  for (let t = 0; t < TIME_ROWS; t++) {
    for (let f = 0; f < FREQ_BINS; f++) {
      const i = t * FREQ_BINS + f;
      const x = ((f / (FREQ_BINS - 1)) - 0.5) * MESH_WIDTH;
      const z = (t / (TIME_ROWS - 1)) * MESH_DEPTH;
      positions[i * 3] = x;
      positions[i * 3 + 1] = 0;
      positions[i * 3 + 2] = z;
      colors[i * 3] = 0.08;
      colors[i * 3 + 1] = 0.1;
      colors[i * 3 + 2] = 0.12;
    }
  }

  for (let t = 0; t < TIME_ROWS - 1; t++) {
    for (let f = 0; f < FREQ_BINS - 1; f++) {
      const a = t * FREQ_BINS + f;
      const b = a + 1;
      const c = a + FREQ_BINS;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return { geometry, positions, colors };
}

/** FFT-shift + average-downsample into FREQ_BINS. */
function downsampleFftShifted(
  spectrum: Float32Array,
  dest: Float32Array,
  destOffset: number,
): void {
  const n = spectrum.length;
  const half = n >> 1;
  const binsPerOut = n / FREQ_BINS;

  for (let i = 0; i < FREQ_BINS; i++) {
    const start = Math.floor(i * binsPerOut);
    const end = Math.floor((i + 1) * binsPerOut);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      const bin = spectrum[(j + half) % n];
      if (Number.isFinite(bin)) {
        sum += bin;
        count += 1;
      }
    }
    dest[destOffset + i] = count > 0 ? sum / count : MIN_DB;
  }
}
