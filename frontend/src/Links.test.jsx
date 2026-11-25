import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AttachmentPanel from './components/AttachmentPanel'
import SrsPanel from './components/SrsPanel'
import api from './services/api'
import * as svc from './services/adminService'

vi.mock('./services/api', async () => {
  const actual = await vi.importActual('./services/api')
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } }
})
vi.mock('./context/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, role: 'student' } }) }))

const URL_ = 'https://drive.google.com/file/d/abc/view'
const srs = (submissions = []) => ({ project_id: 7, title: 'P', status: 'not_submitted', status_label: 'Not Submitted', can_submit: true,
  implementation_unlocked: false, due_date: null, submissions })
const reply = (data) => Promise.resolve({ data: { data } })

beforeEach(() => vi.clearAllMocks())

describe('links instead of uploads', () => {
  it('submits the SRS as a link', async () => {
    api.get.mockReturnValue(reply(srs()))
    api.post.mockReturnValue(reply(srs()))
    render(<SrsPanel projectId={7} />)
    await userEvent.click(await screen.findByRole('radio', { name: 'Add a link' }))
    await userEvent.type(screen.getByLabelText(/link to your srs/i), URL_)
    await userEvent.click(screen.getByRole('button', { name: /submit srs/i }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const form = api.post.mock.calls[0][1]
    expect([form.get('url'), form.get('file')]).toEqual([URL_, null])
  })

  it('adds a link as an attachment and opens it in a new tab', async () => {
    api.post.mockReturnValue(reply({}))
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<AttachmentPanel psId={3} kind="datasets" title="Datasets" canEdit onChanged={vi.fn()}
      items={[{ id: 1, name: 'train.csv', size_bytes: 0, url: URL_ }]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open train.csv' }))
    expect(open).toHaveBeenCalledWith(URL_, '_blank', 'noopener,noreferrer')
    expect(api.get).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('radio', { name: 'Add a link' }))
    await userEvent.type(screen.getByLabelText('Datasets link'), URL_)
    await userEvent.click(screen.getByRole('button', { name: 'Add link' }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][1].get('url')).toBe(URL_)
    open.mockRestore()
  })

  it('sends a linked proposal document and dataset links', async () => {
    api.post.mockReturnValue(reply({}))
    await svc.proposalService.submit('T', '', URL_, ['https://example.com/a.csv'])
    const form = api.post.mock.calls[0][1]
    expect([form.get('url'), form.getAll('dataset_links'), form.get('file')]).toEqual([URL_, ['https://example.com/a.csv'], null])
  })
})
