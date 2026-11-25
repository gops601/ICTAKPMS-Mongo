import { Link } from 'react-router-dom'
import StatusBadge from './StatusBadge'

const STATE = { submitted: ['green', 'Submitted'], late: ['orange', 'Submitted late'], pending: ['blue', 'Pending'], overdue: ['red', 'Overdue'] }

/** The two things due in the first week after a project is approved: the SRS and the GitHub repository link. */
export default function FirstWeekCard({ firstWeek, projectId, isStudent }) {
  if (!firstWeek) return null
  const due = firstWeek.due_date ? new Date(`${firstWeek.due_date}T00:00:00`).toLocaleDateString() : null
  const items = [
    { key: 'srs', label: 'SRS document', to: `/srs/${projectId}`, action: 'Upload SRS' },
    { key: 'github', label: 'GitHub repository link', to: '/student/github', action: 'Add GitHub link' },
  ]

  return (
    <section className="card space-y-3" aria-label="First week submissions">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">First week submissions</h2>
        <p className="mt-1 text-sm text-slate-600">
          {isStudent ? 'Submit both within the first week of your approved project' : 'Due within the first week of the approved project'}{due ? ` – by ${due}.` : '.'}
        </p>
      </div>
      <ul className="space-y-2">
        {items.map(({ key, label, to, action }) => {
          const [tone, text] = STATE[firstWeek[key].state]
          return (
            <li key={key} className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium">{label}<StatusBadge tone={tone}>{text}</StatusBadge></div>
              {isStudent && !firstWeek[key].submitted_at && <Link className="btn-primary" to={to}>{action}</Link>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
