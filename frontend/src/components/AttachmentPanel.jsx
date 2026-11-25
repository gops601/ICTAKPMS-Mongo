import { useRef, useState } from 'react'
import { Download, ExternalLink, Trash2, Upload } from 'lucide-react'
import { psService } from '../services/adminService'
import { errorMessage } from '../services/api'
import Alert from './Alert'
import ConfirmDialog from './ConfirmDialog'
import SourceToggle from './SourceToggle'

const fmtSize = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)

/** Datasets or reference documents for one problem statement. `canEdit` toggles upload/delete. */
export default function AttachmentPanel({ psId, kind, title, hint, items, canEdit, onChanged }) {
  const fileRef = useRef(null)
  const [desc, setDesc] = useState('')
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('file')
  const [link, setLink] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [removing, setRemoving] = useState(null)

  const upload = async () => {
    const source = mode === 'link' ? link.trim() : file
    if (!source) return setError(mode === 'link' ? 'Paste a link first.' : 'Choose a file first.')
    setBusy(true); setError('')
    try {
      await psService.upload(psId, kind, source, desc)
      setFile(null); setLink(''); setDesc('')
      if (fileRef.current) fileRef.current.value = ''
      onChanged()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const download = async (item) => {
    setError('')
    try { await psService.download(psId, kind, item) } catch (e) { setError(errorMessage(e, 'Download failed')) }
  }
  const remove = async () => {
    setBusy(true)
    try { await psService.removeFile(psId, kind, removing.id); setRemoving(null); onChanged() }
    catch (e) { setError(errorMessage(e)); setRemoving(null) } finally { setBusy(false) }
  }

  return (
    <section className="card space-y-3" aria-label={title}>
      <div>
        <h3 className="font-semibold text-slate-900">{title}</h3>
        {hint && <p className="text-xs text-slate-500">{hint}</p>}
      </div>
      <Alert>{error}</Alert>
      {items.length === 0 && <p className="text-sm text-slate-500">Nothing attached yet.</p>}
      <ul className="divide-y divide-slate-100">
        {items.map((it) => (
          <li key={it.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <div className="min-w-0">
              <div className="truncate font-medium">{it.name}</div>
              <div className="text-xs text-slate-500">{it.url ? 'Link' : fmtSize(it.size_bytes)}{it.description ? ` · ${it.description}` : ''}</div>
            </div>
            <div className="flex shrink-0 gap-2">
              <button className="btn-secondary" onClick={() => download(it)} aria-label={`${it.url ? 'Open' : 'Download'} ${it.name}`} title={it.url || undefined}>{it.url ? <ExternalLink className="h-4 w-4" /> : <Download className="h-4 w-4" />}</button>
              {canEdit && <button className="btn-secondary" onClick={() => setRemoving(it)} aria-label={`Delete ${it.name}`}><Trash2 className="h-4 w-4" /></button>}
            </div>
          </li>
        ))}
      </ul>
      {canEdit && (
        <div className="space-y-2 rounded-lg bg-slate-50 p-3">
          <SourceToggle name={`source-${kind}`} label={`How to add to ${title}`} mode={mode} onChange={(m) => { setMode(m); setFile(null); setError('') }} />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {mode === 'file'
              ? <input ref={fileRef} type="file" aria-label={`Choose ${title} file`} className="text-sm sm:col-span-1" onChange={(e) => setFile(e.target.files?.[0] || null)} />
              : <input type="url" className="input" placeholder="https://…" aria-label={`${title} link`} value={link} onChange={(e) => setLink(e.target.value)} />}
            <input className="input" placeholder="Description (optional)" aria-label={`${title} description`} value={desc} onChange={(e) => setDesc(e.target.value)} />
            <button className="btn-primary" onClick={upload} disabled={busy}><Upload className="h-4 w-4" />{mode === 'link' ? (busy ? 'Adding…' : 'Add link') : (busy ? 'Uploading…' : 'Upload')}</button>
          </div>
        </div>
      )}
      {removing && <ConfirmDialog danger confirmLabel="Delete file" busy={busy} message={`Permanently delete “${removing.name}”?`} onConfirm={remove} onCancel={() => setRemoving(null)} />}
    </section>
  )
}
