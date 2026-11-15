import * as THREE from "three";
import { MercatorCoordinate, type CustomLayerInterface, type Map as MapLibreMap } from "maplibre-gl";

// Sensor buoys floating in the water, each linked to every other by a light link. Each buoy is a white
// capsule, part of it below the surface, with wave packets rolling out from the
// waterline. Drawn in the map's own GL context so it shares perspective and depth
// with the buildings. Scene units are meters east (x), up (y) and south (z) of the
// first buoy.

// On-screen pixels per model unit at REF_ZOOM. Between constant screen size (0) and
// true world scale (1), ZOOM_FOLLOW lets a buoy shrink as you zoom out and grow as you zoom in.
const PX_PER_UNIT = 30;
const REF_ZOOM = 17;
const ZOOM_FOLLOW = 0.65;
const MIN_PX_PER_UNIT = 6;
const MAX_PX_PER_UNIT = 44;
const EARTH_CIRCUMFERENCE = 40075016.686;
const BODY_RADIUS = 0.26;
const WATER_EXTENT = 1.7;
const BUOY_FLOAT = 0.14;
const BUOY_TOP = BUOY_FLOAT + 0.15 + BODY_RADIUS; // top of the hull, in model units
const ARC_RISE = 0.1; // arc height as a fraction of the span
const HALO_RADIUS = 0.62;

/** On-screen pixels per model unit at a zoom; shared with hit-testing. */
export function pxPerUnitAt(zoom: number) {
  return Math.min(MAX_PX_PER_UNIT, Math.max(MIN_PX_PER_UNIT, PX_PER_UNIT * 2 ** ((zoom - REF_ZOOM) * ZOOM_FOLLOW)));
}

/** Height of a buoy's body center above the water, in model units, for hit-testing. */
export const BUOY_BODY_CENTER = BUOY_FLOAT + 0.12;

export type BuoyLayer = CustomLayerInterface & {
  /** Highlight per buoy: 0 none, 0.5 hovered, 1 selected. Eased in the render loop. */
  setLevels(levels: number[]): void;
};

// Clips everything below the water surface (y = 0)
const waterline = [new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)];

const waterVertex = /* glsl */ `
  varying vec2 vPos;
  void main() {
    vPos = position.xy;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// Wave packets: each one is a few soft crests travelling outward and fading
const ripplesMaterial = (phase: number) =>
  new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
      uPhase: { value: phase },
      uBoost: { value: 0 },
      uBodyR: { value: BODY_RADIUS },
      uColor: { value: new THREE.Color("#8cc8ff") },
    },
    vertexShader: waterVertex,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform float uPhase;
      uniform float uBodyR;
      uniform vec3 uColor;
      uniform float uBoost;
      varying vec2 vPos;
      const float PERIOD = 4.2;
      const float R_MAX = ${WATER_EXTENT.toFixed(2)};
      void main() {
        float r = length(vPos);
        float angle = atan(vPos.y, vPos.x);
        float light = 0.0;
        for (int i = 0; i < 3; i++) {
          float p = fract(uTime / PERIOD + uPhase + float(i) / 3.0);
          float front = uBodyR + p * (R_MAX - uBodyR) * 0.92;
          float d = r - front;
          float envelope = exp(-d * d / 0.045);
          float crest = pow(0.5 + 0.5 * cos(d * 38.0), 5.0);
          float wobble = 0.85 + 0.15 * sin(angle * 5.0 + uTime * 0.7 + float(i) * 2.1);
          light += envelope * crest * wobble * pow(1.0 - p, 1.8);
        }
        light *= smoothstep(uBodyR, uBodyR + 0.06, r);
        float meniscus = exp(-pow((r - uBodyR) / 0.018, 2.0)) * 0.7;
        float a = (light * 0.7 + meniscus) * smoothstep(R_MAX, R_MAX * 0.75, r);
        gl_FragColor = vec4(mix(uColor, vec3(1.0), uBoost * 0.35), a * (1.0 + uBoost * 0.9));
      }
    `,
  });

