import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { isTyping, useStore } from '../store'
import type { MockupTemplate } from '../types'
import Modal from '../components/Modal'

export default function Mockups() {
  const store = useStore()
  const photos = useMemo(() => store.items.filter((i) => i.kind === 'photo'), [store.items])
  const [templates, setTemplates] = useState<MockupTemplate[]>([])
  const [ti, setTi] = useState(0)
  const [url, setUrl] = useState<string | null>(null)
  const [favs, setFavs] = useState<string[]>([])
  const [editor, setEditor] = useState(false)
  const [loading, setLoading] = useState(false)
  const path = store.current!
  const pi = Math.max(0, photos.findIndex((p) => p.path === path))
  const others = photos.filter((p) => p.path !== path).map((p) => p.path)
  const t = templates[ti]

  const loadTemplates = () => api.templates().then((r) => setTemplates(r.templates))
  useEffect(() => { loadTemplates() }, [])
  useEffect(() => { api.info(path).then((i) => setFavs(i.mockup_favorites)) }, [path])
  useEffect(() => {
    if (!t) return
    let alive = true
    setLoading(true)
    api.renderMockup(t.id, [path, ...others.slice(0, 8)], null).then((u) => { if (alive) { setUrl((o) => { if (o) URL.revokeObjectURL(o); return u }); setLoading(false) } }).catch((e) => { store.toast(e.message, 'error'); setLoading(false) })
    return () => { alive = false }
  }, [t?.id, path])

  const nextT = (d: number) => templates.length && setTi((i) => (i + d + templates.length) % templates.length)
  const nextP = (d: number) => { if (photos.length) store.setCurrent(photos[(pi + d + photos.length) % photos.length].path) }
  const toggleFav = async () => { if (!t) return; const r = await api.favoriteMockup(path, t.id, !favs.includes(t.id)); setFavs(r.mockup_favorites) }
  const exportAll = async () => {
    const job = await api.exportMockups(path, others.slice(0, 8))
    store.trackJob(job, (j) => store.toast(`Mockups ZIP: ${j.result.zip}`))
  }

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (isTyping() || editor) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'e') { e.preventDefault(); exportAll(); return }
      if (e.metaKey || e.ctrlKey) return
      if (e.key === 'ArrowRight') nextT(1); else if (e.key === 'ArrowLeft') nextT(-1)
      else if (e.key === 'ArrowDown') nextP(1); else if (e.key === 'ArrowUp') nextP(-1)
      else if (e.key.toLowerCase() === 'f') toggleFav()
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [templates, ti, pi, photos, favs, editor, path])

  return (
    <div className="h-full flex">
      <aside className="w-60 border-r border-zinc-800 p-3 flex flex-col gap-2 shrink-0 overflow-auto">
        <div className="panel-title">Mockups <span className="text-zinc-600 normal-case">← → cycle</span></div>
        {templates.map((x, i) => (
          <div key={x.id} className={`px-2 py-1 rounded text-sm cursor-pointer flex items-center ${i === ti ? 'bg-zinc-800' : 'hover:bg-zinc-900'}`} onClick={() => setTi(i)}>
            <span className="flex-1 truncate">{x.name}</span>{favs.includes(x.id) && <span className="text-yellow-400 text-xs">★</span>}
          </div>
        ))}
        <button className="btn mt-2" onClick={() => setEditor(true)}>+ New template…</button>
        <button className="btn" onClick={exportAll} title="Cmd+E">Export all as ZIP</button>
        <div className="panel-title mt-3">Photo <span className="text-zinc-600 normal-case">↑ ↓ cycle</span></div>
        <div className="text-xs text-zinc-400 truncate">{photos[pi]?.name}</div>
        <div className="flex gap-1"><button className="btn flex-1" onClick={() => nextP(-1)}>↑ prev</button><button className="btn flex-1" onClick={() => nextP(1)}>↓ next</button></div>
        <div className="text-[11px] text-zinc-500 mt-2">Multi-slot templates are filled with the other photos in the library. Uses the current edit (with its filter).</div>
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="h-10 flex items-center gap-2 px-3 border-b border-zinc-800 text-sm">
          <button className="btn" onClick={() => nextT(-1)}>◀</button><button className="btn" onClick={() => nextT(1)}>▶</button>
          <span className="text-zinc-300">{t?.name}</span>
          <button className={`btn ${favs.includes(t?.id ?? '') ? 'text-yellow-400' : ''}`} onClick={toggleFav} title="F">★ Favourite</button>
          <div className="flex-1" />
          {t && <button className="btn" onClick={async () => { if (confirm(`Delete template "${t.name}"?`)) { await api.deleteTemplate(t.id); setTi(0); loadTemplates() } }}>Delete template</button>}
          <span className="text-xs text-zinc-500">{loading ? 'rendering…' : ''}</span>
        </div>
        <div className="flex-1 bg-black flex items-center justify-center p-3 min-h-0">
          {url && <img src={url} className="max-w-full max-h-full object-contain" />}
        </div>
      </div>
      {editor && <TemplateEditor onClose={() => setEditor(false)} onSaved={(id) => { setEditor(false); loadTemplates().then(() => setTi(Math.max(0, templates.findIndex((x) => x.id === id)))) }} />}
    </div>
  )
}

