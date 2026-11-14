import { useEffect, useState } from 'react'
import { AirportMap } from '@/AirportMap'
import { Feed } from '@/Feed'
import { TopBar } from '@/TopBar'
import type { Airport } from '@/types'
import { useTower } from '@/useTower'

export default function App() {
  const [airport, setAirport] = useState<Airport | null>(null)
  const tower = useTower()

  // Retry until the backend answers; it may start after the dashboard.
  useEffect(() => {
    let timer = 0
    const load = () =>
      fetch('/api/airport')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then(setAirport)
        .catch(() => (timer = window.setTimeout(load, 2000)))
    load()
    return () => clearTimeout(timer)
  }, [])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0b0f14] text-slate-100">
      <TopBar config={airport?.config ?? null} weather={tower.weather} runway={tower.runway} t={tower.t} connected={tower.connected} />
      <main className="flex min-h-0 flex-1">
        <section className="relative min-w-0 flex-1">
          {airport ? (
            <AirportMap airport={airport} runway={tower.runway} aircraft={tower.aircraft} />
          ) : (
            <p className="p-8 text-2xl text-slate-500">Waiting for backend…</p>
          )}
        </section>
        <aside className="w-[600px] shrink-0 border-l border-slate-800 bg-slate-950/60">
          <Feed events={tower.feed} />
        </aside>
      </main>
    </div>
  )
}
