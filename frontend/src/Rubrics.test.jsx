import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RubricsPage from './pages/common/RubricsPage'
import * as svc from './services/adminService'
import { useAuth } from './context/AuthContext'

vi.mock('./context/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('./services/adminService', () => ({ rubricService: { get: vi.fn() } }))

const crit = (code, name) => ({ code, name, marks: 5, scope: 'Team', assessed_at: 'R3', levels: ['5', '4', '3', '0–2'].map((marks) => ({ marks, description: `${name} ${marks}` })) })
const track = (key, name, criterion) => ({ key, name, summary: 'Team', criteria: [crit('T1', criterion)], domains: [] })
const payload = (extra) => ({
  title: 'Framework', version: '1.1', levels: ['Excellent', 'Good', 'Satisfactory', 'Needs Improvement'],
  common_criteria: [crit('C1', 'Requirements')], tracks: [], unmapped_domains: [],
  scoring_principles: [], code_quality: [], grades: [], eligibility: [], ...extra,
})

beforeEach(() => vi.clearAllMocks())

describe('evaluation rubric page', () => {
  it('lets an admin switch between domain rubrics', async () => {
    useAuth.mockReturnValue({ user: { role: 'admin' } })
    svc.rubricService.get.mockResolvedValue(payload({ tracks: [track('fsd', 'Full Stack', 'Frontend'), track('ds', 'Data Science', 'Framing')] }))
    render(<RubricsPage />)
    expect(await screen.findByRole('table', { name: 'Part B – Full Stack' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Data Science' }))
    expect(screen.getByRole('table', { name: 'Part B – Data Science' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Part B – Full Stack' })).not.toBeInTheDocument()
  })

  it('shows a student their single rubric without tabs', async () => {
    useAuth.mockReturnValue({ user: { role: 'student' } })
    svc.rubricService.get.mockResolvedValue(payload({ tracks: [track('ds', 'Data Science', 'Framing')] }))
    render(<RubricsPage />)
    expect(await screen.findByRole('table', { name: 'Part B – Data Science' })).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('shows only the common criteria when the domain has no rubric', async () => {
    useAuth.mockReturnValue({ user: { role: 'mentor' } })
    svc.rubricService.get.mockResolvedValue(payload({ unmapped_domains: [{ id: 5, name: 'IoT' }] }))
    render(<RubricsPage />)
    expect(await screen.findByRole('note')).toHaveTextContent('IoT')
    expect(screen.getByRole('table', { name: 'Part A – Common criteria' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /part b/i })).not.toBeInTheDocument()
  })
})
