import { Fragment, useEffect, useState } from 'react'
import type { Comparison as Cmp, Metrics, RefereeFail, RunInfo } from '@/types'

// The dashboard's only actions: pick a level and a controller, start/reset a run, switch controller mid-run.

type Options = { levels: { id: string; name: string }[]; controllers: { id: string; label: string }[] }

async function post(path: string, body: object) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
}

const SELECT = 'rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-lg text-slate-100'
const BUTTON = 'rounded-md px-4 py-2 text-lg font-semibold disabled:opacity-40'

export function Controls({ run, ended }: { run: RunInfo | null; ended: boolean }) {
  const [opts, setOpts] = useState<Options | null>(null)
  const [level, setLevel] = useState('')
  const [controller, setController] = useState('agent')
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/options').then((r) => r.json()).then((o: Options) => {
      setOpts(o)
      setLevel((l) => l || o.levels[0]?.id || '')
    }).catch(() => setError('Backend unreachable'))
  }, [])

  const runLevel = run?.level
  useEffect(() => {
    if (runLevel) setLevel(runLevel)
  }, [runLevel])

  const act = (p: Promise<void>) => p.then(() => setError('')).catch((e: Error) => setError(e.message))
  const other = opts?.controllers.find((c) => c.id !== run?.controller)

  return (
    <div className="flex items-center gap-3">
      <select className={SELECT} value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
        {opts?.levels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
      <select className={SELECT} value={controller} onChange={(e) => setController(e.target.value)} aria-label="Controller">
        {opts?.controllers.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>
      <button className={`${BUTTON} bg-emerald-600 text-white hover:bg-emerald-500`} disabled={!level}
        onClick={() => act(post('/api/run', { level, controller }))}>
        {run ? 'Reset run' : 'Start run'}
      </button>
      <button className={`${BUTTON} bg-sky-700 text-white hover:bg-sky-600`} disabled={!run || ended || !other}
        onClick={() => other && act(post('/api/controller', { controller: other.id }))}>
        Switch to {other?.label.split(' ')[0] ?? '…'}
      </button>
      {error && <span className="text-lg text-red-400">{error}</span>}
    </div>
  )
}

export function Banner({ verdict, fails, run, ended }: { verdict: string; fails: RefereeFail[]; run: RunInfo | null; ended: boolean }) {
  const who = run ? `${run.level_name} · ${run.controller_label}` : 'No run'
  if (verdict === 'FAIL') {
    const first = fails[0]
    return (
      <div className="border-b-4 border-red-400 bg-red-700 px-6 py-4 text-white">
        <div className="flex items-baseline gap-4">
          <span className="text-5xl font-black tracking-wider">FAIL</span>
          <span className="text-xl font-semibold">{who}{fails.length > 1 ? ` · ${fails.length} fails` : ''}</span>
        </div>
        {first && (
          <div className="mt-1 text-2xl leading-snug">
            <span className="font-mono font-bold">{first.callsigns.join(', ')}</span> · {first.rule.replaceAll('_', ' ')}: {first.reason}
          </div>
        )}
      </div>
    )
  }
  if (verdict === 'PASS') {
    return <div className="bg-emerald-700 px-6 py-3 text-4xl font-black tracking-wider text-white">PASS <span className="text-xl font-semibold">{who}</span></div>
  }
  return (
    <div className="border-b border-slate-800 bg-slate-900 px-6 py-2 text-lg text-slate-400">
      <span className="font-semibold tracking-widest text-slate-300">{ended ? 'ENDED' : 'RUNNING'}</span> · {who} · Referee: no fails so far
    </div>
  )
}

const mins = (s: number | undefined) => (s == null ? '--' : `${(s / 60).toFixed(1)} min`)

export function Scoreboard({ m }: { m: Metrics | null }) {
  const rows: [string, string | number][] = [
    ['Fails', m?.fails ?? '--'],
    ['Completed', m?.completed ?? '--'],
    ['Avg delay', m ? `${Math.round(m.avg_delay_s)} s` : '--'],
    ['Total delay', mins(m?.total_delay_s)],
    ['Go-arounds', m?.go_arounds ?? '--'],
    ['Diversions', m?.diversions ?? '--'],
    ['Readback errors caught', m?.readback_errors_caught ?? '--'],
    ['Readback errors missed', m?.readback_errors_accepted ?? '--'],
  ]
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1 border-b border-slate-800 p-4">
      <div className="col-span-2 text-sm font-semibold tracking-widest text-slate-500">SCOREBOARD</div>
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between text-xl">
          <span className="text-slate-400">{k}</span>
          <span className={`font-mono text-2xl font-bold ${k === 'Fails' && Number(v) > 0 ? 'text-red-400' : 'text-slate-100'}`}>{v}</span>
        </div>
      ))}
    </div>
  )
}

const SECTION_NAMES = { training: 'Training seeds', heldout: 'Held-out seeds', practice: 'Practice' } as const

// Latest Agent vs Human evaluation (python -m app.evaluate --publish), judged only by the simulator's Referee.
export function Comparison({ c }: { c: Cmp | null }) {
  if (!c) return null
  const rows = (['training', 'heldout', 'practice'] as const).flatMap((k) =>
    (['human', 'agent'] as const).map((who) => ({ key: `${k}-${who}`, name: `${SECTION_NAMES[k]} · ${who === 'human' ? 'Human' : 'Agent'}`, t: c.sections[k]?.[who] })))
  return (
    <div className="border-b border-slate-800 p-4">
      <div className="text-sm font-semibold tracking-widest text-slate-500">
        AGENT VS HUMAN{c.human_impl === 'tower' ? ' · Human = built-in tower (stand-in)' : ' · Human = simulated average controller'}
      </div>
      <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-x-4 text-lg">
        {['', 'Pass', 'Fails', 'Avg delay', 'Diverted'].map((h) => <span key={h} className="text-sm font-semibold text-slate-500">{h}</span>)}
        {rows.filter((r) => r.t).map(({ key, name, t }) => (
          <Fragment key={key}>
            <span className={name.endsWith('Agent') ? 'text-emerald-300' : 'text-slate-400'}>{name}</span>
            <span className="text-right font-mono">{t!.passed}/{t!.runs}</span>
            <span className="text-right font-mono">{t!.fails}</span>
            <span className="text-right font-mono">{Math.round(t!.avg_delay_s)} s</span>
            <span className="text-right font-mono">{t!.diversions}</span>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
