/** Renders one OU-configured field. Nothing here knows about specific OUs or field names. */
import { useState } from 'react'

export default function DynamicField({ field, value, onChange, error }) {
  const options = field.options || []
  const otherOpt = field.field_type === 'select' ? options.find((o) => String(o).toLowerCase() === 'other') : undefined
  const isListed = (v) => options.some((o) => o === v)
  // "Other" shows a text box so a value outside the list can be typed.
  const [otherPicked, setOtherPicked] = useState(false)
  const typedOther = otherOpt !== undefined && (otherPicked || (value && !isListed(value)))
  const id = `f-${field.field_key}`
  const common = { id, className: 'input', value: value ?? '', onChange: (e) => onChange(e.target.value) }
  let input
  if (field.field_type === 'select') {
    const selected = typedOther ? otherOpt : (value ?? '')
    const pick = (e) => {
      const v = e.target.value
      if (otherOpt !== undefined && v === otherOpt) { setOtherPicked(true); onChange(isListed(value) ? '' : (value ?? '')) }
      else { setOtherPicked(false); onChange(v) }
    }
    input = (
      <>
        <select {...common} value={selected} onChange={pick}>
          <option value="">Select…</option>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {typedOther && (
          <input type="text" className="input mt-2" aria-label={`${field.label} (other)`} placeholder={`Type the ${field.label.toLowerCase()}`}
            maxLength={500} value={isListed(value) ? '' : (value ?? '')} onChange={(e) => onChange(e.target.value)} />
        )}
      </>
    )
  } else {
    const type = { number: 'number', date: 'date', email: 'email' }[field.field_type] || 'text'
    input = <input type={type} {...common} />
  }
  return (
    <div>
      <label htmlFor={id} className="label">{field.label}{field.is_required && <span className="text-red-600"> *</span>}</label>
      {input}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}