// The submerged part of the hull, seen as a soft dark shape under the surface
const hullShadowMaterial = () =>
  new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { uBodyR: { value: BODY_RADIUS } },
    vertexShader: waterVertex,
    fragmentShader: /* glsl */ `
      uniform float uBodyR;
      varying vec2 vPos;
      void main() {
        float r = length(vPos);
        float a = 0.55 * (1.0 - smoothstep(uBodyR * 0.6, uBodyR * 1.9, r));
        gl_FragColor = vec4(0.0, 0.05, 0.12, a);
      }
    `,
  });

// Selection ring on the water around a buoy; uLevel fades it from hover to selected
const haloMaterial = () =>
  new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: { uLevel: { value: 0 }, uTime: { value: 0 }, uColor: { value: new THREE.Color("#64d2ff") } },
    vertexShader: waterVertex,
    fragmentShader: /* glsl */ `
      uniform float uLevel, uTime;
      uniform vec3 uColor;
      varying vec2 vPos;
      void main() {
        float r = length(vPos);
        float d = r - ${HALO_RADIUS.toFixed(2)};
        float ring = exp(-d * d / 0.0012);
        float glow = exp(-d * d / 0.03) * 0.35;
        float fill = smoothstep(${HALO_RADIUS.toFixed(2)}, 0.0, r) * 0.12;
        float breathe = 0.88 + 0.12 * sin(uTime * 2.4);
        vec3 col = mix(uColor, vec3(1.0), ring * 0.45);
        gl_FragColor = vec4(col, (ring + glow + fill) * uLevel * breathe);
      }
    `,
  });

// A smooth link between two buoys: a faint steady line with bright pulses of light
// shot along it, one each way. Two crossed ribbons give it body from any angle, and it
// fades out toward both ends.
const arcMaterial = (seed: number, core: boolean) =>
  new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    uniforms: {
      uTime: { value: 0 },
      uSeed: { value: seed },
      uA: { value: new THREE.Vector3() },
      uB: { value: new THREE.Vector3() },
      uEndH: { value: 0 },
      uRise: { value: 0 },
      uWidth: { value: 0 },
      uColor: { value: new THREE.Color(core ? "#e3f3ff" : "#2f8fff") },
      uAlpha: { value: core ? 0.9 : 0.5 },
      uBoost: { value: 0 },
    },
    vertexShader: /* glsl */ `
      attribute float aU;
      attribute float aV;
      attribute float aAxis;
      uniform float uEndH, uRise, uWidth;
      uniform vec3 uA, uB;
      varying float vU;
      varying float vV;
      void main() {
        vU = aU;
        vV = aV;
        vec3 up = vec3(0.0, 1.0, 0.0);
        vec3 side = normalize(cross(normalize(uB - uA), up));
        vec3 c = mix(uA, uB, aU) + up * (uEndH + uRise * sin(3.14159265 * aU));
        vec3 across = aAxis < 0.5 ? up : side;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(c + across * aV * uWidth, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime, uSeed, uAlpha, uBoost;
      uniform vec3 uColor;
      varying float vU;
      varying float vV;
      const float PERIOD = 3.4;
      // A pulse with a sharp leading edge and a fading tail, travelling from u = 0 to 1
      float pulse(float x, float phase) {
        float head = fract(uTime / PERIOD + phase) * 1.5 - 0.25;
        float d = head - x;
        return d >= 0.0 ? exp(-d * 11.0) * step(d, 0.4) : exp(-d * d * 3000.0);
      }
      void main() {
        float profile = pow(1.0 - abs(vV), 2.0);
        float ends = smoothstep(0.0, 0.1, vU) * smoothstep(1.0, 0.9, vU);
        float shot = pulse(vU, uSeed) + pulse(1.0 - vU, uSeed + 0.5);
        gl_FragColor = vec4(uColor * (0.7 + shot), profile * ends * uAlpha * (0.16 + 0.45 * uBoost + 1.3 * (1.0 + uBoost) * shot));
      }
    `,
  });

