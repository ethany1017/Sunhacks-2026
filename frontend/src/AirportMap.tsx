import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useRef } from 'react'
import type { Aircraft, Airport, LonLat, RunwayConfig, RunwayEnd } from '@/types'

// MapLibre locates its worker relative to its own module, which Vite's dependency bundling breaks.
maplibregl.setWorkerUrl(workerUrl)

// Satellite imagery, like the first viewer (web/src/map.ts). Fallback: the blank dark style below.
const USGS_TILES = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}'
const IMAGERY: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    usgs: { type: 'raster', tiles: [USGS_TILES], tileSize: 256, maxzoom: 16, attribution: 'USDA, USGS The National Map: Orthoimagery' },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#0a1018' } },
    { id: 'usgs', type: 'raster', source: 'usgs', paint: { 'raster-brightness-max': 0.92, 'raster-saturation': -0.12, 'raster-fade-duration': 0 } },
  ],
}
const BLANK: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b0f14' } }],
}
const COLOR = { arrival: '#22d3ee', departure: '#f59e0b', vehicle: '#94a3b8', unknown: '#e5e7eb' }
const RUNWAY = '#4b5563'
const RUNWAY_ACTIVE = '#22c55e'

// Probe one tile first so an offline laptop goes straight to the blank style (no-cors: only a network error rejects).
async function loadStyle(): Promise<maplibregl.StyleSpecification> {
  try {
    await fetch(USGS_TILES.replace('{z}/{y}/{x}', '0/0/0'), { mode: 'no-cors', signal: AbortSignal.timeout(4000) })
    return IMAGERY
  } catch (err) {
    console.warn('Basemap unavailable, using blank style:', err)
    return BLANK
  }
}

// Final approach courses (one per runway end), drawn for the active arrival runways.
function finals(airport: Airport): GeoJSON.FeatureCollection {
  const approaches = (airport.graph as { approaches?: Record<string, { path: LonLat[] }> })?.approaches ?? {}
  return {
    type: 'FeatureCollection',
    features: Object.entries(approaches).map(([end, a]) => ({
      type: 'Feature', geometry: { type: 'LineString', coordinates: a.path }, properties: { end },
    })),
  }
}

// Runway width in px at true scale (KPHX latitude, 512 px tiles): drawn 1.3x so the band frames the imagery runway.
const TRUE_WIDTH: maplibregl.DataDrivenPropertyValueSpecification<number> =
  ['interpolate', ['exponential', 2], ['zoom'], 12, ['*', ['get', 'width_m'], 0.08], 18, ['*', ['get', 'width_m'], 5.2]]

function addAirportLayers(map: maplibregl.Map, airport: Airport, imagery: boolean) {
  const geojson = airport.geojson
  map.addSource('finals', { type: 'geojson', data: finals(airport) })
  map.addLayer({
    id: 'final', type: 'line', source: 'finals', filter: ['in', ['get', 'end'], ['literal', []]],
    paint: { 'line-color': COLOR.arrival, 'line-opacity': 0.6, 'line-width': 3, 'line-dasharray': [2, 2] },
  })
  map.addSource('airport', { type: 'geojson', data: geojson })
  const kind = (k: string) => ['==', ['get', 'kind'], k] as maplibregl.FilterSpecification
  map.addLayer({
    id: 'taxiway', type: 'line', source: 'airport', filter: kind('taxiway'),
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: imagery
      ? { 'line-color': '#fbbf24', 'line-opacity': 0.3, 'line-width': 1 }
      : { 'line-color': '#a16207', 'line-opacity': 0.75, 'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, 1.5, 17, 16] },
  })
  if (imagery) {
    // translucent band + edge lines (line-gap-width draws two parallel lines either side of the runway)
    map.addLayer({ id: 'runway', type: 'line', source: 'airport', filter: kind('runway'), paint: { 'line-color': RUNWAY_ACTIVE, 'line-opacity': 0, 'line-width': TRUE_WIDTH } })
    map.addLayer({
      id: 'runway-edge', type: 'line', source: 'airport', filter: kind('runway'),
      paint: { 'line-color': 'rgba(255,255,255,0.18)', 'line-width': 2, 'line-gap-width': TRUE_WIDTH },
    })
    map.addLayer({
      id: 'hold-short', type: 'circle', source: 'airport', filter: kind('hold_short'),
      paint: { 'circle-color': '#ef4444', 'circle-opacity': 0.8, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 1.5, 17, 4] },
    })
    return
  }
  map.addLayer({
    id: 'runway', type: 'line', source: 'airport', filter: kind('runway'),
    paint: {
      'line-color': RUNWAY,
      // drawn ~3x true width so it reads on a projector
      'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, ['*', ['get', 'width_m'], 0.1], 18, ['*', ['get', 'width_m'], 6.4]],
    },
  })
  map.addLayer({
    id: 'runway-centerline', type: 'line', source: 'airport', filter: kind('runway'),
    paint: { 'line-color': '#f8fafc', 'line-opacity': 0.7, 'line-width': 1.5, 'line-dasharray': [6, 4] },
  })
  map.addLayer({
    id: 'hold-short', type: 'circle', source: 'airport', filter: kind('hold_short'),
    paint: { 'circle-color': '#ef4444', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 2, 17, 5], 'circle-stroke-color': '#0b0f14', 'circle-stroke-width': 1.5 },
  })
  map.addLayer({
    id: 'parking', type: 'circle', source: 'airport', filter: kind('parking'),
    paint: { 'circle-color': '#64748b', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 2, 17, 7], 'circle-stroke-color': '#cbd5e1', 'circle-stroke-width': 1 },
  })
}

