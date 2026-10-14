import { useEffect, useState } from 'react'
import { api } from '../api'
import { isTyping, useStore } from '../store'
import FolderBrowser from '../components/FolderBrowser'
import { quickExport } from './Editor'
import { GooglePhotosPanel, uploadToGoogle, useGoogle } from '../components/GooglePhotos'

export default function Library() {
  const s = useStore()
  const [browse, setBrowse] = useState(false)
  const [busy, setBusy] = useState(false)

  const addFolder = async (path: string) => {
    setBusy(true)
    try {
      const r = await api.addFolder(path)
      s.toast(`Indexed ${r.items} files in ${r.folder}`)
      await s.loadFolders(); await s.setFolder(r.folder)
    } catch (e: any) { s.toast(e.message, 'error') } finally { setBusy(false); setBrowse(false) }
  }
  const pickNative = async () => {
    const r = await api.pickFolder()
    if (r.path) addFolder(r.path); else setBrowse(true)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping()) return
      const items = s.items
      const idx = items.findIndex((i) => i.path === s.current)
      const cols = Math.max(1, Math.floor((document.getElementById('grid')?.clientWidth ?? 800) / 190))
      const move = (d: number) => { const n = Math.max(0, Math.min(items.length - 1, (idx < 0 ? 0 : idx) + d)); if (items[n]) { s.select(items[n].path, { shift: e.shiftKey }); document.getElementById('it-' + n)?.scrollIntoView({ block: 'nearest' }) } }
      if (e.key === 'ArrowRight') { e.preventDefault(); move(1) }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1) }
      else if (e.key === 'ArrowDown') { e.preventDefault(); move(cols) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-cols) }
      else if (e.key === 'Enter' && s.current) s.open(s.current)
      else if (/^[0-5]$/.test(e.key) && s.selected.length) s.selected.forEach((p) => s.rate(p, Number(e.key)))
      else if (e.key.toLowerCase() === 'f' && !e.metaKey) s.setKindFilter(s.kindFilter === 'all' ? 'photo' : s.kindFilter === 'photo' ? 'video' : 'all')
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); useStore.setState({ selected: items.map((i) => i.path) }) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [s.items, s.current, s.selected, s.kindFilter])

  return (
    <div className="h-full flex">
      <aside className="w-60 border-r border-zinc-800 p-3 flex flex-col gap-3 shrink-0">
        <div className="panel-title">Folders</div>
        <div className="flex gap-1">
          <button className="btn-primary flex-1" onClick={pickNative} disabled={busy}>{busy ? 'Indexing…' : '+ Add folder'}</button>
          <button className="btn" onClick={() => setBrowse(true)} title="Browse on the server">…</button>
        </div>
        <div className="flex flex-col gap-0.5 overflow-auto">
          <div className={`px-2 py-1 rounded text-sm cursor-pointer ${s.folder === null ? 'bg-zinc-800' : 'hover:bg-zinc-900'}`} onClick={() => s.setFolder(null)}>All folders</div>
          {s.folders.map((f) => (
            <div key={f} className={`px-2 py-1 rounded text-sm cursor-pointer flex items-center group ${s.folder === f ? 'bg-zinc-800' : 'hover:bg-zinc-900'}`} onClick={() => s.setFolder(f)} title={f}>
              <span className="truncate flex-1">{f.split('/').filter(Boolean).pop()}</span>
              <button className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 text-xs" onClick={async (e) => { e.stopPropagation(); await api.removeFolder(f); if (s.folder === f) s.setFolder(null); s.loadFolders() }}>✕</button>
            </div>
          ))}
        </div>
        <button className="btn" onClick={async () => { const r = await api.rescan(); s.toast(`Rescanned: ${r.items} files`); s.loadItems() }}>Rescan</button>
        <div className="mt-2"><GooglePhotosPanel /></div>
        <div className="panel-title mt-2">Show</div>
        <div className="flex gap-1">
          {(['all', 'photo', 'video'] as const).map((k) => <button key={k} className={`btn flex-1 ${s.kindFilter === k ? '!bg-zinc-600' : ''}`} onClick={() => s.setKindFilter(k)}>{k === 'all' ? 'All' : k === 'photo' ? 'Photos' : 'Videos'}</button>)}
        </div>
        <div className="flex items-center gap-1 text-sm">
          <span className="text-zinc-500 text-xs mr-1">Min ★</span>
          {[0, 1, 2, 3, 4, 5].map((r) => <button key={r} className={`btn !px-1.5 ${s.minRating === r ? '!bg-zinc-600' : ''}`} onClick={() => s.setMinRating(r)}>{r || '–'}</button>)}
        </div>
        <select className="bg-zinc-800 rounded px-2 py-1 text-sm" value={s.sort} onChange={(e) => s.setSort(e.target.value)}>
          <option value="name">Sort by name</option><option value="date">Sort by date</option><option value="rating">Sort by rating</option>
        </select>
        <div className="text-xs text-zinc-500 mt-auto">{s.items.length} items · {s.selected.length} selected</div>
      </aside>
      <section id="grid" className="flex-1 overflow-auto p-3 pt-0">
        <ActionBar />
        {!s.folders.length && (
          <div className="h-full flex flex-col items-center justify-center text-zinc-500 gap-3">
            <div className="text-lg">No folders yet</div>
            <div className="text-sm">Add a folder with your a6000 photos (.ARW/.JPG) and videos (.MTS/.MP4).</div>
            <button className="btn-primary" onClick={pickNative}>+ Add folder</button>
          </div>
        )}
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(176px, 1fr))' }}>
          {s.items.map((it, i) => {
            const sel = s.selected.includes(it.path), cur = s.current === it.path
            return (
              <div key={it.path} id={'it-' + i}
                className={`rounded overflow-hidden bg-zinc-900 cursor-pointer border-2 ${cur ? 'border-blue-500' : sel ? 'border-blue-900' : 'border-transparent'} hover:border-zinc-600`}
                onClick={(e) => s.select(it.path, { shift: e.shiftKey, meta: e.metaKey || e.ctrlKey })} onDoubleClick={() => s.open(it.path)}>
                <div className="aspect-[3/2] bg-black flex items-center justify-center relative">
                  <img src={api.thumbUrl(it.path)} loading="lazy" className="max-h-full max-w-full object-contain" />
                  {it.kind === 'video' && <span className="absolute top-1 left-1 text-[10px] bg-black/70 px-1 rounded">▶ {it.ext.slice(1).toUpperCase()}</span>}
                  {it.ext === '.arw' && <span className="absolute top-1 left-1 text-[10px] bg-black/70 px-1 rounded">RAW</span>}
                  {!!it.has_edits && <span className="absolute top-1 right-1 text-[10px] bg-blue-600/80 px-1 rounded">edited</span>}
                </div>
                <div className="px-2 py-1 flex items-center">
                  <span className="text-xs truncate flex-1 text-zinc-300" title={it.name}>{it.name}</span>
                  <Stars value={it.rating} onChange={(r) => s.rate(it.path, r)} />
                </div>
              </div>
            )
          })}
        </div>
      </section>
      {browse && <FolderBrowser onPick={addFolder} onClose={() => setBrowse(false)} />}
    </div>
  )
}

