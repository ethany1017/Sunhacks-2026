import { Badge } from '@/components/ui/badge'
import { clock } from '@/useTower'
import type { AirportConfig, WeatherData } from '@/types'

export const PROJECT_NAME = 'Tower Zero'

function Field({ label, value, grow }: { label: string; value: string; grow?: boolean }) {
  return (
    <div className={`flex min-w-0 flex-col ${grow ? 'flex-1' : ''}`}>
      <span className="text-sm font-semibold tracking-widest text-slate-500">{label}</span>
      <span className={`truncate font-mono text-[26px] leading-tight text-slate-100 ${grow ? 'text-xl leading-9' : ''}`}>{value}</span>
    </div>
  )
}

type Props = { config: AirportConfig | null; weather: WeatherData | null; runway: string | null; t: number; connected: boolean }

export function TopBar({ config, weather, runway, t, connected }: Props) {
  const wind = weather
    ? `${String(weather.wind_dir).padStart(3, '0')}° ${weather.wind_speed}${weather.wind_gust ? `G${weather.wind_gust}` : ''} kt`
    : '--'
  return (
    <header className="flex items-center gap-8 border-b border-slate-800 bg-slate-950 px-6 py-3">
      <div className="flex shrink-0 flex-col">
        <span className="text-[28px] font-bold tracking-wide text-emerald-400">{PROJECT_NAME}</span>
        <span className="flex items-center gap-2 text-base text-slate-400">
          {config?.name ?? 'Loading airport…'}
          {config?.placeholder && <Badge className="bg-amber-500/90 text-xs font-bold text-black">PLACEHOLDER AIRPORT</Badge>}
        </span>
      </div>
      <Field label="METAR" value={weather?.raw ?? '--'} grow />
      <Field label="WIND" value={wind} />
      <Field label="RWY" value={runway ?? '--'} />
      <Field label="SIM" value={clock(t)} />
      <div className="flex shrink-0 items-center gap-2 text-lg">
        <span className={`size-3.5 rounded-full ${connected ? 'bg-emerald-400' : 'animate-pulse bg-red-500'}`} />
        <span className={connected ? 'text-emerald-400' : 'text-red-400'}>{connected ? 'LIVE' : 'OFFLINE'}</span>
      </div>
    </header>
  )
}
