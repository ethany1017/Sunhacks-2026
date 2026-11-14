import type { FeatureCollection } from 'geojson'

// The backend contract: GET /api/airport and the WS /ws event stream.

export type LonLat = [number, number]

export type AirportConfig = {
  placeholder?: boolean
  name: string
  icao: string
  map: { center: LonLat; zoom: number; bearing?: number }
}

export type RunwayEnd = { designator: string; heading: number; threshold: LonLat }

export type Airport = {
  config: AirportConfig
  geojson: FeatureCollection
}

export type Phase =
  | 'approach' | 'landing' | 'rollout' | 'taxi' | 'holding' | 'lineup' | 'takeoff' | 'departed' | 'parked'

type Ev<T extends string, D> = { t: number; seq: number; type: T; callsign: string | null; data: D; reason: string }

export type WeatherData = {
  raw: string; wind_dir: number; wind_speed: number; wind_gust: number | null; visibility_sm: number; source: string
}

export type SimEvent =
  | Ev<'sim_start', { run_id: number; airport: string; icao: string; speed: number }>
  | Ev<'sim_end', Record<string, never>>
  | Ev<'weather', WeatherData>
  | Ev<'runway_change', { runway: string; previous: string | null }>
  | Ev<'aircraft_spawn', { kind: 'arrival' | 'departure'; aircraft_type: string; lat: number; lon: number; heading: number; phase: Phase }>
  | Ev<'position', { lat: number; lon: number; heading: number; phase: Phase; speed_kt: number }>
  | Ev<'proposal', { action: string; runway: string | null; route: string[] | null }>
  | Ev<'ai_decision', { question: string; options: { id: string; label: string }[]; choice: string; explanation: string; source: 'claude' | 'fallback' }>
  | Ev<'safety_check', { approved: boolean; rule: string; action: string }>
  | Ev<'clearance', { text: string; action: string; runway: string | null }>
  | Ev<'audio', { url: string; speaker: 'tower' | 'pilot'; text: string }>
  | Ev<'readback', { transcript: string; matched: boolean }>
  | Ev<'readback_correction', { text: string }>
  | Ev<'sequence', { arrivals: string[]; departures: string[] }>
  | Ev<'alert', { level: 'info' | 'warning' | 'critical'; message: string }>

export type Aircraft = {
  callsign: string
  kind: 'arrival' | 'departure' | 'unknown'
  lat: number
  lon: number
  heading: number
  phase: Phase
}
