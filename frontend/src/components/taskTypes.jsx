import { Bookmark, Bug, CheckSquare, Zap } from 'lucide-react'

export const TASK_TYPES = [
  { value: 'epic', label: 'Epic', Icon: Zap, color: 'text-purple-600' },
  { value: 'story', label: 'Story', Icon: Bookmark, color: 'text-green-600' },
  { value: 'task', label: 'Task', Icon: CheckSquare, color: 'text-blue-600' },
  { value: 'bug', label: 'Bug', Icon: Bug, color: 'text-red-600' },
]

export function TypeIcon({ type, className = 'h-4 w-4' }) {
  const t = TASK_TYPES.find((x) => x.value === type) || TASK_TYPES[2]
  return <t.Icon className={`${className} ${t.color} shrink-0`} aria-label={t.label} role="img" />
}