function labelMarker(map: maplibregl.Map, lngLat: maplibregl.LngLatLike, text: string, className: string) {
  const el = document.createElement('div')
  el.className = className
  el.textContent = text
  return new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map)
}

function planeElement(a: Aircraft) {
  const el = document.createElement('div')
  el.className = 'aircraft'
  el.innerHTML =
    '<svg viewBox="-16 -16 32 32" width="40" height="40"><path d="M0,-14 L2.5,-5 L13,1 L13,4 L2.5,1 L2,9 L6,12 L6,14 L0,12.5 L-6,14 L-6,12 L-2,9 L-2.5,1 L-13,4 L-13,1 L-2.5,-5 Z" stroke="#0b0f14" stroke-width="1.2"/></svg>' +
    '<div class="aircraft-label"><span></span> <span class="aircraft-type"></span></div>'
  el.style.setProperty('--ac', COLOR[a.kind])
  el.querySelector('.aircraft-label span')!.textContent = a.callsign
  return el
}

type Track = { marker: maplibregl.Marker; from: [number, number]; to: [number, number]; start: number; dur: number; updated: number }

// Mutable view state shared by the map callbacks and the prop effects.
type View = {
  map: maplibregl.Map | null
  ready: boolean // airport layers exist in the current style
  imagery: boolean // satellite basemap (false = blank dark fallback)
  types: Record<string, string>
  speaking: string | null
  tracks: Map<string, Track>
  ends: { end: RunwayEnd; runwayId: string; marker: maplibregl.Marker }[]
  runways: RunwayConfig | null
  aircraft: Record<string, Aircraft>
}

function syncRunway(v: View) {
  if (!v.map) return
  const arr = v.runways?.arrival_runways ?? []
  const active = new Set([...arr, ...(v.runways?.departure_runways ?? [])])
  const ids = v.ends.filter((x) => active.has(x.end.designator)).map((x) => x.runwayId)
  for (const { end, marker } of v.ends) marker.getElement().classList.toggle('active', active.has(end.designator))
  if (!v.ready) return
  const on: maplibregl.ExpressionSpecification = ['in', ['get', 'id'], ['literal', ids]]
  if (v.imagery) {
    v.map.setPaintProperty('runway', 'line-opacity', ['case', on, 0.22, 0])
    v.map.setPaintProperty('runway-edge', 'line-color', ['case', on, RUNWAY_ACTIVE, 'rgba(255,255,255,0.18)'])
  } else {
    v.map.setPaintProperty('runway', 'line-color', ['case', on, RUNWAY_ACTIVE, RUNWAY])
  }
  v.map.setFilter('final', ['in', ['get', 'end'], ['literal', arr]])
}

// Which labels win when two would overlap: flying and runway traffic first, parked aircraft last.
const RANK: Record<string, number> = { final: 0, go_around: 0, airborne: 0, landing_roll: 0, takeoff_roll: 0, line_up: 0, at_gate: 2, parked: 2 }
const LABEL_BOX = { w: 110, h: 30 }

// Hide any callsign label that would overlap one already shown (greedy, by rank). Runs every frame: n is ~40.
function declutter(v: View) {
  const map = v.map!
  const shown: maplibregl.Point[] = []
  const rank = (cs: string) => (cs === v.speaking ? -1 : RANK[v.aircraft[cs]?.phase] ?? 1)
  const order = [...v.tracks.entries()].sort(([a], [b]) => rank(a) - rank(b))
  for (const [, tr] of order) {
    const p = map.project(tr.marker.getLngLat())
    const free = shown.every((q) => Math.abs(q.x - p.x) > LABEL_BOX.w || Math.abs(q.y - p.y) > LABEL_BOX.h)
    if (free) shown.push(p)
    const label = tr.marker.getElement().querySelector<HTMLElement>('.aircraft-label')!
    if (label.hidden === free) label.hidden = !free
  }
}

