import { useEffect, useState } from 'react'
import { rubricService } from '../../services/adminService'
import { errorMessage } from '../../services/api'
import { useAuth } from '../../context/AuthContext'
import PageHeader from '../../components/PageHeader'
import Spinner from '../../components/Spinner'
import Alert from '../../components/Alert'

const LEVEL_TONES = ['text-emerald-700', 'text-blue-700', 'text-amber-700', 'text-red-700']
const total = (criteria) => criteria.reduce((sum, c) => sum + c.marks, 0)

function Table({ label, headers, children }) {
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50">
            {headers.map((h) => <th key={h} scope="col" className="px-3 py-2 font-semibold text-slate-700">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 align-top">{children}</tbody>
      </table>
    </div>
  )
}

function CriteriaCard({ title, criteria, levels }) {
  return (
    <section className="card">
      <h2 className="mb-3 text-base font-semibold text-slate-900">{title} ({total(criteria)} marks)</h2>
      <div className="min-w-0">
        <Table label={title} headers={['Criterion', 'Marks', ...levels]}>
          {criteria.map((c) => (
            <tr key={c.code}>
              <th scope="row" className="min-w-[12rem] px-3 py-3 font-semibold text-slate-900">
                {c.code} {c.name}
                <div className="mt-1 text-xs font-normal text-slate-500">Scope: {c.scope}<br />Assessed at: {c.assessed_at}</div>
              </th>
              <td className="px-3 py-3 text-center font-semibold">{c.marks}</td>
              {c.levels.map((l, i) => (
                <td key={levels[i]} className="min-w-[11rem] px-3 py-3">
                  <div className={`text-xs font-semibold ${LEVEL_TONES[i]}`}>{l.marks} marks</div>
                  <div className="mt-1 text-slate-600">{l.description}</div>
                </td>
              ))}
            </tr>
          ))}
        </Table>
      </div>
    </section>
  )
}

function ResultRules({ data }) {
  return (
    <>
      <section className="card">
        <h2 className="mb-2 text-base font-semibold text-slate-900">Scoring principles</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{data.scoring_principles.map((p) => <li key={p}>{p}</li>)}</ul>
      </section>
      <section className="card">
        <h2 className="mb-1 text-base font-semibold text-slate-900">Code quality marks (C3)</h2>
        <p className="mb-3 text-sm text-slate-500">Scored from the final scan on <a className="text-brand underline" href="https://qc.ictkerala.org" target="_blank" rel="noreferrer">qc.ictkerala.org</a>.</p>
        <Table label="Code quality marks" headers={['Final report output', 'C3 marks (out of 10)']}>
          {data.code_quality.map((r) => <tr key={r.marks}><td className="px-3 py-2">{r.result}</td><td className="px-3 py-2 font-semibold">{r.marks}</td></tr>)}
        </Table>
      </section>
      <section className="card">
        <h2 className="mb-3 text-base font-semibold text-slate-900">Grading and eligibility</h2>
        <Table label="Grades" headers={['Grade', 'Score (out of 100)', 'Interpretation']}>
          {data.grades.map((g) => <tr key={g.score}><td className="px-3 py-2 font-semibold">{g.grade}</td><td className="px-3 py-2">{g.score}</td><td className="px-3 py-2">{g.meaning}</td></tr>)}
        </Table>
        <p className="mb-1 mt-4 text-sm font-medium text-slate-700">Project certification needs both:</p>
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{data.eligibility.map((e) => <li key={e}>{e}</li>)}</ul>
      </section>
    </>
  )
}

/** The evaluation rubric. The API decides which tracks a user gets: admins all of them, others their own domains'. */
export default function RubricsPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')

  useEffect(() => {
    rubricService.get().then(setData).catch((e) => setError(errorMessage(e)))
  }, [])

  if (!data) return error ? <Alert>{error}</Alert> : <Spinner />
  const track = data.tracks.find((t) => t.key === selected) || data.tracks[0]
  const missing = data.unmapped_domains.map((d) => d.name).join(', ')

  return (
    <div className="space-y-4">
      <PageHeader title={isAdmin ? 'Evaluation Rubrics' : 'Evaluation Rubric'}
        subtitle={`${data.title} v${data.version} · Part A (common, 40 marks) + Part B (domain, 60 marks) = 100`} />

      {data.tracks.length > 1 && (
        <div role="tablist" aria-label="Domain rubrics" className="flex flex-wrap gap-2">
          {data.tracks.map((t) => (
            <button key={t.key} role="tab" aria-selected={t.key === track.key} onClick={() => setSelected(t.key)}
              className={t.key === track.key ? 'btn-primary' : 'btn-secondary'}>{t.name}</button>
          ))}
        </div>
      )}

      {missing && (
        <p role="note" className="rounded-lg bg-orange-50 px-4 py-3 text-sm text-orange-800 ring-1 ring-orange-200">
          No domain-specific rubric is set for: {missing}. {isAdmin ? 'Choose one under Domains → Edit.' : 'Only the common criteria below apply until an administrator sets one.'}
        </p>
      )}
      {!missing && !track && (
        <p role="note" className="text-sm text-slate-500">You are not linked to a domain yet, so only the common criteria are shown.</p>
      )}

      {track && (
        <div>
          <h2 className="text-lg font-semibold text-slate-900">{track.name}</h2>
          <p className="text-sm text-slate-500">
            {track.summary} · {track.domains.length ? `Applies to: ${track.domains.map((d) => d.name).join(', ')}` : 'Not linked to any domain yet'}
          </p>
        </div>
      )}

      <CriteriaCard title="Part A – Common criteria" criteria={data.common_criteria} levels={data.levels} />
      {track && <CriteriaCard title={`Part B – ${track.name}`} criteria={track.criteria} levels={data.levels} />}
      <ResultRules data={data} />
    </div>
  )
}
