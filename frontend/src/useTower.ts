import { useEffect, useState } from 'react'
import type { Aircraft, SimEvent, WeatherData } from '@/types'

const FEED_CAP = 300

export const clock = (t: number) => {
  const s = Math.max(0, Math.floor(t))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export type TowerState = {
  feed: SimEvent[] // newest first
  aircraft: Record<string, Aircraft>
  weather: WeatherData | null
  runway: string | null
  t: number
}

export const INITIAL: TowerState = { feed: [], aircraft: {}, weather: null, runway: null, t: 0 }

// Applies a batch of events to a copy of `prev`. Mutates only the copy, so a replay of thousands of
// events costs one state update.
export function applyEvents(prev: TowerState, events: SimEvent[]): TowerState {
  let s: TowerState = { ...prev, aircraft: { ...prev.aircraft } }
  const fresh: SimEvent[] = []
  for (const e of events) {
    if (e.type === 'sim_start') {
      s = { ...INITIAL, aircraft: {} }
      fresh.length = 0
      prev = INITIAL
    }
    s.t = e.t
    const cs = e.callsign
    switch (e.type) {
      case 'weather':
        s.weather = e.data
        break
      case 'runway_change':
        s.runway = e.data.runway
        break
      case 'aircraft_spawn':
        if (cs) s.aircraft[cs] = { callsign: cs, ...e.data }
        break
      case 'position':
        if (!cs) break
        if (e.data.phase === 'departed') delete s.aircraft[cs]
        else s.aircraft[cs] = { callsign: cs, kind: s.aircraft[cs]?.kind ?? 'unknown', ...e.data }
        break
    }
    if (e.type !== 'position') fresh.push(e)
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
        setState(INITIAL)
        setConnected(true)
      }
      sock.onmessage = (m) => {
        try {
          buffer.push(JSON.parse(m.data))
        } catch {
          return // ignore a malformed message rather than break the dashboard
        }
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
