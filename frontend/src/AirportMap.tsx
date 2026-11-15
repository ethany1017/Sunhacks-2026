import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useRef } from 'react'
import type { Aircraft, Airport, RunwayEnd } from '@/types'

// MapLibre locates its worker relative to its own module, which Vite's dependency bundling breaks.
maplibregl.setWorkerUrl(workerUrl)

const STYLE_URL = 'https://tiles.openfreemap.org/styles/dark'
const BLANK: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b0f14' } }],
}
const COLOR = { arrival: '#38bdf8', departure: '#fbbf24', vehicle: '#94a3b8', unknown: '#e5e7eb' }
const RUNWAY = '#4b5563'
const RUNWAY_ACTIVE = '#22c55e'

async function loadStyle(): Promise<maplibregl.StyleSpecification> {
  try {
    const res = await fetch(STYLE_URL, { signal: AbortSignal.timeout(4000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (err) {
    console.warn('Basemap unavailable, using blank style:', err)
    return BLANK
  }
}

function addAirportLayers(map: maplibregl.Map, geojson: Airport['geojson']) {
  map.addSource('airport', { type: 'geojson', data: geojson })
  const kind = (k: string) => ['==', ['get', 'kind'], k] as maplibregl.FilterSpecification
  map.addLayer({
    id: 'taxiway', type: 'line', source: 'airport', filter: kind('taxiway'),
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#a16207', 'line-opacity': 0.75, 'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, 1.5, 17, 16] },
  })
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
    '<div class="aircraft-label"></div>'
  el.style.setProperty('--ac', COLOR[a.kind])
  el.querySelector('.aircraft-label')!.textContent = a.callsign
  return el
}

type Track = { marker: maplibregl.Marker; from: [number, number]; to: [number, number]; start: number; dur: number; updated: number }

// Mutable view state shared by the map callbacks and the prop effects.
type View = {
  map: maplibregl.Map | null
  ready: boolean // airport layers exist in the current style
  tracks: Map<string, Track>
  ends: { end: RunwayEnd; runwayId: string; marker: maplibregl.Marker }[]
  runway: string | null
  aircraft: Record<string, Aircraft>
}

function syncRunway(v: View) {
  if (!v.map) return
  const activeId = v.ends.find((x) => x.end.designator === v.runway)?.runwayId ?? ''
  for (const { end, marker } of v.ends) marker.getElement().classList.toggle('active', end.designator === v.runway)
  if (v.ready) v.map.setPaintProperty('runway', 'line-color', ['case', ['==', ['get', 'id'], activeId], RUNWAY_ACTIVE, RUNWAY])
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
    el.querySelector('svg')!.style.transform = `rotate(${a.heading - map.getBearing()}deg)`
  }
}

export function AirportMap({ airport, runway, aircraft }: { airport: Airport; runway: string | null; aircraft: Record<string, Aircraft> }) {
  const container = useRef<HTMLDivElement>(null)
  const view = useRef<View>({ map: null, ready: false, tracks: new Map(), ends: [], runway: null, aircraft: {} })

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
      v.map = map
      let fellBack = style === BLANK
      let loaded = false
      map.once('load', () => (loaded = true))
      // A later failure (tiles, sprite, glyphs) before the first full load: switch to the blank style.
      map.on('error', (e: maplibregl.ErrorEvent) => {
        console.warn('Map error:', e.error)
        if (!fellBack && !loaded) {
          fellBack = true
          v.ready = false
          map.setStyle(BLANK, { diff: false })
        }
      })
      map.on('style.load', () => {
        addAirportLayers(map, airport.geojson)
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
        frame = requestAnimationFrame(tick)
      }
      frame = requestAnimationFrame(tick)
    })
    return () => {
      cancelled = true
      cancelAnimationFrame(frame)
      v.map?.remove()
      Object.assign(v, { map: null, ready: false, tracks: new Map(), ends: [] })
    }
  }, [airport])

  useEffect(() => {
    view.current.runway = runway
    syncRunway(view.current)
  }, [runway])

  useEffect(() => {
    view.current.aircraft = aircraft
    syncAircraft(view.current)
  }, [aircraft])

  return <div ref={container} className="h-full w-full" />
}
