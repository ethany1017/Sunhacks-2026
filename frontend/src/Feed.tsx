import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { SimEvent } from '@/types'
import { clock } from '@/useTower'

type Look = { label: string; text: string; tone: string; badge?: string }

const NEUTRAL = 'border-slate-700/70'
const GOOD = 'border-emerald-700/60'
const BAD = 'border-red-500 bg-red-950/60'
const WARN = 'border-amber-500 bg-amber-950/50'

function look(e: SimEvent): Look {
  switch (e.type) {
    case 'sim_start': return { label: 'RUN START', text: `${e.data.airport} (run ${e.data.run_id}, ${e.data.speed}x)`, tone: NEUTRAL }
    case 'sim_end': return { label: 'RUN END', text: '', tone: NEUTRAL }
    case 'run_start': return { label: 'RUN START', text: `${e.data.level_name}, ${e.data.controller_label}, seed ${e.data.seed}`, tone: NEUTRAL }
    case 'controller_switch': return { label: 'CONTROLLER', text: `Now controlled by ${e.data.label}`, tone: 'border-sky-600' }
    case 'aircraft_exit': return { label: e.data.reason.toUpperCase(), text: '', tone: e.data.reason === 'diverted' ? WARN : NEUTRAL }
    case 'referee_fail': return { label: `REFEREE FAIL · ${e.data.rule.replaceAll('_', ' ')}`, text: e.data.reason, tone: BAD }
    case 'referee_result':
      return { label: `REFEREE ${e.data.verdict}`, text: `${e.data.fails.length} fail(s)`, tone: e.data.verdict === 'PASS' ? GOOD : BAD }
    case 'weather': return { label: 'WEATHER', text: e.data.metar, tone: NEUTRAL }
    case 'runway_change':
      return { label: 'RUNWAY', text: e.data.runway, tone: 'border-emerald-600' }
    case 'aircraft_spawn': return { label: e.data.kind.toUpperCase(), text: `${e.data.aircraft_type}, ${e.data.phase.replaceAll('_', ' ')}`, tone: NEUTRAL }
    case 'proposal':
      return { label: 'PROPOSAL', text: `${e.data.action.replaceAll('_', ' ')}${e.data.runway ? ` runway ${e.data.runway}` : ''}`, tone: NEUTRAL }
    case 'ai_decision': {
      const pick = e.data.options.find((o) => o.id === e.data.choice)?.label ?? e.data.choice
      return { label: 'AI DECISION', text: `${e.data.question} → ${pick}. ${e.data.explanation}`, tone: 'border-sky-700', badge: e.data.source === 'claude' ? 'Claude' : 'Fallback' }
    }
    case 'safety_check':
      return e.data.approved
        ? { label: 'SAFETY OK', text: `${e.data.action.replaceAll('_', ' ')} approved (${e.data.rule})`, tone: GOOD }
        : { label: 'SAFETY REJECTED', text: `${e.data.action.replaceAll('_', ' ')} rejected (${e.data.rule})`, tone: BAD }
    case 'clearance': return { label: 'CLEARANCE', text: e.data.text, tone: 'border-slate-500' }
    case 'audio': return { label: `AUDIO ${e.data.speaker.toUpperCase()}`, text: e.data.text, tone: NEUTRAL }
    case 'readback':
      return e.data.matched
        ? { label: 'READBACK', text: e.data.transcript, tone: GOOD }
        : { label: 'READBACK MISMATCH', text: e.data.transcript, tone: BAD }
    case 'readback_correction': return { label: 'CORRECTION', text: e.data.text, tone: WARN }
    case 'sequence':
      return { label: 'SEQUENCE', text: `Arrivals: ${e.data.arrivals.join(', ') || 'none'} · Departures: ${e.data.departures.join(', ') || 'none'}`, tone: NEUTRAL }
    case 'alert':
      return {
        label: `ALERT ${e.data.level.toUpperCase()}`, text: e.data.message,
        tone: e.data.level === 'critical' ? BAD : e.data.level === 'warning' ? WARN : NEUTRAL,
      }
    default: return { label: (e as SimEvent).type, text: '', tone: NEUTRAL }
  }
}

const LABEL_COLOR: Record<string, string> = {
  [BAD]: 'text-red-300', [WARN]: 'text-amber-300', [GOOD]: 'text-emerald-400',
}

export function Feed({ events }: { events: SimEvent[] }) {
  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-3">
      {events.length === 0 && <p className="p-4 text-xl text-slate-500">Waiting for events…</p>}
      {events.map((e) => {
        const l = look(e)
        return (
          <Card key={e.seq} className={`gap-1 rounded-lg border-l-4 bg-slate-900/80 px-4 py-3 shadow-none ${l.tone}`}>
            <div className="flex items-center gap-3 text-lg">
              <span className="font-mono text-slate-400 tabular-nums">{clock(e.t)}</span>
              <span className={`font-semibold tracking-wide ${LABEL_COLOR[l.tone] ?? 'text-slate-300'}`}>{l.label}</span>
              {e.callsign && <span className="font-mono font-semibold text-slate-100">{e.callsign}</span>}
              {l.badge && (
                <Badge className={`ml-auto text-sm ${l.badge === 'Claude' ? 'bg-violet-600 text-white' : 'bg-slate-600 text-white'}`}>{l.badge}</Badge>
              )}
            </div>
            {l.text && <div className={`text-[19px] leading-snug ${e.type === 'audio' ? 'text-slate-400' : 'text-slate-100'}`}>{l.text}</div>}
            {e.reason && e.reason !== l.text && <div className="text-[18px] leading-snug text-slate-400">{e.reason}</div>}
          </Card>
        )
      })}
    </div>
  )
}