function syncAircraft(v: View) {
  const map = v.map
  if (!map) return
  const now = performance.now()
  for (const [cs, tr] of v.tracks) {
    if (!v.aircraft[cs]) {
      tr.marker.remove()
      v.tracks.delete(cs)
    }
  }
  for (const a of Object.values(v.aircraft)) {
    const to: [number, number] = [a.lon, a.lat]
    let tr = v.tracks.get(a.callsign)
    if (!tr) {
      tr = { marker: new maplibregl.Marker({ element: planeElement(a) }).setLngLat(to).addTo(map), from: to, to, start: now, dur: 0, updated: now }
      v.tracks.set(a.callsign, tr)
    } else if (to[0] !== tr.to[0] || to[1] !== tr.to[1]) {
      const p = tr.marker.getLngLat()
      // glide over the interval between updates (capped) so motion keeps pace with the sim speed
      Object.assign(tr, { from: [p.lng, p.lat], to, start: now, dur: Math.min(now - tr.updated, 2000), updated: now })
    }
    const el = tr.marker.getElement()
    el.style.setProperty('--ac', COLOR[a.kind])
    el.classList.toggle('speaking', a.callsign === v.speaking)
    el.querySelector('.aircraft-type')!.textContent = v.types[a.callsign] ?? ''
    el.querySelector('svg')!.style.transform = `rotate(${a.heading - map.getBearing()}deg)`
  }
}

type Props = {
  airport: Airport
  runways: RunwayConfig | null
  aircraft: Record<string, Aircraft>
  types?: Record<string, string> // ICAO type per callsign
  speaking?: string | null // callsign being spoken now: pulsing ring, label always shown
  inset?: () => { top: number; right: number } // px covered by floating panels, so the airport centres in the rest
}

export function AirportMap({ airport, runways, aircraft, types = {}, speaking = null, inset }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const view = useRef<View>({ map: null, ready: false, imagery: false, types: {}, speaking: null, tracks: new Map(), ends: [], runways: null, aircraft: {} })
  const insetRef = useRef(inset)
  insetRef.current = inset

  // Create the map once per airport.
  useEffect(() => {
    const v = view.current
    const el = container.current!
    let cancelled = false
    let frame = 0
    loadStyle().then((style) => {
      if (cancelled) return
      const { center, zoom, bearing = 0 } = airport.config.map
      const map = new maplibregl.Map({
        container: el, style, center, zoom, bearing,
        dragRotate: false, pitchWithRotate: false, touchPitch: false,
        attributionControl: { compact: false },
      })
      map.touchZoomRotate.disableRotation()
      const pad = () => ({ top: 0, right: 0, bottom: 0, left: 0, ...insetRef.current?.() })
      // config zoom fits the ~1280 px of map left visible at 1920x1080; zoom out to fit smaller screens (1280x720)
      const p = pad()
      map.jumpTo({ center, zoom: zoom + Math.min(0, Math.log2((el.clientWidth - p.right - p.left) / 1280)), padding: p })
      map.on('resize', () => map.jumpTo({ padding: pad() }))
      v.map = map
      let fellBack = style === BLANK
      let loaded = false
      map.once('load', () => (loaded = true))
      const fallBack = () => {
        if (fellBack || loaded) return
        fellBack = true
        v.ready = false
        map.setStyle(BLANK, { diff: false })
      }
      // A later failure (tiles, sprite, glyphs) or a stall before the first full load: switch to the blank style.
      map.on('error', (e: maplibregl.ErrorEvent) => {
        console.warn('Map error:', e.error)
        fallBack()
      })
      const stall = window.setTimeout(fallBack, 10000)
      map.once('remove', () => clearTimeout(stall))
      map.on('style.load', () => {
        v.imagery = !fellBack
        el.classList.toggle('imagery', v.imagery)
        addAirportLayers(map, airport, v.imagery)
        v.ready = true
        syncRunway(v)
      })
      for (const f of airport.geojson.features) {
        const p = f.properties ?? {}
        if (p.kind === 'runway') {
          for (const end of p.ends as RunwayEnd[]) {
            v.ends.push({ end, runwayId: p.id, marker: labelMarker(map, end.threshold, end.designator, 'runway-label') })
          }
        } else if (p.kind === 'taxiway' && p.label !== false && f.geometry.type === 'LineString') {
          const c = f.geometry.coordinates
          const i = Math.floor((c.length - 2) / 2) // middle segment, so the label sits between vertices
          const [a, b] = [c[i], c[i + 1]]
          labelMarker(map, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], p.name, 'taxiway-label')
        }
      }
      syncRunway(v)
      syncAircraft(v)
      const tick = () => {
        const now = performance.now()
        for (const tr of v.tracks.values()) {
          const k = tr.dur ? Math.min((now - tr.start) / tr.dur, 1) : 1
          tr.marker.setLngLat([tr.from[0] + (tr.to[0] - tr.from[0]) * k, tr.from[1] + (tr.to[1] - tr.from[1]) * k])
        }
        declutter(v)
        frame = requestAnimationFrame(tick)
      }
      frame = requestAnimationFrame(tick)
    })
    return () => {
      cancelled = true
      cancelAnimationFrame(frame)
      v.map?.remove()
      Object.assign(v, { map: null, ready: false, imagery: false, tracks: new Map(), ends: [] })
    }
  }, [airport])

  useEffect(() => {
    view.current.runways = runways
    syncRunway(view.current)
  }, [runways])

  useEffect(() => {
    Object.assign(view.current, { aircraft, types, speaking })
    syncAircraft(view.current)
  }, [aircraft, types, speaking])

  return <div ref={container} className="h-full w-full" />
}
