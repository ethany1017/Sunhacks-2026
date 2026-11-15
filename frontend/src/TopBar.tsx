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
      <span className="text-xs font-semibold tracking-[0.12em] text-slate-500">{label}</span>
      <span className="font-mono text-xl leading-tight font-semibold text-slate-900 tabular-nums min-[1600px]:text-[22px]">{value}</span>
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
    <header className="flex items-center gap-4 min-[1600px]:gap-6 rounded-xl border border-slate-900 bg-white px-5 py-2.5">
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-3">
          <span className="text-2xl font-bold tracking-tight text-slate-950 min-[1600px]:text-[28px]">{PROJECT_NAME}</span>
          <span className="rounded-sm border border-slate-900 px-2 py-0.5 text-xs font-semibold tracking-[0.14em] text-slate-900">SIMULATION ONLY</span>
        </span>
        <span className="text-base text-slate-500">{config?.name ?? 'Waiting for airport layout…'}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="w-24 shrink-0 text-xs font-semibold tracking-[0.12em] text-slate-500 min-[1600px]:w-32">LEVEL WX</span>
          <span className="line-clamp-2 font-mono text-base leading-snug min-[1600px]:line-clamp-none min-[1600px]:truncate min-[1600px]:text-[18px] text-slate-900">{weather?.metar ?? '--'}</span>
        </div>
        <div className="flex min-w-0 items-baseline gap-3" title="Real-world weather from aviationweather.gov. Display only: the agent flies the level's weather.">
          <span className="w-24 shrink-0 text-xs font-semibold tracking-[0.12em] text-slate-400 min-[1600px]:w-32">LIVE {config?.icao ?? live?.icao ?? ''}</span>
          <span className="truncate font-mono text-sm text-slate-500 min-[1600px]:text-lg">{liveText}</span>
        </div>
      </div>
      <Field label="WIND" value={weather ? windText(weather.wind_dir, weather.wind_kt, weather.gust_kt) : '--'} />
      <Field label="RUNWAYS" value={runway ?? '--'} />
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-2 text-xs font-semibold tracking-[0.12em] text-slate-500">
          SIM TIME
          <span className={`size-2.5 rounded-full ${connected ? 'bg-green-600' : 'animate-pulse bg-red-600'}`} />
          <span className={connected ? 'text-slate-500' : 'text-red-600'}>{connected ? 'ONLINE' : 'OFFLINE'}</span>
        </span>
        <span className="font-mono text-[28px] leading-tight font-bold min-[1600px]:text-[36px] text-slate-950 tabular-nums">{clock(t)}</span>
      </div>
    </header>
  )
}
