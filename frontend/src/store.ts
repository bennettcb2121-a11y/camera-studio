import { create } from 'zustand'
import { api, waitJob } from './api'
import type { Job, LibraryItem, Preset } from './types'

export type Mode = 'library' | 'edit' | 'mockups' | 'video'

interface Toast { id: number; text: string; kind: 'info' | 'error' }

interface State {
  mode: Mode
  setMode: (m: Mode) => void
  folders: string[]
  folder: string | null
  items: LibraryItem[]
  kindFilter: 'all' | 'photo' | 'video'
  minRating: number
  sort: string
  selected: string[]           // multi-selection (paths)
  current: string | null       // the photo open in the editor / mockups
  presets: Preset[]
  jobs: Job[]
  toasts: Toast[]
  aiAvailable: boolean
  clipboard: { source: string } | null
  loadFolders: () => Promise<void>
  setFolder: (f: string | null) => Promise<void>
  loadItems: () => Promise<void>
  setKindFilter: (k: 'all' | 'photo' | 'video') => void
  setMinRating: (r: number) => void
  setSort: (s: string) => void
  select: (path: string, opts?: { shift?: boolean; meta?: boolean }) => void
  setCurrent: (p: string | null) => void
  open: (p: string) => void
  rate: (path: string, rating: number) => Promise<void>
  loadPresets: () => Promise<void>
  trackJob: (job: Job, onDone?: (j: Job) => void, onProgress?: (j: Job) => void) => Promise<Job>
  toast: (text: string, kind?: 'info' | 'error') => void
  checkAI: () => Promise<void>
  setClipboard: (c: { source: string } | null) => void
}

let toastId = 0
const saved = (() => { try { return JSON.parse(localStorage.getItem('ccs') || '{}') } catch { return {} } })()
const persist = (s: Partial<State>) => { try { localStorage.setItem('ccs', JSON.stringify({ current: s.current, mode: s.mode, folder: s.folder })) } catch { /* ignore */ } }

export const useStore = create<State>((set, get) => ({
  mode: saved.current ? saved.mode ?? 'library' : 'library',
  setMode: (mode) => set({ mode }),
  folders: [], folder: saved.folder ?? null, items: [], kindFilter: 'all', minRating: 0, sort: 'name',
  selected: saved.current ? [saved.current] : [], current: saved.current ?? null, presets: [], jobs: [], toasts: [], aiAvailable: false, clipboard: null,

  loadFolders: async () => {
    const { folders } = await api.folders()
    set({ folders })
    if (!get().folder && folders.length) await get().setFolder(folders[folders.length - 1])
    else await get().loadItems()
  },
  setFolder: async (folder) => { set({ folder }); await get().loadItems() },
  loadItems: async () => {
    const { folder, kindFilter, minRating, sort } = get()
    const { items } = await api.items({ folder: folder ?? undefined, kind: kindFilter === 'all' ? undefined : kindFilter, min_rating: minRating, sort })
    set({ items })
  },
  setKindFilter: (kindFilter) => { set({ kindFilter }); get().loadItems() },
  setMinRating: (minRating) => { set({ minRating }); get().loadItems() },
  setSort: (sort) => { set({ sort }); get().loadItems() },
  select: (path, opts = {}) => {
    const { items, selected, current } = get()
    if (opts.meta) {
      set({ selected: selected.includes(path) ? selected.filter((p) => p !== path) : [...selected, path], current: path })
    } else if (opts.shift && current) {
      const a = items.findIndex((i) => i.path === current), b = items.findIndex((i) => i.path === path)
      const [lo, hi] = a < b ? [a, b] : [b, a]
      set({ selected: items.slice(lo, hi + 1).map((i) => i.path), current: path })
    } else set({ selected: [path], current: path })
  },
  setCurrent: (current) => set({ current, selected: current ? [current] : [] }),
  open: (p) => {
    const item = get().items.find((i) => i.path === p)
    set({ current: p, selected: [p], mode: item?.kind === 'video' ? 'video' : 'edit' })
  },
  rate: async (path, rating) => {
    await api.rate(path, rating)
    set({ items: get().items.map((i) => (i.path === path ? { ...i, rating } : i)) })
  },
  loadPresets: async () => { const { presets } = await api.presets(); set({ presets }) },
  trackJob: async (job, onDone, onProgress) => {
    set({ jobs: [job, ...get().jobs].slice(0, 20) })
    const upd = (j: Job) => { set({ jobs: get().jobs.map((x) => (x.id === j.id ? j : x)) }); onProgress?.(j) }
    try {
      const done = await waitJob(job.id, upd)
      upd(done); onDone?.(done)
      return done
    } catch (e: any) {
      get().toast(`${job.kind} failed: ${e.message}`, 'error')
      const j = await api.job(job.id).catch(() => null)
      if (j) upd(j)
      throw e
    }
  },
  toast: (text, kind = 'info') => {
    const id = ++toastId
    set({ toasts: [...get().toasts, { id, text, kind }] })
    setTimeout(() => set({ toasts: get().toasts.filter((t) => t.id !== id) }), kind === 'error' ? 8000 : 3500)
  },
  checkAI: async () => { try { const s = await api.aiStatus(); set({ aiAvailable: s.available }) } catch { /* ignore */ } },
  setClipboard: (clipboard) => set({ clipboard }),
}))

export const isTyping = () => {
  const el = document.activeElement as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable) && (el as HTMLInputElement).type !== 'range'
}

useStore.subscribe((s) => persist(s))
