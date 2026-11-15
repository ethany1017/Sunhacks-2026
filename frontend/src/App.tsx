import { AirportMap } from '@/AirportMap'
import { Feed } from '@/Feed'
import { Banner, Comparison, Controls, Scoreboard } from '@/RunPanel'
import { TopBar } from '@/TopBar'
import type { Aircraft } from '@/types'
import { useTower } from '@/useTower'
import { VoicePanel } from '@/Voice'

// Arrivals on final, lowest (closest to landing) first: most of the 11 km finals lie off the default map view.
function OnFinal({ aircraft }: { aircraft: Record<string, Aircraft> }) {
  const finals = Object.values(aircraft)
    .filter((a) => a.kind === 'arrival' && (a.phase === 'final' || a.phase === 'go_around'))
    .sort((a, b) => (a.altitude_ft ?? 0) - (b.altitude_ft ?? 0))
  return (
    <div className="pointer-events-none absolute top-3 left-3 z-10 rounded-lg border border-sky-700 bg-slate-950/85 px-4 py-2">
      <div className="text-sm font-semibold tracking-widest text-sky-400">ON FINAL ({finals.length})</div>
      {finals.length === 0 && <div className="text-lg text-slate-500">none</div>}
      {finals.slice(0, 8).map((a) => (
        <div key={a.callsign} className="flex gap-4 font-mono text-xl">
          <span className={a.phase === 'go_around' ? 'text-amber-300' : 'text-sky-300'}>{a.callsign}</span>
          <span className="ml-auto text-slate-300 tabular-nums">{a.phase === 'go_around' ? 'GO AROUND' : `${Math.round(a.altitude_ft ?? 0)} ft`}</span>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const tower = useTower()
  const airport = tower.airport

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0b0f14] text-slate-100">
      <TopBar config={airport?.config ?? null} weather={tower.weather} runway={tower.runway} t={tower.t} connected={tower.connected} />
      <div className="flex items-center gap-4 border-b border-slate-800 bg-slate-950 px-6 py-2">
        <Controls run={tower.run} ended={tower.ended} />
        <VoicePanel />
      </div>
      <Banner verdict={tower.verdict} fails={tower.fails} run={tower.run} ended={tower.ended} />
      <main className="flex min-h-0 flex-1">
        <section className="relative min-w-0 flex-1">
          {airport ? (
            <>
              <AirportMap airport={airport} runways={tower.runways} aircraft={tower.aircraft} />
              <OnFinal aircraft={tower.aircraft} />
            </>
          ) : (
            <p className="p-8 text-2xl text-slate-500">Waiting for airport layout…</p>
          )}
        </section>
        <aside className="flex w-[600px] shrink-0 flex-col border-l border-slate-800 bg-slate-950/60">
          <Scoreboard m={tower.metrics} />
          <Comparison c={tower.comparison} />
          <div className="min-h-0 flex-1">
            <Feed events={tower.feed} />
          </div>
        </aside>
      </main>
    </div>
  )
}
