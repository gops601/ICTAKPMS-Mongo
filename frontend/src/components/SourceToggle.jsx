/** Choose between uploading a file and pasting a link to it. `mode` is 'file' or 'link'. */
export default function SourceToggle({ name, label, mode, onChange }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-5 text-sm">
      {[['file', 'Upload a file'], ['link', 'Add a link']].map(([value, text]) => (
        <label key={value} className="flex items-center gap-2">
          <input type="radio" name={name} checked={mode === value} onChange={() => onChange(value)} />{text}
        </label>
      ))}
    </div>
  )
}
