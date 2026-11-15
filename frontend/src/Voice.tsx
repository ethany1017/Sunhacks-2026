import { useSyncExternalStore } from 'react'
import type { SimEvent } from '@/types'

// Plays `audio` events one at a time, in order. Lines made before this WebSocket connection opened or before the
// current run started are history (a replay or the previous run) and never play. At the 10x demo pace one spoken line
// spans ~40 sim s, so the voice keeps to the newest exchange only: a tower line replaces whatever is queued, its pilot
// readback joins it, a readback whose clearance was skipped is dropped, and a tower line older than MAX_AGE_S (wall
// clock, stamped by the backend) is skipped. A playback error or a stalled file skips to the next line.
const MAX_AGE_S = 4 // measured on Light Rain 25 at 10x: median ~9 sim s from the event to the start of its speech
const LOAD_MS = 8_000 // a file that hasn't loaded by then is skipped; once loaded, its duration + 2 s
const KEY = 'towerzero.sound' // 'on' once the user enabled sound this session

type Line = { url: string; speaker: string; callsign: string | null; text: string; wall: number; clearance: unknown }
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
  const tower = queue.filter((l) => l.speaker === 'tower' && l.wall >= fresh)
  // a readback plays only right after its own clearance (the one just spoken, or one still queued)
  queue = queue.filter((l) => tower.includes(l) || (l.speaker !== 'tower' && (l.clearance === snap.now?.clearance || tower.some((t) => t.clearance === l.clearance))))
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

function silence() {
  queue = []
  clearTimeout(stall)
  el?.pause()
}

// Called by the WebSocket client when a connection opens (what follows first is the run's history) and on every
// `run_start` (a reset): stop speaking and ignore any line made before now.
export function audioConnected() {
  since = Date.now() / 1000 - 0.5 // ponytail: same-machine clocks assumed (backend and browser on one laptop)
  silence()
  if (snap.now) set({ now: null })
}

// Called by the WebSocket client for every `audio` event.
export function onAudio(e: Extract<SimEvent, { type: 'audio' }>) {
  const d = e.data
  if (!snap.on || snap.blocked || !d.wall || d.wall < since || d.wall < Date.now() / 1000 - MAX_AGE_S) return
  const line = { url: d.url, speaker: d.speaker, callsign: e.callsign, text: d.text, wall: d.wall, clearance: d.clearance_id }
  if (d.speaker === 'tower') queue = [line] // the newest exchange replaces older ones
  else if (d.clearance_id != null && (queue[queue.length - 1] ?? snap.now)?.clearance === d.clearance_id) queue.push(line)
  else return // its clearance was skipped or a newer one is queued: no readback out of context
  if (!snap.now) next()
}

function toggle() {
  const on = !snap.on || snap.blocked // the click is the user gesture the browser wants
  sessionStorage.setItem(KEY, on ? 'on' : 'off')
  silence()
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
