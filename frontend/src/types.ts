import type { FeatureCollection } from 'geojson'

// The backend contract: the WS /ws event stream. The airport layout arrives as an `airport` event.

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
  graph: unknown // routing graph; not drawn by the dashboard
}

export type Phase = string // the simulator's phase names (final, taxi_out, at_gate, ...)
export type Kind = 'arrival' | 'departure' | 'vehicle' | 'unknown'

type Ev<T extends string, D> = { t: number; seq: number; type: T; callsign: string | null; data: D; reason: string }

export type WeatherData = {
  metar: string; wind_dir: number; wind_kt: number; gust_kt: number | null; vis_sm: number; wx: string | null; temp_f: number; t?: number
}

export type RunwayConfig = { arrival_runways: string[]; departure_runways: string[]; runway: string }

// The real-world METAR (GET /api/weather/live). Display only.
export type LiveWeather = {
  source: 'live' | 'cache' | 'unavailable'; icao?: string; metar?: string; wind_dir?: number | string | null
  wind_kt?: number | null; gust_kt?: number | null; vis_sm?: number | string | null; obs_time?: string | null
}

export type Decision = { kind: string; options: { id: string; label?: string }[]; default: string; choice?: string }

export type RefereeFail = { rule: string; callsigns: string[]; reason: string; t: number }

export type Metrics = {
  verdict: 'RUNNING' | 'PASS' | 'FAIL'; controller: string; fails: number; completed: number; avg_delay_s: number
  total_delay_s: number; go_arounds: number; diversions: number; readback_errors_caught: number
  readback_errors_accepted: number; runway_operations: number
}

// Totals of one controller in one section of the latest evaluation (backend/app/evaluate.py summary()).
export type EvalTotals = {
  runs: number; passed: number; pass_rate: number; fails: number; fails_by_rule: Record<string, number>; avg_delay_s: number
  go_arounds: number; diversions: number; readback_errors_caught: number; readback_errors_missed: number; completed: number
}
export type Comparison = {
  generated: string; human_impl: string | null
  sections: Partial<Record<'training' | 'heldout' | 'practice', Partial<Record<'human' | 'agent', EvalTotals>>>>
}

export type RunInfo = { level: string; level_name: string; controller: string; controller_label: string; seed: number }

export type SimEvent =
  | Ev<'run_start', RunInfo>
  | Ev<'controller_switch', { from: string; to: string; label: string }>
  | Ev<'aircraft_exit', { reason: 'departed' | 'diverted' | 'parked'; gate?: string }>
  | Ev<'metrics', Metrics | { comparison: Comparison }>
  | Ev<'referee_fail', RefereeFail>
  | Ev<'referee_result', { verdict: 'PASS' | 'FAIL'; fails: RefereeFail[]; score: Record<string, unknown> }>
  | Ev<'airport', Airport>
  | Ev<'sim_start', { run_id: number; airport: string; icao: string; speed: number }>
  | Ev<'sim_end', { why?: string }>
  | Ev<'weather', WeatherData>
  | Ev<'runway_change', RunwayConfig>
  | Ev<'aircraft_spawn', { kind: Kind; aircraft_type: string; lat: number; lon: number; heading: number; phase: Phase }>
  | Ev<'position', { lat: number; lon: number; heading: number; phase: Phase; speed_kt: number; kind?: Kind; altitude_ft?: number }>
  | Ev<'proposal', { action: string; params?: Record<string, unknown>; decision?: Decision }>
  | Ev<'ai_decision', Decision & { choice: string; explanation: string; source: 'claude' | 'fallback' }>
  | Ev<'safety_check', { approved: boolean; rule: string; action: string }>
  | Ev<'clearance', { text: string; action: string; params?: Record<string, unknown> }>
  | Ev<'audio', { url: string; speaker: 'tower' | 'pilot'; text: string }>
  | Ev<'readback', { transcript: string; matched: boolean }>
  | Ev<'readback_correction', { transcript: string }>
  | Ev<'sequence', { runway: string; order: string[] }>
  | Ev<'alert', { level: 'info' | 'warning' | 'critical'; message: string; source?: string }>

export type Aircraft = {
  callsign: string
  kind: Kind
  lat: number
  lon: number
  heading: number
  phase: Phase
  altitude_ft?: number
}
