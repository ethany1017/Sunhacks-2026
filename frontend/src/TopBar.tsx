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
      <span className="text-sm font-semibold tracking-[0.2em] text-slate-400">{label}</span>
      <span className="font-mono text-xl leading-tight text-slate-100 tabular-nums min-[1600px]:text-[26px]">{value}</span>
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
    <header className="pointer-events-auto rounded-xl border border-white/10 bg-slate-950/70 shadow-2xl shadow-black/50 backdrop-blur-md flex items-center gap-4 px-5 py-2.5 min-[1600px]:gap-6">
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-3">
          <span className="text-2xl font-bold tracking-wide text-emerald-400 min-[1600px]:text-[28px]">{PROJECT_NAME}</span>
          <span className="rounded-md border-2 border-amber-400 px-2 py-0.5 text-base font-black tracking-widest text-amber-300">SIMULATION ONLY</span>
        </span>
        <span className="text-base text-slate-400">{config?.name ?? 'Waiting for airport layout…'}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="w-24 shrink-0 text-sm font-semibold tracking-widest text-slate-500 min-[1600px]:w-32">LEVEL WX</span>
          <span className="line-clamp-2 font-mono text-base leading-snug text-slate-100 min-[1600px]:line-clamp-none min-[1600px]:truncate min-[1600px]:text-[21px]">{weather?.metar ?? '--'}</span>
        </div>
        <div className="flex min-w-0 items-baseline gap-3" title="Real-world weather from aviationweather.gov. Display only: the agent flies the level's weather.">
          <span className="w-24 shrink-0 text-sm font-semibold tracking-widest text-sky-500 min-[1600px]:w-32">LIVE {config?.icao ?? live?.icao ?? ''}</span>
          <span className="truncate font-mono text-sm text-sky-300/90 min-[1600px]:text-lg">{liveText}</span>
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
        <span className="font-mono text-[34px] leading-tight font-bold min-[1600px]:text-[44px] text-slate-100 tabular-nums">{clock(t)}</span>
      </div>
    </header>
  )
}
