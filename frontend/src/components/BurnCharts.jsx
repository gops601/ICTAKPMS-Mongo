import { useEffect, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { scrumService } from '../services/adminService'
import { errorMessage } from '../services/api'
import { COLORS, ChartCard, StatCard } from './charts'
import Alert from './Alert'
import Spinner from './Spinner'

const tick = { fill: COLORS.inkSecondary, fontSize: 12 }
const shortDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })

function Lines({ data, series }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 24, bottom: 4, left: -8 }}>
        <CartesianGrid stroke={COLORS.grid} vertical={false} />
        <XAxis dataKey="date" tickFormatter={shortDate} tick={tick} minTickGap={24} axisLine={{ stroke: COLORS.axis }} tickLine={false} />
        <YAxis tick={tick} axisLine={false} tickLine={false} />
        <Tooltip labelFormatter={shortDate} formatter={(v) => `${v} h`} />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
        {series.map((s) => (
          <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? '6 4' : undefined}
            type={s.dashed ? 'linear' : 'stepAfter'} dot={data.length === 1} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

const CHARTS = [
  { title: 'Burndown', subtitle: 'Estimated hours still to do, against an even pace to the target date', series: [
    { key: 'remaining', label: 'Remaining', color: COLORS.series[0] }, { key: 'ideal', label: 'Ideal', color: COLORS.neutral, dashed: true }] },
  { title: 'Burnup', subtitle: 'Estimated hours completed, against the total planned', series: [
    { key: 'completed', label: 'Completed', color: COLORS.series[0] }, { key: 'scope', label: 'Total scope', color: COLORS.series[1] }] },
]

/** Burndown and burnup of a project's Scrum board, measured in the tasks' estimated hours. */
export default function BurnCharts({ projectId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { scrumService.burn(projectId).then(setData).catch((e) => setError(errorMessage(e))) }, [projectId])

  if (!data) return error ? <Alert>{error}</Alert> : <Spinner />
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Estimated" value={`${data.total_hours} h`} />
        <StatCard label="Completed" value={`${data.completed_hours} h`} />
        <StatCard label="Remaining" value={`${data.remaining_hours} h`} />
      </div>
      {data.unestimated > 0 && <Alert type="warning">{data.unestimated} task(s) have no estimated hours and are not in these charts. Open each task and add an estimate.</Alert>}
      <div className="grid gap-4 xl:grid-cols-2">
        {CHARTS.map((c) => (
          <ChartCard key={c.title} title={c.title} subtitle={c.subtitle} empty={data.points.length === 0}
            table={{ columns: [{ key: 'date', label: 'Date' }, ...c.series.map((s) => ({ key: s.key, label: `${s.label} (h)` }))], rows: data.points }}>
            <Lines data={data.points} series={c.series} />
          </ChartCard>
        ))}
      </div>
    </div>
  )
}
