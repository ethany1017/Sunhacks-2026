import { Loader } from "@googlemaps/js-api-loader";
import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import { MapboxOverlay } from "@deck.gl/mapbox";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { LonLat } from "./types";

export interface View {
  center: LonLat;
  zoom: number;
}
export type MapKind = "google" | "maplibre";

interface Impl {
  kind: MapKind;
  setLayers(layers: any[]): void;
  getView(): View;
  setView(v: View): void;
  destroy(): void;
}

const USGS_TILES = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}";
const DEFAULT_VIEW: View = { center: [-112.0116, 33.4343], zoom: 10 };

async function createGoogle(el: HTMLElement, apiKey: string, view: View, onAuthFailure: () => void): Promise<Impl> {
  (window as any).gm_authFailure = () => onAuthFailure();
  const loader = new Loader({ apiKey, version: "weekly" });
  const timeout = new Promise<never>((_, rej) => setTimeout(() => rej(new Error("Google Maps load timeout")), 9000));
  const lib = (await Promise.race([loader.importLibrary("maps"), timeout])) as google.maps.MapsLibrary;
  const map = new lib.Map(el, {
    center: { lat: view.center[1], lng: view.center[0] },
    zoom: view.zoom,
    mapTypeId: "satellite",
    disableDefaultUI: true,
    gestureHandling: "greedy",
    keyboardShortcuts: false,
    clickableIcons: false,
    tilt: 0,
    heading: 0,
    isFractionalZoomEnabled: true,
    backgroundColor: "#0b1220",
    styles: [
      { featureType: "poi", stylers: [{ visibility: "off" }] },
      { elementType: "labels", stylers: [{ visibility: "off" }] },
    ],
  });
  const overlay = new GoogleMapsOverlay({ interleaved: false, layers: [] });
  overlay.setMap(map);
  return {
    kind: "google",
    setLayers: (layers) => overlay.setProps({ layers }),
    getView: () => {
      const c = map.getCenter();
      return { center: c ? [c.lng(), c.lat()] : view.center, zoom: map.getZoom() ?? view.zoom };
    },
    setView: (v) => map.moveCamera({ center: { lat: v.center[1], lng: v.center[0] }, zoom: v.zoom }),
    destroy: () => {
      overlay.setMap(null);
      overlay.finalize();
    },
  };
}

function createMapLibre(el: HTMLElement, view: View): Impl {
  const map = new maplibregl.Map({
    container: el,
    style: {
      version: 8,
      sources: {
        usgs: {
          type: "raster",
          tiles: [USGS_TILES],
          tileSize: 256,
          maxzoom: 16, // USGS NAIP tiles end at z16 (256 px); MapLibre overzooms beyond
          attribution: "USDA, USGS The National Map: Orthoimagery",
        },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#0a1018" } },
        { id: "usgs", type: "raster", source: "usgs", paint: { "raster-brightness-max": 0.92, "raster-saturation": -0.12, "raster-fade-duration": 0 } },
      ],
    },
    center: view.center,
    zoom: view.zoom - 1,
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false,
    maxPitch: 0,
    keyboard: false,
    fadeDuration: 0,
  });
  map.touchZoomRotate.disableRotation();
  const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
  map.addControl(overlay as unknown as maplibregl.IControl);
  map.on("error", () => {
    /* tile errors are expected offline: the dark background layer keeps the screen non-blank */
  });
  return {
    kind: "maplibre",
    setLayers: (layers) => overlay.setProps({ layers }),
    getView: () => {
      const c = map.getCenter();
      return { center: [c.lng, c.lat], zoom: map.getZoom() + 1 }; // MapLibre zoom uses 512 px tiles: +1 equals Google zoom
    },
    setView: (v) => map.jumpTo({ center: v.center, zoom: v.zoom - 1 }),
    destroy: () => {
      map.removeControl(overlay as unknown as maplibregl.IControl);
      map.remove();
    },
  };
}

/** Owns the base map. Starts with Google when a key exists and falls back to MapLibre + USGS automatically. */
export class MapController {
  private impl: Impl | null = null;
  private layers: any[] = [];
  private host: HTMLElement;
  onFallback: (reason: string) => void = () => {};

  constructor(private container: HTMLElement) {
    this.host = this.freshHost();
  }

  private freshHost(): HTMLElement {
    this.container.querySelectorAll(".map-host").forEach((n) => n.remove());
    const d = document.createElement("div");
    d.className = "map-host";
    this.container.appendChild(d);
    return d;
  }

  get kind(): MapKind {
    return this.impl?.kind ?? "maplibre";
  }

  async init(): Promise<void> {
    const key = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined)?.trim();
    if (key && !new URLSearchParams(location.search).has("offline")) {
      try {
        this.impl = await createGoogle(this.host, key, DEFAULT_VIEW, () => this.fallback("Google Maps rejected the API key"));
        return;
      } catch (e) {
        console.warn("Google Maps failed, using fallback:", e);
      }
    }
    this.useFallback(DEFAULT_VIEW, key ? "Google Maps unavailable" : "no Google key");
  }

  private useFallback(view: View, reason: string): void {
    this.host = this.freshHost();
    this.impl = createMapLibre(this.host, view);
    if (this.layers.length) this.impl.setLayers(this.layers);
    this.onFallback(reason);
  }

  private fallback(reason: string): void {
    if (this.impl?.kind !== "google") return;
    const view = this.impl.getView();
    this.impl.destroy();
    this.useFallback(view, reason);
  }

  setLayers(layers: any[]): void {
    this.layers = layers;
    this.impl?.setLayers(layers);
  }
  getView(): View {
    return this.impl?.getView() ?? DEFAULT_VIEW;
  }
  setView(v: View): void {
    this.impl?.setView(v);
  }
}
