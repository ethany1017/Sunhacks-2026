import { AirportMap } from '@/AirportMap'
import { Feed } from '@/Feed'
import { TopBar } from '@/TopBar'
import { useTower } from '@/useTower'

export default function App() {
  const tower = useTower()
  const airport = tower.airport

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0b0f14] text-slate-100">
      <TopBar config={airport?.config ?? null} weather={tower.weather} runway={tower.runway} t={tower.t} connected={tower.connected} />
      <main className="flex min-h-0 flex-1">
        <section className="relative min-w-0 flex-1">
          {airport ? (
            <AirportMap airport={airport} runway={tower.runway} aircraft={tower.aircraft} />
          ) : (
            <p className="p-8 text-2xl text-slate-500">Waiting for airport layout…</p>
          )}
        </section>
        <aside className="w-[600px] shrink-0 border-l border-slate-800 bg-slate-950/60">
          <Feed events={tower.feed} />
        </aside>
      </main>
    </div>
  )
}
