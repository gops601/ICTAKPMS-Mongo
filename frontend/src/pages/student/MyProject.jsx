import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { projectService } from '../../services/adminService'
import { errorMessage } from '../../services/api'
import ProjectView from '../../components/ProjectView'
import Alert from '../../components/Alert'
import Spinner from '../../components/Spinner'
import PageHeader from '../../components/PageHeader'
import { EmptyState } from '../../components/illustrations'
import MyMentorsCard from '../../components/MyMentorsCard'

export default function MyProject() {
  const [state, setState] = useState({ loading: true, projects: [], error: '' })
  const [selected, setSelected] = useState(() => { try { return Number(sessionStorage.getItem('my-project-id')) || null } catch { return null } })

  useEffect(() => {
    projectService.list({ per_page: 10 })
      .then((r) => setState({ loading: false, projects: r.items, error: '' }))
      .catch((e) => setState({ loading: false, projects: [], error: errorMessage(e) }))
  }, [])

  if (state.loading) return <Spinner />
  if (state.error) return <Alert>{state.error}</Alert>
  if (state.projects.length === 0) {
    return (
      <div>
        <PageHeader title="My Project" />
        <div className="space-y-4">
          <div className="card"><EmptyState title="No project has been allocated to you yet.">Your mentor or Domain Lead will allocate one &ndash; or <Link className="text-brand underline" to="/student/proposal">add your own project</Link>. You&apos;ll be notified.</EmptyState></div>
        </div>
      </div>
    )
  }
  const current = state.projects.find((p) => p.id === selected) || state.projects[0]
  return (
    <div className="space-y-4">
      {state.projects.length > 1 && (
        <div className="flex items-center gap-2 text-sm">
          <label htmlFor="my-project-select" className="font-medium text-slate-700">Project</label>
          <select id="my-project-select" className="input !w-auto max-w-full" value={current.id} onChange={(e) => { setSelected(Number(e.target.value)); try { sessionStorage.setItem('my-project-id', e.target.value) } catch { /* optional */ } }}>
            {state.projects.map((p) => <option key={p.id} value={p.id}>{p.code ? `${p.code} · ` : ''}{p.title}</option>)}
          </select>
        </div>
      )}
      <MyMentorsCard /><ProjectView key={current.id} id={current.id} />
    </div>
  )
}
