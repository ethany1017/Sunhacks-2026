import { clock } from '@/useTower'
import type { AirportConfig, WeatherData } from '@/types'

export const PROJECT_NAME = 'Tower Zero'

function Field({ label, value, big }: { label: string; value: string; big?: boolean }) {
  return (
    <div className="flex shrink-0 flex-col">
      <span className="text-sm font-semibold tracking-widest text-slate-500">{label}</span>
      <span className={`font-mono leading-tight text-slate-100 tabular-nums ${big ? 'text-[44px] font-bold' : 'text-[28px]'}`}>{value}</span>
    </div>
  )
}

const windText = (dir: number | string | null | undefined, kt: number | null | undefined, gust?: number | null) =>
  kt == null ? '--' : `${typeof dir === 'number' ? String(dir).padStart(3, '0') + '°' : (dir ?? '')} ${kt}${gust ? `G${gust}` : ''} kt`

type Props = { config: AirportConfig | null; weather: WeatherData | null; runway: string | null; t: number; connected: boolean }

export function TopBar({ config, weather, runway, t, connected }: Props) {
  return (
    <header className="flex items-center gap-8 border-b border-slate-800 bg-slate-950 px-6 py-3">
      <div className="flex shrink-0 flex-col">
        <span className="flex items-center gap-3">
          <span className="text-[28px] font-bold tracking-wide text-emerald-400">{PROJECT_NAME}</span>
          <span className="rounded-md border-2 border-amber-400 px-2 py-0.5 text-base font-black tracking-widest text-amber-300">SIMULATION ONLY</span>
        </span>
        <span className="text-base text-slate-400">{config?.name ?? 'Waiting for airport layout…'}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="w-36 shrink-0 text-sm font-semibold tracking-widest text-slate-500">LEVEL WEATHER</span>
          <span className="truncate font-mono text-[22px] text-slate-100">{weather?.metar ?? '--'}</span>
        </div>
      </div>
      <Field label="WIND" value={weather ? windText(weather.wind_dir, weather.wind_kt, weather.gust_kt) : '--'} />
      <Field label="RUNWAYS" value={runway ?? '--'} />
      <Field label="SIM TIME" value={clock(t)} big />
      <div className="flex shrink-0 items-center gap-2 text-lg">
        <span className={`size-3.5 rounded-full ${connected ? 'bg-emerald-400' : 'animate-pulse bg-red-500'}`} />
        <span className={connected ? 'text-emerald-400' : 'text-red-400'}>{connected ? 'CONNECTED' : 'OFFLINE'}</span>
      </div>
    </header>
  )
}
