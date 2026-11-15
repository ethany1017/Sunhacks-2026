import { useEffect, useState } from 'react'
import { audioConnected, onAudio } from '@/Voice'
import type { Aircraft, Airport, Comparison, Metrics, RefereeFail, RunInfo, RunwayConfig, SimEvent, WeatherData } from '@/types'

const FEED_CAP = 300

export const clock = (t: number) => {
  const s = Math.max(0, Math.floor(t))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export type TowerState = {
  airport: Airport | null // from the latest `airport` event
  feed: SimEvent[] // newest first
  aircraft: Record<string, Aircraft>
  weather: WeatherData | null
  runway: string | null // one-line label of the active runway configuration
  runways: RunwayConfig | null
  t: number
  run: RunInfo | null // level and the active controller
  verdict: 'RUNNING' | 'PASS' | 'FAIL'
  fails: RefereeFail[]
  metrics: Metrics | null
  comparison: Comparison | null // latest Agent vs Human evaluation (a `metrics` event with `comparison`)
  ended: boolean
}

export const INITIAL: TowerState = {
  airport: null, feed: [], aircraft: {}, weather: null, runway: null, runways: null, t: 0,
  run: null, verdict: 'RUNNING', fails: [], metrics: null, comparison: null, ended: false,
}

// Applies a batch of events to a copy of `prev`. Mutates only the copy, so a replay of thousands of
// events costs one state update.
export function applyEvents(prev: TowerState, events: SimEvent[]): TowerState {
  let s: TowerState = { ...prev, aircraft: { ...prev.aircraft }, fails: [...prev.fails] }
  const fresh: SimEvent[] = []
  for (const e of events) {
    if (e.type === 'airport') {
      s.airport = e.data // not a feed item; the map redraws from it
      continue
    }
    if (e.type === 'sim_start' || e.type === 'run_start') {
      s = { ...INITIAL, aircraft: {}, fails: [], airport: s.airport } // keep the map until the run's airport event
      fresh.length = 0
      prev = INITIAL
    }
    if (e.type === 'audio') continue // played by Voice (onAudio); not a feed item, and its t lags the clock
    if (e.type === 'metrics' && 'comparison' in e.data) {
      s.comparison = e.data.comparison // not part of the live run: no clock, no feed item
      continue
    }
    s.t = e.t
    const cs = e.callsign
    switch (e.type) {
      case 'weather':
        s.weather = e.data
        break
      case 'runway_change':
        s.runway = e.data.runway
        s.runways = e.data
        break
      case 'aircraft_spawn':
        if (cs) s.aircraft[cs] = { callsign: cs, ...e.data }
        break
      case 'position':
        if (!cs) break
        if (e.data.phase === 'departed') delete s.aircraft[cs]
        else s.aircraft[cs] = { callsign: cs, ...e.data, kind: e.data.kind ?? s.aircraft[cs]?.kind ?? 'unknown' }
        break
      case 'aircraft_exit':
        if (cs && e.data.reason !== 'parked') delete s.aircraft[cs]
        break
      case 'run_start':
        s.run = e.data
        break
      case 'controller_switch':
        if (s.run) s.run = { ...s.run, controller: e.data.to, controller_label: e.data.label }
        break
      case 'metrics':
        s.metrics = e.data as Metrics // comparison metrics were handled above
        if (s.verdict === 'RUNNING') s.verdict = s.metrics.verdict
        break
      case 'referee_fail':
        s.fails.push(e.data)
        s.verdict = 'FAIL'
        break
      case 'referee_result':
        s.verdict = e.data.verdict
        break
      case 'sim_end':
        s.ended = true
        break
    }
    if (e.type !== 'position' && e.type !== 'metrics') fresh.push(e)
  }
  s.feed = fresh.length ? fresh.reverse().concat(prev.feed).slice(0, FEED_CAP) : prev.feed
  return s
}

// Read-only WebSocket client: reconnects forever, resets on every new connection (the server
// replays the run), and flushes buffered events once per animation frame.
export function useTower() {
  const [state, setState] = useState<TowerState>(INITIAL)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let ws: WebSocket | null = null
    let stopped = false
    let retry = 0
    let delay = 250
    let frame = 0
    let buffer: SimEvent[] = []

    const flush = () => {
      frame = 0
      const batch = buffer
      buffer = []
      setState((prev) => applyEvents(prev, batch))
    }

    const connect = () => {
      const sock = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`)
      ws = sock
      sock.onopen = () => {
        delay = 250
        buffer = []
        audioConnected()
        setState(INITIAL)
        setConnected(true)
      }
      sock.onmessage = (m) => {
        let e: SimEvent
        try {
          e = JSON.parse(m.data)
        } catch {
          return // ignore a malformed message rather than break the dashboard
        }
        buffer.push(e)
        if (e.type === 'audio') onAudio(e)
        else if (e.type === 'run_start') audioConnected() // a reset: drop the previous run's speech
        if (!frame) frame = requestAnimationFrame(flush)
      }
      sock.onclose = () => {
        if (ws !== sock) return
        setConnected(false)
        if (stopped) return
        retry = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 3000)
      }
    }

    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      cancelAnimationFrame(frame)
      ws?.close()
    }
  }, [])

  return { ...state, connected }
}
