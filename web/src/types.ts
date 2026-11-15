// Message shapes from docs/API_CONTRACT.md
export type LonLat = [number, number];

export interface Aircraft {
  id: string;
  callsign: string;
  type: string;
  kind: "arrival" | "departure" | "vehicle";
  airline?: string;
  lon: number;
  lat: number;
  heading_deg: number;
  speed_mps: number;
  phase: string;
  status_text?: string;
  wingspan_m: number;
  length_m: number;
  gate?: string | null;
  runway?: string | null;
  route?: LonLat[];
  planned_route?: LonLat[];
  hold?: { runway: string; lon: number; lat: number } | null;
  eta_s?: number | null;
  altitude_ft?: number;
}

export interface OccupancyWindow {
  callsign: string;
  start_s: number;
  end_s: number;
  kind: "occupied" | "reserved";
}
export interface Runway {
  id: string;
  name: string;
  status: "free" | "reserved" | "occupied";
  owner: string | null;
  occupancy: OccupancyWindow[];
}
export interface Clearance {
  id: string;
  callsign: string;
  action: string;
  text: string;
  state: "issued" | "acknowledged" | "complete" | "amended" | "cancelled";
  issued_t: number;
  ack_t: number | null;
  done_t: number | null;
}
export interface Weather {
  t?: number;
  active: boolean;
  metar: string;
  wind_dir: number | null;
  wind_kt: number | null;
  gust_kt: number | null;
  vis_sm: number | null;
  wx: string | null;
  temp_f?: number | null;
  haboob_progress: number;
}
export interface Decision {
  id: string;
  t: number;
  callsign: string;
  action: string;
  summary: string;
  facts: string[];
  rules: string[];
  alternatives: { option: string; reason: string }[];
  next_review_s: number | null;
  voice_id?: string | null;
}
export interface VoiceLine {
  id: string;
  t: number;
  speaker: "agent" | "pilot";
  callsign: string;
  text: string;
  status: "ok" | "corrected" | "pending";
}
export interface Score {
  separation_losses: number;
  runway_conflicts: number;
  deadlocks: number;
  readback_errors_accepted: number;
  completed: number;
  avg_delay_s: number;
  throughput_per_10min: number;
}
export interface AgentInfo {
  status: "idle" | "thinking";
  controller: string;
}
export interface ScenarioInfo {
  name: string;
  duration_s: number;
  haboob_triggered: boolean;
  start_utc?: string;
}

export interface Message {
  type: "snapshot" | "delta";
  t: number;
  speed?: number;
  paused?: boolean;
  agent?: AgentInfo;
  scenario?: ScenarioInfo;
  weather?: Weather;
  aircraft?: Aircraft[];
  runways?: Runway[];
  clearances?: Clearance[];
  decisions?: Decision[];
  voice?: VoiceLine[];
  score?: Score;
  removed?: string[];
  events?: unknown[];
}

export interface GraphFeature {
  type: "Feature";
  properties: Record<string, any>;
  geometry: { type: string; coordinates: any };
}
export interface Graph {
  type: "FeatureCollection";
  features: GraphFeature[];
  meta?: { center: LonLat; bounds: [number, number, number, number] };
}