export function Stars({ value, onChange }: { value: number; onChange: (r: number) => void }) {
  return (
    <span className="text-[11px] leading-none whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
      {[1, 2, 3, 4, 5].map((r) => <span key={r} className={`cursor-pointer ${r <= value ? 'text-yellow-400' : 'text-zinc-700 hover:text-zinc-400'}`} onClick={() => onChange(r === value ? 0 : r)}>★</span>)}
    </span>
  )
}

/** Bulk actions on the selected photos — the fast path for a whole shoot. */
function ActionBar() {
  const s = useStore()
  const photos = s.selected.filter((p) => s.items.find((i) => i.path === p)?.kind === 'photo')
  const [busy, setBusy] = useState(false)
  const google = useGoogle((g) => g.status)
  if (!photos.length) return <div className="h-3" />
  const n = photos.length
  const run = async (fn: () => Promise<void>) => { setBusy(true); try { await fn() } catch (e: any) { s.toast(e.message, 'error') } finally { setBusy(false); s.loadItems() } }
  return (
    <div className="sticky top-0 z-10 -mx-3 mb-3 px-3 py-2 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 flex items-center gap-2 text-sm">
      <span className="text-zinc-300">{n} photo{n > 1 ? 's' : ''} selected</span>
      <span className="text-zinc-600 text-xs">(Shift/Cmd-click to add, Cmd+A for all)</span>
      <div className="flex-1" />
      <button className="btn" disabled={busy} onClick={() => run(async () => { await s.trackJob(await api.autoMany(photos)); s.toast(`Auto applied to ${n}`) })}>✨ Auto</button>
      <select className="bg-zinc-800 rounded px-2 py-1 text-sm" value="" disabled={busy} onChange={(e) => { const id = e.target.value; if (!id) return
        run(async () => { await api.applyPresetMany(photos, id === '__none' ? null : id); s.toast(id === '__none' ? `Filter removed from ${n}` : `Filter applied to ${n}`) }) }}>
        <option value="">Filter…</option><option value="__none">— remove filter —</option>
        {s.presets.map((p) => <option key={p.id} value={p.id}>{p.group}: {p.name}</option>)}
      </select>
      <button className="btn" disabled={busy || !s.aiAvailable} title={s.aiAvailable ? 'Opus edits each photo' : 'Sign in to Claude first (see the AI tab)'} onClick={() => {
        const note = window.prompt(`✦ Ask Claude to edit ${n} photo${n > 1 ? 's' : ''} (leave empty for a general Auto-edit):`, '')
        if (note === null) return
        run(async () => { const job = await api.aiBatch(photos, note.trim() ? 'ask' : 'auto_edit', note.trim())
          s.toast(`Claude is editing ${n} photo${n > 1 ? 's' : ''} — progress under Jobs`)
          const j = await s.trackJob(job); const ok = j.result.results.filter((r: any) => r.ok).length; s.toast(`Claude edited ${ok}/${n}`) })
      }}>✦ AI edit…</button>
      {google?.connected && <button className="btn" disabled={busy} onClick={() => uploadToGoogle(photos)} title="Edited, full-resolution JPEGs into the “Charlie's Camera Studio” album">⬆ Google Photos</button>}
      <button className="btn-primary" disabled={busy} onClick={() => quickExport(photos, useStore.getState())} title="Uses your last export settings">Export {n}</button>
      <button className="btn" onClick={() => useStore.setState({ selected: [] })}>✕</button>
    </div>
  )
}
