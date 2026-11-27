import { useEffect, useState } from 'react'
import { Pencil } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { authService } from '../../services/authService'
import { errorMessage, fieldErrors } from '../../services/api'
import { ROLE_LABEL } from '../../utils/roles'
import Alert from '../../components/Alert'

const FIELDS = [['designation', 'Designation'], ['expertise', 'Expertise'], ['company', 'Company'], ['industry_sector', 'Industry / sector'],
  ['experience_years', 'Experience (years)', 'number'], ['linkedin_url', 'LinkedIn / profile link'], ['bio', 'Professional profile', 'textarea']]

/** A mentor (or domain lead) edits their own profile here. */
function MentorEditor({ mentor, onSaved, onCancel }) {
  const { refreshUser } = useAuth()
  const [form, setForm] = useState({ name: mentor.name || '', phone: mentor.phone || '', ...Object.fromEntries(FIELDS.map(([k]) => [k, mentor[k] ?? ''])) })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [errs, setErrs] = useState({})
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError(''); setErrs({})
    const body = { ...form, experience_years: form.experience_years === '' ? null : Number(form.experience_years) }
    try { const m = await authService.updateMentorProfile(body); await refreshUser(); onSaved(m) }
    catch (err) { setError(errorMessage(err)); setErrs(fieldErrors(err)) } finally { setBusy(false) }
  }
  const err = (k) => errs[k] && <p className="mt-1 text-xs text-red-600">{errs[k]}</p>
  return (
    <form onSubmit={save} className="card space-y-3">
      <Alert>{error}</Alert>
      <div><label htmlFor="p-name" className="label">Name *</label><input id="p-name" className="input" required minLength={2} value={form.name} onChange={set('name')} />{err('name')}</div>
      <div><label htmlFor="p-phone" className="label">Phone</label><input id="p-phone" className="input" value={form.phone} onChange={set('phone')} />{err('phone')}</div>
      {FIELDS.map(([k, label, type]) => (
        <div key={k}><label htmlFor={`p-${k}`} className="label">{label}</label>
          {type === 'textarea' ? <textarea id={`p-${k}`} className="input" rows={4} value={form[k]} onChange={set(k)} />
            : <input id={`p-${k}`} type={type || 'text'} className="input" value={form[k]} onChange={set(k)} />}{err(k)}</div>
      ))}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save profile'}</button>
      </div>
    </form>
  )
}

export default function Profile() {
  const { user } = useAuth()
  const [mentor, setMentor] = useState(null)      // the mentor profile, when the user has one
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    if (user.role === 'mentor' || user.role === 'domain_lead') authService.mentorProfile().then(setMentor).catch(() => setMentor(null))
  }, [user.role])

  const rows = [
    ['Name', user.name],
    ['Email', user.email],
    ['Phone', user.phone || '—'],
    ['Role', ROLE_LABEL[user.role]],
    ...(mentor ? [['Designation', mentor.designation || '—'], ['Expertise', mentor.expertise || '—'],
      ...(mentor.mentor_type === 'industry' ? [['Company', mentor.company || '—'], ['Industry / sector', mentor.industry_sector || '—'],
        ['Experience', mentor.experience_years != null ? `${mentor.experience_years} years` : '—']] : []),
      ['Profile link', mentor.linkedin_url || '—'], ['Professional profile', mentor.bio || '—']] : []),
    ['Last login', user.last_login_at ? new Date(user.last_login_at + 'Z').toLocaleString() : '—'],
  ]
  return (
    <div className="max-w-xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">My Profile</h1>
        {mentor && !editing && <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" aria-hidden />Edit profile</button>}
      </div>
      {editing ? <MentorEditor mentor={mentor} onCancel={() => setEditing(false)} onSaved={(m) => { setMentor(m); setEditing(false) }} /> : (
        <div className="card">
          <dl className="divide-y divide-slate-100">
            {rows.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 py-2 text-sm">
                <dt className="shrink-0 text-slate-500">{k}</dt>
                <dd className="whitespace-pre-line text-right font-medium text-slate-900">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