function arcGeometry(segments = 160) {
  const u: number[] = [];
  const v: number[] = [];
  const axis: number[] = [];
  const index: number[] = [];
  for (let ax = 0; ax < 2; ax++) {
    const base = u.length;
    for (let i = 0; i <= segments; i++) {
      for (const side of [-1, 1]) {
        u.push(i / segments);
        v.push(side);
        axis.push(ax);
      }
    }
    for (let i = 0; i < segments; i++) {
      const a = base + i * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(new Array(u.length * 3).fill(0), 3));
  geo.setAttribute("aU", new THREE.Float32BufferAttribute(u, 1));
  geo.setAttribute("aV", new THREE.Float32BufferAttribute(v, 1));
  geo.setAttribute("aAxis", new THREE.Float32BufferAttribute(axis, 1));
  geo.setIndex(index);
  return geo;
}

function buildBuoy(phase: number) {
  const rig = new THREE.Group(); // scaled per frame, see PX_PER_UNIT
  const clip = { clippingPlanes: waterline };

  const surface = new THREE.Group();
  surface.rotation.x = -Math.PI / 2;
  rig.add(surface);
  const plane = new THREE.PlaneGeometry(WATER_EXTENT * 2, WATER_EXTENT * 2);
  const shadow = new THREE.Mesh(plane, hullShadowMaterial());
  shadow.position.z = 0.002;
  const ripples = ripplesMaterial(phase);
  const water = new THREE.Mesh(plane, ripples);
  water.position.z = 0.004;
  const halo = haloMaterial();
  const haloMesh = new THREE.Mesh(plane, halo);
  haloMesh.position.z = 0.006;
  surface.add(shadow, water, haloMesh);

  const hull = new THREE.Group();
  rig.add(hull);

  hull.add(
    new THREE.Mesh(
      new THREE.CapsuleGeometry(BODY_RADIUS, 0.3, 12, 48),
      new THREE.MeshLambertMaterial({ color: "#f5f6f8", ...clip }),
    ),
  );

  const band = new THREE.Mesh(
    new THREE.CylinderGeometry(BODY_RADIUS + 0.004, BODY_RADIUS + 0.004, 0.075, 64, 1, true),
    new THREE.MeshLambertMaterial({ color: "#0a84ff", emissive: "#0a4aa8", emissiveIntensity: 0.35, ...clip }),
  );
  band.position.y = -0.02;
  hull.add(band);

  const top = 0.15 + BODY_RADIUS;
  const bezel = new THREE.Mesh(
    new THREE.TorusGeometry(0.055, 0.014, 12, 40),
    new THREE.MeshLambertMaterial({ color: "#2c2c2e", ...clip }),
  );
  bezel.rotation.x = Math.PI / 2;
  bezel.position.y = top - 0.008;
  hull.add(bezel);

  const led = new THREE.Mesh(
    new THREE.SphereGeometry(0.042, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: "#64d2ff", ...clip }),
  );
  led.position.y = top - 0.012;
  hull.add(led);

  return { rig, hull, ripples, halo, led: led.material as THREE.MeshBasicMaterial, phase };
}

