import { Fragment, useEffect, useState } from 'react'
import type { Comparison as Cmp, EvalTotals, Metrics, RefereeFail, RunInfo } from '@/types'

// The dashboard's only actions: pick a level and a controller, start/reset a run, switch controller mid-run.

type Options = { levels: { id: string; name: string }[]; controllers: { id: string; label: string }[] }

async function post(path: string, body: object) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
}

const SELECT = 'rounded-lg border border-slate-900 bg-white px-3 py-1.5 text-base text-slate-900'
const BUTTON = 'shrink-0 whitespace-nowrap rounded-lg px-4 py-1.5 text-base font-semibold disabled:opacity-40'

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
    <div className="flex shrink-0 items-center gap-3">
      <select className={`${SELECT} max-w-64 min-[1600px]:max-w-none`} value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
        {opts?.levels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
      <select className={SELECT} value={controller} onChange={(e) => setController(e.target.value)} aria-label="Controller">
        {opts?.controllers.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>
      <button className={`${BUTTON} bg-blue-700 text-white hover:bg-blue-800`} disabled={!level}
        onClick={() => act(post('/api/run', { level, controller }))}>
        {run ? 'Reset run' : 'Start run'}
      </button>
      <button className={`${BUTTON} border border-slate-900 bg-white text-slate-900 hover:bg-slate-50`} disabled={!run || ended || !other}
        onClick={() => other && act(post('/api/controller', { controller: other.id }))}>
        Switch to {other?.label.split(' ')[0] ?? '…'}
      </button>
      {error && <span className="text-lg text-red-600">{error}</span>}
    </div>
  )
}

export function Banner({ verdict, fails, run, ended }: { verdict: string; fails: RefereeFail[]; run: RunInfo | null; ended: boolean }) {
  const who = run ? `${run.level_name} · seed ${run.seed} · ${run.controller_label}` : 'No run'
  if (verdict === 'FAIL') {
    const first = fails[0]
    return (
      <div className="rounded-xl border border-red-800 bg-red-600 px-5 py-3 text-white">
        <div className="flex items-baseline gap-4">
          <span className="text-4xl font-black tracking-wider">FAIL</span>
          <span className="text-xl font-semibold">{who}{fails.length > 1 ? ` · ${fails.length} fails` : ''}</span>
        </div>
        {first && (
          <div className="mt-1 text-xl leading-snug">
            <span className="font-mono font-bold">{first.callsigns.join(', ')}</span> · {first.rule.replaceAll('_', ' ')}: {first.reason}
          </div>
        )}
      </div>
    )
  }
  if (verdict === 'PASS') {
    return <div className="rounded-xl border border-green-800 bg-green-600 px-5 py-2.5 text-3xl font-black tracking-wider text-white">PASS <span className="text-lg font-semibold">{who}</span></div>
  }
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-900 bg-white px-5 py-2 text-base text-slate-600">
      <span className="size-2.5 rounded-full bg-green-600" />
      <span className="font-semibold tracking-[0.12em] text-slate-900">{ended ? 'ENDED' : 'RUNNING'}</span>
      <span>{who}</span><span className="text-slate-300">·</span><span>Referee: no fails so far</span>
    </div>
  )
}

const mins = (s: number | undefined) => (s == null ? '--' : `${(s / 60).toFixed(1)} min`)

export function Scoreboard({ m }: { m: Metrics | null }) {
  const stats: [string, string | number][] = [
    ['Fails', m?.fails ?? '--'],
    ['Completed', m?.completed ?? '--'],
    ['Avg delay', m ? `${Math.round(m.avg_delay_s)} s` : '--'],
    ['Total delay', mins(m?.total_delay_s)],
    ['Go-arounds', m?.go_arounds ?? '--'],
    ['Diversions', m?.diversions ?? '--'],
  ]
  return (
    <div className="rounded-xl border border-slate-900 bg-white px-4 py-3 min-[1600px]:py-4">
      <div className="text-xs font-semibold tracking-[0.12em] text-slate-500">SCORE</div>
      <div className="mt-1 grid grid-cols-3 gap-x-4 gap-y-2">
        {stats.map(([k, v]) => (
          <div key={k} className="flex flex-col">
            <span className="text-xs font-medium text-slate-500 min-[1600px]:text-sm">{k}</span>
            <span className={`font-mono text-2xl leading-tight font-bold tabular-nums min-[1600px]:text-3xl ${k === 'Fails' && Number(v) > 0 ? 'text-red-600' : 'text-slate-950'}`}>{v}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between border-t border-slate-900 pt-2 text-sm text-slate-500 min-[1600px]:text-base">
        <span>Readback errors caught <span className="font-mono font-bold text-slate-950">{m?.readback_errors_caught ?? '--'}</span></span>
        <span>Missed <span className={`font-mono font-bold ${Number(m?.readback_errors_accepted) > 0 ? 'text-red-600' : 'text-slate-950'}`}>{m?.readback_errors_accepted ?? '--'}</span></span>
      </div>
    </div>
  )
}

const SECTION_NAMES = { training: 'Training seeds', heldout: 'Held-out seeds', practice: 'Practice' } as const

// Latest Agent vs Human evaluation (python -m app.evaluate --publish), judged only by the simulator's Referee.
export function Comparison({ c }: { c: Cmp | null }) {
  if (!c) return null
  const rows = (['training', 'heldout', 'practice'] as const).flatMap((k) =>
    (['human', 'agent'] as const).map((who) => ({ key: `${k}-${who}`, name: `${SECTION_NAMES[k]} · ${who === 'human' ? 'Human' : 'Agent'}`, t: c.sections[k]?.[who] })))
  const tr = c.sections.training
  const line = (t: EvalTotals | undefined) => (t ? `${t.passed}/${t.runs} pass · ${t.fails} fails` : '--')
  return (
    <>
    <div className="rounded-xl border border-slate-900 bg-white px-4 py-2 text-base min-[1600px]:hidden">
      <span className="text-xs font-semibold tracking-[0.12em] text-slate-500">AGENT VS HUMAN</span>{' '}
      <span className="text-slate-500">training seeds: </span>
      <span className="font-mono font-semibold text-slate-950">Agent {line(tr?.agent)}</span>
      <span className="text-slate-300"> | </span>
      <span className="font-mono text-slate-500">Human {line(tr?.human)}</span>
    </div>
    <div className="hidden rounded-xl border border-slate-900 bg-white p-4 min-[1600px]:block">
      <div className="text-xs font-semibold tracking-[0.12em] text-slate-500">
        AGENT VS HUMAN{c.human_impl === 'tower' ? ' · Human = built-in tower (stand-in)' : ' · Human = simulated average controller'}
      </div>
      <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-x-4 text-lg">
        {['', 'Pass', 'Fails', 'Avg delay', 'Diverted'].map((h) => <span key={h} className="text-sm font-medium text-slate-400">{h}</span>)}
        {rows.filter((r) => r.t).map(({ key, name, t }) => (
          <Fragment key={key}>
            <span className={name.endsWith('Agent') ? 'font-semibold text-slate-950' : 'text-slate-500'}>{name}</span>
            <span className="text-right font-mono">{t!.passed}/{t!.runs}</span>
            <span className="text-right font-mono">{t!.fails}</span>
            <span className="text-right font-mono">{Math.round(t!.avg_delay_s)} s</span>
            <span className="text-right font-mono">{t!.diversions}</span>
          </Fragment>
        ))}
      </div>
    </div>
    </>
  )
}
