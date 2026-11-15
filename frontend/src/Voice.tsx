import { useSyncExternalStore } from 'react'
import type { SimEvent } from '@/types'

// Plays `audio` events one at a time, in order. Lines made before this WebSocket connection opened are history (the
// replay after a reload) and never play; lines older than MAX_AGE_S (wall clock, stamped by the backend) are skipped so
// speech never lags far behind the map; the queue keeps at most MAX_QUEUE lines. A playback error or a stalled file skips to the next line.
const MAX_AGE_S = 10
const MAX_QUEUE = 3
const LOAD_MS = 8_000 // a file that hasn't loaded by then is skipped; once loaded, its duration + 2 s
const KEY = 'towerzero.sound' // 'on' once the user enabled sound this session

type Line = { url: string; speaker: string; callsign: string | null; text: string; wall: number }
type Snap = { on: boolean; blocked: boolean; now: Line | null }

const el = typeof Audio === 'undefined' ? null : new Audio()
let snap: Snap = { on: sessionStorage.getItem(KEY) === 'on', blocked: false, now: null }
let queue: Line[] = []
let stall = 0
let since = Infinity // wall time of the current WebSocket connection; nothing plays before the first one opens
const subs = new Set<() => void>()
const set = (p: Partial<Snap>) => {
  snap = { ...snap, ...p }
  subs.forEach((f) => f())
}

function next() {
  clearTimeout(stall)
  const fresh = Date.now() / 1000 - MAX_AGE_S
  queue = queue.filter((l) => l.wall >= fresh)
  const line = queue.shift() ?? null
  set({ now: line })
  if (!line || !el) return
  const done = () => {
    if (snap.now === line) next() // once per line, whichever of ended/error/stall/rejection comes first
  }
  el.onended = el.onerror = done
  el.onloadedmetadata = () => {
    clearTimeout(stall)
    if (snap.now === line) stall = window.setTimeout(done, (Number.isFinite(el.duration) ? el.duration * 1000 : 30_000) + 2000)
  }
  el.src = line.url
  stall = window.setTimeout(done, LOAD_MS)
  el.play().catch((err: DOMException) => {
    if (err.name === 'NotAllowedError') { // autoplay policy: drop the queue and wait for a click
      queue = []
      set({ blocked: true })
    }
    done()
  })
}

// Called by the WebSocket client when a connection opens: what follows first is the run's history.
export function audioConnected() {
  since = Date.now() / 1000 - 0.5 // ponytail: same-machine clocks assumed (backend and browser on one laptop)
}

// Called by the WebSocket client for every `audio` event.
export function onAudio(e: Extract<SimEvent, { type: 'audio' }>) {
  const d = e.data
  if (!snap.on || snap.blocked || !d.wall || d.wall < since || d.wall < Date.now() / 1000 - MAX_AGE_S) return
  queue.push({ url: d.url, speaker: d.speaker, callsign: e.callsign, text: d.text, wall: d.wall })
  if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE)
  if (!snap.now) next()
}

function toggle() {
  const on = !snap.on || snap.blocked // the click is the user gesture the browser wants
  sessionStorage.setItem(KEY, on ? 'on' : 'off')
  queue = []
  clearTimeout(stall)
  el?.pause()
  set({ on, blocked: false, now: null })
}

const subscribe = (f: () => void) => {
  subs.add(f)
  return () => subs.delete(f)
}

// Callsign of the line being spoken right now, or null.
export function useSpeaking(): string | null {
  return useSyncExternalStore(subscribe, () => snap).now?.callsign ?? null
}

export function VoicePanel() {
  const s = useSyncExternalStore(subscribe, () => snap)
  const live = s.on && !s.blocked
  return (
    <div className="ml-auto flex min-w-0 items-center gap-3">
      {s.now && (
        <div className="flex min-w-0 items-baseline gap-2 text-lg" data-testid="now-speaking">
          <span className="shrink-0 animate-pulse font-semibold tracking-widest text-emerald-400">
            ◉ {s.now.speaker === 'tower' ? 'TOWER' : 'PILOT'}
          </span>
          {s.now.callsign && <span className="shrink-0 font-mono font-semibold text-slate-100">{s.now.callsign}</span>}
          <span className="truncate text-slate-300">“{s.now.text}”</span>
        </div>
      )}
      <button
        type="button"
        onClick={toggle}
        className={`shrink-0 rounded-md border-2 px-3 py-1 text-base font-semibold tracking-wide ${live ? 'border-emerald-500 text-emerald-300' : 'border-amber-400 text-amber-300'}`}
      >
        {live ? 'SOUND ON · Mute' : s.blocked ? 'Click to enable sound' : 'SOUND OFF · Enable'}
      </button>
    </div>
  )
}
