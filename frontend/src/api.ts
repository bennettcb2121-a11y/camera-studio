import type { EditSettings, History, Histogram, Job, LibraryItem, MockupTemplate, PhotoInfo, Preset, VideoProject } from './types'

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail ?? msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json()
}
const post = <T,>(url: string, body: unknown) => j<T>(url, { method: 'POST', body: JSON.stringify(body) })
const q = (o: Record<string, string | number | undefined | null>) =>
  '?' + Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')

export const api = {
  health: () => j<{ ok: boolean; ai: boolean }>('/api/health'),
  // library
  folders: () => j<{ folders: string[] }>('/api/library/folders'),
  addFolder: (path: string) => post<{ folder: string; items: number }>('/api/library/folders', { path }),
  removeFolder: (path: string) => j('/api/library/folders' + q({ path }), { method: 'DELETE' }),
  rescan: () => post<{ items: number }>('/api/library/rescan', {}),
  items: (p: { folder?: string; kind?: string; min_rating?: number; sort?: string }) => j<{ items: LibraryItem[] }>('/api/library/items' + q(p)),
  thumbUrl: (path: string) => '/api/library/thumb' + q({ path }),
  rate: (path: string, rating: number) => post<{ rating: number }>('/api/library/rating', { path, rating }),
  browse: (path?: string) => j<{ path: string; parent: string; dirs: string[]; media: Record<string, number> }>('/api/library/browse' + q({ path })),
  pickFolder: () => post<{ path: string | null }>('/api/library/pick-folder', {}),
  // photos
  info: (path: string) => j<PhotoInfo>('/api/photos/info' + q({ path })),
  preview: async (path: string, settings: EditSettings | null, size: number, before = false, withHist = true) => {
    const r = await fetch('/api/photos/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, settings, size, before, with_histogram: withHist }) })
    if (!r.ok) throw new Error('preview failed')
    const blob = await r.blob()
    const h = r.headers.get('X-Histogram')
    return { url: URL.createObjectURL(blob), histogram: h ? (JSON.parse(h) as Histogram) : null, ms: Number(r.headers.get('X-Render-Ms') || 0) }
  },
  setSettings: (path: string, settings: EditSettings, label: string) => post<{ settings: EditSettings; history: History }>('/api/photos/settings', { path, settings, label }),
  undo: (path: string) => post<{ settings: EditSettings; history: History }>('/api/photos/undo', { path }),
  redo: (path: string) => post<{ settings: EditSettings; history: History }>('/api/photos/redo', { path }),
  jump: (path: string, index: number) => post<{ settings: EditSettings; history: History }>('/api/photos/history/jump', { path, index }),
  reset: (path: string) => post<{ settings: EditSettings; history: History }>('/api/photos/reset', { path }),
  auto: (path: string) => post<{ settings: EditSettings; history: History }>('/api/photos/auto', { path }),
  copySettings: (source: string, targets: string[], fields?: string[]) => post<{ applied: string[] }>('/api/photos/copy-settings', { source, targets, fields }),
  filterStrip: (path: string, size = 120) => j<{ filters: { id: string | null; name: string; group: string; thumb: string }[] }>('/api/photos/filter-strip' + q({ path, size })),
  exportPhotos: (paths: string[], format: string, quality: number, long_edge?: number | null) => post<Job>('/api/photos/export', { paths, format, quality, long_edge }),
  // presets
  presets: () => j<{ presets: Preset[]; luts: string[] }>('/api/presets'),
  applyPreset: (path: string, preset_id: string | null, strength?: number) => post<{ settings: EditSettings; history: History }>('/api/presets/apply', { path, preset_id, strength }),
  savePreset: (name: string, path: string, notes = '') => post<Preset>('/api/presets/save', { name, path, notes }),
  deletePreset: (id: string) => j(`/api/presets/${id}`, { method: 'DELETE' }),
  // mockups
  templates: () => j<{ templates: MockupTemplate[] }>('/api/mockups/templates'),
  backgroundUrl: (template_id: string) => '/api/mockups/background' + q({ template_id }),
  renderMockup: async (template_id: string, paths: string[], settings: EditSettings | null, long_edge = 1400) => {
    const r = await fetch('/api/mockups/render', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template_id, paths, settings, long_edge }) })
    if (!r.ok) throw new Error('mockup failed')
    return URL.createObjectURL(await r.blob())
  },
  favoriteMockup: (path: string, template_id: string, favorite: boolean) => post<{ mockup_favorites: string[] }>('/api/mockups/favorite', { path, template_id, favorite }),
  exportMockups: (path: string, fill_paths: string[], template_ids?: string[]) => post<Job>('/api/mockups/export-all', { path, fill_paths, template_ids }),
  saveTemplate: (body: { id?: string; name: string; slots: { corners: number[][] }[]; blend?: any; background_path?: string }) => post<MockupTemplate>('/api/mockups/templates', body),
  deleteTemplate: (id: string) => j(`/api/mockups/templates/${id}`, { method: 'DELETE' }),
  uploadBackground: async (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    const r = await fetch('/api/mockups/upload-background', { method: 'POST', body: fd })
    if (!r.ok) throw new Error('upload failed')
    return r.json() as Promise<{ path: string; width: number; height: number }>
  },
  // ai
  aiStatus: (refresh = false) => j<AIStatus>('/api/ai/status' + (refresh ? '?refresh=true' : '')),
  aiBatch: (paths: string[], mode: string, note: string) => post<Job>('/api/ai/batch', { paths, mode, note }),
  autoMany: (paths: string[]) => post<Job>('/api/photos/auto-many', { paths }),
  applyPresetMany: (paths: string[], preset_id: string | null) => post<{ applied: string[] }>('/api/presets/apply-many', { paths, preset_id }),
  aiRun: (path: string, mode: string, note: string, n = 5) => post<Job>('/api/ai/run', { path, mode, note, n }),
  aiPick: (path: string, chosen: any, rejected: any[]) => post<{ settings: EditSettings; history: History }>('/api/ai/pick', { path, chosen, rejected }),
  aiReel: (project_id: string, clip_src: string, target_seconds: number, note = '') => post<Job>('/api/ai/reel', { project_id, clip_src, target_seconds, note }),
  // jobs
  jobs: () => j<{ jobs: Job[] }>('/api/jobs'),
  job: (id: string) => j<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post(`/api/jobs/${id}/cancel`, {}),
  // video
  videoInfo: (path: string) => j<{ duration: number; width: number; height: number; fps: number; has_audio: boolean }>('/api/video/info' + q({ path })),
  videoThumbUrl: (path: string, t = 0.5, width = 320) => '/api/video/thumb' + q({ path, t, width }),
  proxyUrl: (path: string) => '/api/video/proxy' + q({ path }),
  makeProxy: (path: string) => post<Job>('/api/video/proxy', { path }),
  fileUrl: (path: string) => '/api/video/file' + q({ path }),
  projects: () => j<{ projects: { id: string; name: string; clips: number }[] }>('/api/video/projects'),
  createProject: (name: string) => post<VideoProject>('/api/video/projects', { name }),
  project: (id: string) => j<VideoProject>(`/api/video/projects/${id}`),
  saveProject: (p: VideoProject) => j<VideoProject>(`/api/video/projects/${p.id}`, { method: 'PUT', body: JSON.stringify(p) }),
  deleteProject: (id: string) => j(`/api/video/projects/${id}`, { method: 'DELETE' }),
  addClip: (pid: string, body: any) => post<{ clip: any; project: VideoProject }>(`/api/video/projects/${pid}/clips`, body),
  patchClip: (pid: string, cid: string, body: any) => j<{ clip: any; project: VideoProject }>(`/api/video/projects/${pid}/clips/${cid}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteClip: (pid: string, cid: string) => j<VideoProject>(`/api/video/projects/${pid}/clips/${cid}`, { method: 'DELETE' }),
  reorder: (pid: string, order: string[]) => post<VideoProject>(`/api/video/projects/${pid}/reorder`, { order }),
  split: (pid: string, playhead: number) => post<{ project: VideoProject }>(`/api/video/projects/${pid}/split`, { playhead }),
  shots: (pid: string, clip_id: string) => post<Job>(`/api/video/projects/${pid}/shots`, { clip_id }),
  music: (pid: string, body: any) => post<VideoProject>(`/api/video/projects/${pid}/music`, body),
  render: (pid: string, aspect: string, draft: boolean) => post<Job>(`/api/video/projects/${pid}/render`, { aspect, draft }),
}

export interface AIStatus { available: boolean; backend: string; model: string; claude_logged_in: boolean; account?: string | null; key_set: boolean; picks_logged: number }

export function waitJob(id: string, onProgress?: (j: Job) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const jb = await api.job(id)
        onProgress?.(jb)
        if (jb.status === 'done') return resolve(jb)
        if (jb.status === 'error' || jb.status === 'cancelled') return reject(new Error(jb.error || jb.status))
        setTimeout(tick, 500)
      } catch (e) { reject(e) }
    }
    tick()
  })
}
