import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { SimEvent } from '@/types'
import { clock } from '@/useTower'

type Look = { label: string; text: string; tone: string; badge?: string }
type SafetyCheck = Extract<SimEvent, { type: 'safety_check' }>
// A feed row: one event, plus the safety_check that answered it (proposals and AI decisions).
type Item = { e: SimEvent; safety?: SafetyCheck }

const NEUTRAL = 'border-slate-700/70'
const GOOD = 'border-emerald-700/60'
const BAD = 'border-red-500 bg-red-950/60'
const WARN = 'border-amber-500 bg-amber-950/50'
const words = (s: string | null | undefined) => (s ?? '').replaceAll('_', ' ')

function target(p: Record<string, unknown>) {
  if (p.arrival_runways) return `arrivals ${(p.arrival_runways as string[]).join(', ')} · departures ${(p.departure_runways as string[]).join(', ')}`
  if (p.runway) return `runway ${p.runway}`
  if (p.to) return `to ${p.to}`
  return ''
}

function look(e: SimEvent): Look {
  switch (e.type) {
    case 'sim_start': return { label: 'RUN START', text: `${e.data.airport} (run ${e.data.run_id}, ${e.data.speed}x)`, tone: NEUTRAL }
    case 'sim_end': return { label: 'RUN END', text: '', tone: NEUTRAL }
    case 'run_start': return { label: 'RUN START', text: `${e.data.level_name}, ${e.data.controller_label}, seed ${e.data.seed}`, tone: NEUTRAL }
    case 'controller_switch': return { label: 'CONTROLLER', text: `Now controlled by ${e.data.label}`, tone: 'border-sky-600' }
    case 'aircraft_exit': return { label: e.data.reason.toUpperCase(), text: '', tone: e.data.reason === 'diverted' ? WARN : NEUTRAL }
    case 'referee_fail': return { label: `REFEREE FAIL · ${words(e.data.rule)}`, text: e.data.reason, tone: BAD }
    case 'referee_result':
      return { label: `REFEREE ${e.data.verdict}`, text: `${e.data.fails.length} fail(s)`, tone: e.data.verdict === 'PASS' ? GOOD : BAD }
    case 'weather': return { label: 'WEATHER', text: e.data.metar, tone: NEUTRAL }
    case 'runway_change': return { label: 'RUNWAY CONFIG', text: e.data.runway, tone: 'border-emerald-600' }
    case 'aircraft_spawn': return { label: e.data.kind.toUpperCase(), text: `${e.data.aircraft_type}, ${words(e.data.phase)}`, tone: NEUTRAL }
    case 'proposal': return { label: 'PROPOSAL', text: `${words(e.data.action)} ${target(e.data.params ?? {})}`, tone: NEUTRAL }
    case 'ai_decision': return { label: `AI DECISION · ${DECISION_KINDS[e.data.kind] ?? words(e.data.kind)}`, text: e.data.explanation, tone: 'border-violet-500', badge: e.data.source === 'claude' ? 'Claude' : 'Fallback' }
    case 'safety_check':
      return e.data.approved
        ? { label: 'SAFETY OK', text: `${words(e.data.action)} approved (${words(e.data.rule)})`, tone: GOOD }
        : { label: `SAFETY REJECTED · ${words(e.data.rule)}`, text: `${words(e.data.action)} blocked`, tone: BAD }
    case 'clearance': return { label: 'CLEARANCE', text: `“${e.data.text}”`, tone: 'border-slate-400' }
    case 'audio': return { label: `AUDIO ${e.data.speaker.toUpperCase()}`, text: e.data.text, tone: NEUTRAL }
    case 'readback':
      return e.data.matched
        ? { label: 'READBACK OK', text: `“${e.data.transcript}”`, tone: GOOD }
        : { label: 'READBACK WRONG', text: `“${e.data.transcript}”`, tone: BAD }
    case 'readback_correction': return { label: 'CORRECTION', text: `Heard “${e.data.transcript}”: read it back again`, tone: WARN }
    case 'sequence': return { label: `SEQUENCE · RWY ${e.data.runway}`, text: e.data.order.join('  →  '), tone: NEUTRAL }
    case 'alert':
      return {
        label: `ALERT ${e.data.level.toUpperCase()}`, text: e.data.message,
        tone: e.data.level === 'critical' ? BAD : e.data.level === 'warning' ? WARN : NEUTRAL,
      }
    default: return { label: (e as SimEvent).type, text: '', tone: NEUTRAL }
  }
}