export function createBuoyLayer(id: string, points: [number, number][]): BuoyLayer {
  const origin = MercatorCoordinate.fromLngLat(points[0], 0);
  const metersToMerc = origin.meterInMercatorCoordinateUnits();
  const reducedMotion =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const camera = new THREE.Camera();
  const scene = new THREE.Scene();

  const anchors = points.map((p) => {
    const m = MercatorCoordinate.fromLngLat(p, 0);
    return new THREE.Vector3((m.x - origin.x) / metersToMerc, 0, (m.y - origin.y) / metersToMerc);
  });

  const buoys = anchors.map((at, i) => {
    const b = buildBuoy(i * 0.37);
    b.rig.position.copy(at);
    scene.add(b.rig);
    return b;
  });

  const arcGeo = arcGeometry();
  // Every buoy links to every other one, a full mesh
  const pairs = anchors.flatMap((_, i) => anchors.slice(i + 1).map((__, j) => [i, i + 1 + j] as const));
  const arcs = pairs.flatMap(([i, j], n) => {
    const from = anchors[i];
    const to = anchors[j];
    return [false, true].map((core) => {
      const material = arcMaterial(n * 0.23, core);
      material.uniforms.uA.value.copy(from);
      material.uniforms.uB.value.copy(to);
      const mesh = new THREE.Mesh(arcGeo, material);
      mesh.frustumCulled = false; // vertices are placed in the shader
      scene.add(mesh);
      return { material, span: from.distanceTo(to), core, ends: [i, j] as const };
    });
  });

  const levels = points.map(() => 0);
  let targets = points.map(() => 0);
  let last = performance.now();

  const ledDim = new THREE.Color("#0a84ff");
  const ledBright = new THREE.Color("#7fdcff");

  scene.add(new THREE.HemisphereLight("#e3eeff", "#0b1a2a", 1.5));
  const key = new THREE.DirectionalLight("#ffffff", 2.1);
  key.position.set(0.6, 1, 0.8);
  scene.add(key);
  const rim = new THREE.DirectionalLight("#7ab8ff", 1.3);
  rim.position.set(-1, 0.35, -1);
  scene.add(rim);

  let map: MapLibreMap;
  let renderer: THREE.WebGLRenderer;
  const start = performance.now();

  return {
    id,
    type: "custom",
    renderingMode: "3d",
    onAdd(m, gl) {
      map = m;
      renderer = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true });
      renderer.autoClear = false;
      renderer.localClippingEnabled = true;
    },
    onRemove() {
      renderer.dispose();
      const disposed = new Set<{ dispose(): void }>();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          disposed.add(o.geometry);
          disposed.add(o.material as THREE.Material);
        }
      });
      disposed.forEach((d) => d.dispose());
    },
    render(_gl, args) {
      const now = performance.now();
      const t = reducedMotion ? 0 : (now - start) / 1000;
      const ease = reducedMotion ? 1 : 1 - Math.exp(-((now - last) / 1000) * 12);
      last = now;
      targets.forEach((target, i) => (levels[i] += (target - levels[i]) * ease));
      const zoom = map.getZoom();

      const metersPerPx = (EARTH_CIRCUMFERENCE * Math.cos((points[0][1] * Math.PI) / 180)) / (512 * 2 ** zoom);
      const k = pxPerUnitAt(zoom) * metersPerPx;

      for (const [i, b] of buoys.entries()) {
        const level = levels[i];
        // Negative z cancels the y-flip in the map matrix, so three keeps winding and normals right
        b.rig.scale.set(k, k, -k);
        const p = t + b.phase * 10;
        b.hull.position.y = BUOY_FLOAT + Math.sin(p * 1.3) * 0.022;
        b.hull.rotation.x = Math.sin(p * 0.9) * 0.06;
        b.hull.rotation.z = Math.sin(p * 0.7 + 1.3) * 0.05;
        b.hull.scale.setScalar(1 + level * 0.08);
        b.led.color.lerpColors(ledDim, ledBright, Math.min(1, 0.5 + 0.5 * Math.sin(p * 2.2) + level));
        b.ripples.uniforms.uTime.value = t;
        b.ripples.uniforms.uBoost.value = level;
        b.halo.uniforms.uLevel.value = level;
        b.halo.uniforms.uTime.value = t;
      }

      for (const a of arcs) {
        const u = a.material.uniforms;
        u.uTime.value = t;
        u.uEndH.value = BUOY_TOP * k;
        u.uRise.value = a.span * ARC_RISE;
        u.uWidth.value = (a.core ? 1.1 : 4.5) * metersPerPx;
        u.uBoost.value = Math.max(levels[a.ends[0]], levels[a.ends[1]]);
      }

      const s = metersToMerc;
      const model = new THREE.Matrix4()
        .makeTranslation(origin.x, origin.y, origin.z)
        .scale(new THREE.Vector3(s, -s, s))
        .multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));
      camera.projectionMatrix = new THREE.Matrix4().fromArray(args.defaultProjectionData.mainMatrix).multiply(model);

      renderer.resetState();
      renderer.render(scene, camera);
      if (!reducedMotion || targets.some((target, i) => Math.abs(target - levels[i]) > 0.001)) map.triggerRepaint();
    },
    setLevels(next) {
      targets = next;
      map?.triggerRepaint();
    },
  };
}