function TemplateEditor({ onClose, onSaved }: { onClose: () => void; onSaved: (id: string) => void }) {
  const store = useStore()
  const photos = store.items.filter((i) => i.kind === 'photo')
  const [bg, setBg] = useState<{ path: string; url: string; w: number; h: number } | null>(null)
  const [name, setName] = useState('My mockup')
  const [corners, setCorners] = useState<number[][]>([])
  const [mode, setMode] = useState<'normal' | 'multiply'>('normal')
  const imgRef = useRef<HTMLImageElement>(null)
  const drag = useRef<number | null>(null)
  const useImage = (path: string, w: number, h: number, url: string) => {
    setBg({ path, url, w, h }); setCorners([[w * 0.25, h * 0.25], [w * 0.75, h * 0.25], [w * 0.75, h * 0.75], [w * 0.25, h * 0.75]])
  }
  const onFile = async (f: File) => { const r = await api.uploadBackground(f); useImage(r.path, r.width, r.height, URL.createObjectURL(f)) }
  const pickLibrary = async (p: string) => { const i = await api.info(p); useImage(p, i.width, i.height, api.thumbUrl(p)); const u = await api.preview(p, null, 1600, false, false); setBg((b) => b && { ...b, url: u.url }) }
  const toImg = (e: React.PointerEvent) => { const r = imgRef.current!.getBoundingClientRect(); return [((e.clientX - r.left) / r.width) * bg!.w, ((e.clientY - r.top) / r.height) * bg!.h] }
  const save = async () => {
    if (!bg) return
    const rec = await api.saveTemplate({ name, slots: [{ corners: corners.map((c) => c.map(Math.round)) }], blend: { mode, opacity: 1, fit: 'cover' }, background_path: bg.path })
    store.toast(`Template "${rec.name}" saved`); onSaved(rec.id)
  }
  return (
    <Modal title="New mockup template — drag the 4 corners onto the picture area" onClose={onClose} wide>
      <div className="flex gap-4 h-full">
        <div className="w-64 space-y-3 text-sm shrink-0">
          <label className="block">Name<input className="w-full bg-zinc-800 rounded px-2 py-1 mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div><div className="panel-title mb-1">Background</div>
            <input type="file" accept="image/*" className="text-xs" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
            <div className="text-xs text-zinc-500 my-1">or pick from the library:</div>
            <div className="max-h-48 overflow-auto border border-zinc-800 rounded">{photos.map((p) => <div key={p.path} className="px-2 py-0.5 text-xs cursor-pointer hover:bg-zinc-800 truncate" onClick={() => pickLibrary(p.path)}>{p.name}</div>)}</div>
          </div>
          <label className="flex items-center gap-2 text-xs">Blend <select className="bg-zinc-800 rounded px-1" value={mode} onChange={(e) => setMode(e.target.value as any)}><option value="normal">normal</option><option value="multiply">multiply</option></select></label>
          <div className="text-xs text-zinc-500">Corner order: top-left, top-right, bottom-right, bottom-left. Multiply blend lets screen glare / paper texture show through.</div>
          <button className="btn-primary w-full" disabled={!bg} onClick={save}>Save template</button>
        </div>
        <div className="flex-1 bg-black rounded flex items-center justify-center min-w-0 relative select-none">
          {bg ? (
            <div className="relative" onPointerMove={(e) => { if (drag.current !== null) { const [x, y] = toImg(e); setCorners((c) => c.map((p, i) => (i === drag.current ? [Math.max(0, Math.min(bg.w, x)), Math.max(0, Math.min(bg.h, y))] : p))) } }} onPointerUp={() => (drag.current = null)}>
              <img ref={imgRef} src={bg.url} className="max-h-[75vh] max-w-full object-contain block" draggable={false} />
              <svg className="absolute inset-0 w-full h-full" viewBox={`0 0 ${bg.w} ${bg.h}`} preserveAspectRatio="none">
                <polygon points={corners.map((c) => c.join(',')).join(' ')} fill="rgba(96,165,250,0.25)" stroke="#60a5fa" strokeWidth={Math.max(2, bg.w / 400)} vectorEffect="non-scaling-stroke" />
              </svg>
              {corners.map((c, i) => (
                <div key={i} className="absolute w-5 h-5 -ml-2.5 -mt-2.5 rounded-full bg-blue-400 border-2 border-white cursor-grab flex items-center justify-center text-[9px] text-black font-bold" style={{ left: `${(c[0] / bg.w) * 100}%`, top: `${(c[1] / bg.h) * 100}%` }}
                  onPointerDown={(e) => { drag.current = i; (e.currentTarget as Element).setPointerCapture(e.pointerId) }}>{['TL', 'TR', 'BR', 'BL'][i]}</div>
              ))}
            </div>
          ) : <div className="text-zinc-500">Choose a background image</div>}
        </div>
      </div>
    </Modal>
  )
}
