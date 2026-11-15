import { useEffect, useState } from 'react'
import { clock } from '@/useTower'
import type { AirportConfig, LiveWeather, WeatherData } from '@/types'

export const PROJECT_NAME = 'Tower Zero'
const LIVE_POLL_MS = 60_000

// The real-world METAR, display only (the backend refreshes it every few minutes; the agent never uses it).
function useLiveWeather() {
  const [wx, setWx] = useState<LiveWeather | null>(null)
  useEffect(() => {
    const load = () => fetch('/api/weather/live').then((r) => r.json()).then(setWx).catch(() => setWx(null))
    load()
    const id = window.setInterval(load, LIVE_POLL_MS)
    return () => clearInterval(id)
  }, [])
  return wx
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex shrink-0 flex-col">
      <span className="text-sm font-semibold tracking-widest text-slate-500">{label}</span>
      <span className="font-mono text-[26px] leading-tight text-slate-100 tabular-nums">{value}</span>
    </div>
  )
}

const windText = (dir: number | string | null | undefined, kt: number | null | undefined, gust?: number | null) =>
  kt == null ? '--' : `${typeof dir === 'number' ? String(dir).padStart(3, '0') + '°' : (dir ?? '')} ${kt}${gust ? `G${gust}` : ''} kt`

type Props = { config: AirportConfig | null; weather: WeatherData | null; runway: string | null; t: number; connected: boolean }

export function TopBar({ config, weather, runway, t, connected }: Props) {
  const live = useLiveWeather()
  const liveText = !live?.metar || live.source === 'unavailable'
    ? 'unavailable'
    : `${live.source === 'cache' ? 'cached · ' : ''}${live.metar.replace(/^(METAR|SPECI) /, '').split(' RMK')[0]}`
  return (
    <header className="flex items-center gap-6 border-b border-slate-800 bg-slate-950 px-6 py-3">
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-3">
          <span className="text-[28px] font-bold tracking-wide text-emerald-400">{PROJECT_NAME}</span>
          <span className="rounded-md border-2 border-amber-400 px-2 py-0.5 text-base font-black tracking-widest text-amber-300">SIMULATION ONLY</span>
        </span>
        <span className="text-base text-slate-400">{config?.name ?? 'Waiting for airport layout…'}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="w-32 shrink-0 text-sm font-semibold tracking-widest text-slate-500">LEVEL WX</span>
          <span className="truncate font-mono text-[21px] text-slate-100">{weather?.metar ?? '--'}</span>
        </div>
        <div className="flex min-w-0 items-baseline gap-3" title="Real-world weather from aviationweather.gov. Display only: the agent flies the level's weather.">
          <span className="w-32 shrink-0 text-sm font-semibold tracking-widest text-sky-500">LIVE {config?.icao ?? live?.icao ?? ''}</span>
          <span className="truncate font-mono text-lg text-sky-300/90">{liveText}</span>
        </div>
      </div>
      <Field label="WIND" value={weather ? windText(weather.wind_dir, weather.wind_kt, weather.gust_kt) : '--'} />
      <Field label="RUNWAYS" value={runway ?? '--'} />
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-2 text-sm font-semibold tracking-widest text-slate-500">
          SIM TIME
          <span className={`size-3 rounded-full ${connected ? 'bg-emerald-400' : 'animate-pulse bg-red-500'}`} />
          <span className={connected ? 'text-emerald-400' : 'text-red-400'}>{connected ? 'ONLINE' : 'OFFLINE'}</span>
        </span>
        <span className="font-mono text-[44px] leading-tight font-bold text-slate-100 tabular-nums">{clock(t)}</span>
      </div>
    </header>
  )
}