const DECISION_KINDS: Record<string, string> = {
  runway_flow: 'Runway flow', arrival_runway: 'Arrival runway', departure_gap: 'Next departure',
}
const LABEL_COLOR: Record<string, string> = {
  [BAD]: 'text-red-300', [WARN]: 'text-amber-300', [GOOD]: 'text-emerald-400', 'border-violet-500': 'text-violet-300',
}

// Pair each safety_check with the proposal it judged (same aircraft and action, just before it), and show it on the AI
// decision that led there too. Events arrive newest first.
function group(events: SimEvent[]): Item[] {
  const out: Item[] = []
  const chrono = [...events].reverse()
  for (let i = 0; i < chrono.length; i++) {
    const e = chrono[i]
    if (e.type === 'safety_check') {
      const prop = out.slice(-3).reverse().find((it) => it.e.type === 'proposal' && !it.safety && it.e.callsign === e.callsign && it.e.data.action === e.data.action)
      const dec = out.slice(-4).reverse().find((it) => it.e.type === 'ai_decision' && !it.safety && it.e.callsign === e.callsign)
      if (dec) dec.safety = e
      if (prop) {
        prop.safety = e
        continue
      }
    }
    out.push({ e })
  }
  return out.reverse()
}

function SafetyLine({ s }: { s: SafetyCheck }) {
  return (
    <div className={`text-[18px] leading-snug ${s.data.approved ? 'text-emerald-300' : 'font-semibold text-red-300'}`}>
      {s.data.approved ? '✔ Safety engine approved' : `✖ Safety engine REJECTED (${words(s.data.rule)})${s.reason ? `: ${s.reason}` : ''}`}
    </div>
  )
}

function Row({ e, safety }: Item) {
  let l = look(e)
  if (e.type === 'proposal' && safety) {
    l = safety.data.approved
      ? { ...l, label: 'PROPOSAL · SAFETY OK', tone: GOOD }
      : { ...l, label: `SAFETY REJECTED · ${words(safety.data.rule)}`, tone: BAD }
  }
  return (
    <Card className={`gap-1 rounded-lg border-l-4 bg-slate-900/80 px-4 py-3 shadow-none ${l.tone}`}>
      <div className="flex flex-wrap items-center gap-x-3 text-lg">
        <span className="font-mono text-slate-400 tabular-nums">{clock(e.t)}</span>
        <span className={`font-semibold tracking-wide ${LABEL_COLOR[l.tone] ?? 'text-slate-300'}`}>{l.label}</span>
        {e.callsign && <span className="font-mono font-semibold text-slate-100">{e.callsign}</span>}
        {l.badge && (
          <Badge className={`ml-auto text-sm ${l.badge === 'Claude' ? 'bg-violet-600 text-white' : 'bg-slate-600 text-white'}`}>{l.badge}</Badge>
        )}
      </div>
      {e.type === 'ai_decision' && (
        <ul className="text-[18px] leading-snug">
          {e.data.options.map((o) => (
            <li key={o.id} className={o.id === e.data.choice ? 'font-semibold text-violet-200' : 'text-slate-500'}>
              {o.id === e.data.choice ? '▶ ' : '· '}{o.label ?? o.id}
            </li>
          ))}
        </ul>
      )}
      {l.text && <div className={`text-[19px] leading-snug ${e.type === 'audio' ? 'text-slate-400' : 'text-slate-100'}`}>{l.text}</div>}
      {e.reason && e.reason !== l.text && e.reason !== (e.data as { text?: string }).text && <div className="text-[18px] leading-snug text-slate-400">{e.reason}</div>}
      {safety && <SafetyLine s={safety} />}
    </Card>
  )
}

export function Feed({ events }: { events: SimEvent[] }) {
  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-3">
      {events.length === 0 && <p className="p-4 text-xl text-slate-500">Waiting for events…</p>}
      {group(events).map((it) => <Row key={it.e.seq} {...it} />)}
    </div>
  )
}
