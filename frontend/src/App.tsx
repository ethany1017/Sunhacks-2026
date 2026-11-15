import { AirportMap } from '@/AirportMap'
import { Feed } from '@/Feed'
import { Banner, Comparison, Controls, Scoreboard } from '@/RunPanel'
import { TopBar } from '@/TopBar'
import type { Aircraft } from '@/types'
import { useMemo, useRef } from 'react'
import { useTower } from '@/useTower'
import { useSpeaking, VoicePanel } from '@/Voice'

const GLASS = 'pointer-events-auto rounded-xl border border-white/10 bg-slate-950/70 shadow-2xl shadow-black/50 backdrop-blur-md'

// Arrivals on final, lowest (closest to landing) first: most of the 11 km finals lie off the default map view.
function OnFinal({ aircraft }: { aircraft: Record<string, Aircraft> }) {
  const finals = Object.values(aircraft)
    .filter((a) => a.kind === 'arrival' && (a.phase === 'final' || a.phase === 'go_around'))
    .sort((a, b) => (a.altitude_ft ?? 0) - (b.altitude_ft ?? 0))
  return (
    <div className={`${GLASS} pointer-events-none! absolute top-0 left-0 px-4 py-2`}>
      <div className="text-sm font-semibold tracking-[0.2em] text-cyan-400">ON FINAL ({finals.length})</div>
      {finals.length === 0 && <div className="text-lg text-slate-500">none</div>}
      {finals.slice(0, 8).map((a) => (
        <div key={a.callsign} className="flex gap-4 font-mono text-xl">
          <span className={a.phase === 'go_around' ? 'text-amber-300' : 'text-cyan-300'}>{a.callsign}</span>
          <span className="ml-auto text-slate-300 tabular-nums">{a.phase === 'go_around' ? 'GO AROUND' : `${Math.round(a.altitude_ft ?? 0)} ft`}</span>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const tower = useTower()
  const airport = tower.airport
  const speaking = useSpeaking()
  const head = useRef<HTMLDivElement>(null)
  const side = useRef<HTMLElement>(null)
  // ICAO type per callsign, from spawn events (kept for the whole run even after the feed drops them).
  const typesRef = useRef<Record<string, string>>({})
  const types = useMemo(() => {
    const next = { ...typesRef.current }
    for (const e of tower.feed) if (e.type === 'aircraft_spawn' && e.callsign) next[e.callsign] = e.data.aircraft_type
    return (typesRef.current = next)
  }, [tower.feed])

  return (
    <div className="relative h-screen overflow-hidden bg-[#0a1018] text-slate-100">
      {/* full-bleed map behind floating glass panels */}
      <div className="absolute inset-0">
        {airport ? (
          <AirportMap airport={airport} runways={tower.runways} aircraft={tower.aircraft} types={types} speaking={speaking}
            inset={() => ({ top: (head.current?.offsetHeight ?? 0) + 12, right: (side.current?.offsetWidth ?? 0) + 24 })} />
        ) : (
          <p className="p-8 pt-72 text-2xl text-slate-500">Waiting for airport layout…</p>
        )}
      </div>
      <div className="pointer-events-none relative flex h-full flex-col gap-3 p-3">
        <div ref={head} className="flex flex-col gap-2">
          <TopBar config={airport?.config ?? null} weather={tower.weather} runway={tower.runway} t={tower.t} connected={tower.connected} />
          <div className={`${GLASS} flex items-center gap-4 px-4 py-2`}>
            <Controls run={tower.run} ended={tower.ended} />
            <VoicePanel />
          </div>
          <Banner verdict={tower.verdict} fails={tower.fails} run={tower.run} ended={tower.ended} />
        </div>
        <main className="flex min-h-0 flex-1 gap-3">
          <section className="relative min-w-0 flex-1">{airport && <OnFinal aircraft={tower.aircraft} />}</section>
          <aside ref={side} className={`${GLASS} flex w-[440px] shrink-0 flex-col overflow-hidden min-[1600px]:w-[600px]`}>
            <Scoreboard m={tower.metrics} />
            <Comparison c={tower.comparison} />
            <div className="min-h-0 flex-1">
              <Feed events={tower.feed} speaking={speaking} />
            </div>
          </aside>
        </main>
      </div>
    </div>
  )
}
