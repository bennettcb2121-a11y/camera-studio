import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { isTyping, useStore } from '../store'
import type { VideoClip, VideoProject } from '../types'
import Modal from '../components/Modal'

const clipDur = (c: VideoClip) => (c.type === 'video' ? Math.max(0, ((c.out ?? 0) - (c.in ?? 0)) / (c.speed || 1)) : c.duration ?? 3)
const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`

export default function Video() {
  const store = useStore()
  const media = useMemo(() => store.items.filter((i) => i.kind === 'video' || i.kind === 'photo'), [store.items])
  const [projects, setProjects] = useState<{ id: string; name: string; clips: number }[]>([])
  const [proj, setProj] = useState<VideoProject | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [playhead, setPlayhead] = useState(0)
  const [player, setPlayer] = useState<{ src: string; label: string; kind: 'proxy' | 'render' } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [reelOpen, setReelOpen] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const dragId = useRef<string | null>(null)

  const refreshProjects = async () => { const r = await api.projects(); setProjects(r.projects); return r.projects }
  useEffect(() => { refreshProjects().then(async (ps) => { if (ps.length) setProj(await api.project(ps[0].id)); else setProj(await api.createProject('My reel')) }) }, [])
  const update = (p: VideoProject) => { setProj(p); refreshProjects() }
  const selClip = proj?.clips.find((c) => c.id === sel) ?? null
  const total = proj ? proj.clips.reduce((a, c) => a + clipDur(c), 0) : 0
  const clipStart = (id: string) => { let t = 0; for (const c of proj!.clips) { if (c.id === id) return t; t += clipDur(c) } return 0 }

  // --- media bin actions
  const addMedia = async (path: string, kind: string) => {
    if (!proj) return
    if (kind === 'video') {
      store.toast('Making a 720p proxy…')
      const r = await api.addClip(proj.id, { type: 'video', src: path })
      update(r.project); setSel(r.clip.id)
    } else { const r = await api.addClip(proj.id, { type: 'photo', src: path, duration: 4 }); update(r.project); setSel(r.clip.id) }
  }
  const addTitle = async () => { if (!proj) return; const text = prompt('Title text', 'My reel'); if (!text) return; const r = await api.addClip(proj.id, { type: 'title', text, duration: 3 }); update(r.project); setSel(r.clip.id) }
  const patch = async (cid: string, body: any) => { if (!proj) return; const r = await api.patchClip(proj.id, cid, body); update(r.project) }
  const del = async () => { if (!proj || !sel) return; update(await api.deleteClip(proj.id, sel)); setSel(null) }
  const split = async () => { if (!proj) return; const r = await api.split(proj.id, playhead); update(r.project); if (r.project.clips.length) store.toast('Split') }
  const shots = async () => {
    if (!proj || !selClip || selClip.type !== 'video') return store.toast('Select a video clip first', 'error')
    const job = await api.shots(proj.id, selClip.id)
    store.trackJob(job, (j) => { update(j.result.project); store.toast(`Split into ${j.result.shots.length} shots`) })
  }
  const move = async (from: number, to: number) => {
    if (!proj || from === to) return
    const ids = proj.clips.map((c) => c.id); const [x] = ids.splice(from, 1); ids.splice(to, 0, x)
    update(await api.reorder(proj.id, ids))
  }
  const previewClip = (c: VideoClip) => {
    if (c.type === 'video' && c.src) setPlayer({ src: api.proxyUrl(c.src), label: c.src.split('/').pop()!, kind: 'proxy' })
    else if (c.type === 'photo' && c.src) setPlayer({ src: api.thumbUrl(c.src), label: 'photo', kind: 'proxy' })
    else setPlayer(null)
  }
  useEffect(() => { if (selClip) previewClip(selClip) }, [sel])
  useEffect(() => {
    const v = videoRef.current
    if (!v || !selClip || selClip.type !== 'video' || player?.kind !== 'proxy') return
    const onLoaded = () => { v.currentTime = selClip.in ?? 0 }
    const onTime = () => { if (v.currentTime >= (selClip.out ?? v.duration)) { v.pause(); v.currentTime = selClip.in ?? 0 } setPlayhead(clipStart(selClip.id) + (v.currentTime - (selClip.in ?? 0)) / (selClip.speed || 1)) }
    v.addEventListener('loadedmetadata', onLoaded); v.addEventListener('timeupdate', onTime)
    v.playbackRate = Math.min(4, Math.max(0.25, selClip.speed || 1))
    return () => { v.removeEventListener('loadedmetadata', onLoaded); v.removeEventListener('timeupdate', onTime) }
  }, [player?.src, selClip?.in, selClip?.out, selClip?.speed])

  const renderDraft = async () => {
    if (!proj || !proj.clips.length) return
    const job = await api.render(proj.id, '16:9', true)
    store.trackJob(job, (j) => { setPlayer({ src: api.fileUrl(j.result.file), label: 'draft render', kind: 'render' }); setSel(null) })
  }

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (isTyping() || exportOpen || reelOpen) return
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'e') { e.preventDefault(); setExportOpen(true); return }
      if (mod) return
      if (e.key === ' ') { e.preventDefault(); const v = videoRef.current; if (v) v.paused ? v.play() : v.pause() }
      else if (e.key.toLowerCase() === 's') split()
      else if (e.key === 'Delete' || e.key === 'Backspace') del()
      else if (e.key === 'ArrowRight') setPlayhead((p) => Math.min(total, p + (e.shiftKey ? 0.1 : 1)))
      else if (e.key === 'ArrowLeft') setPlayhead((p) => Math.max(0, p - (e.shiftKey ? 0.1 : 1)))
      else if (e.key === '[' && selClip?.type === 'video') { const v = videoRef.current; if (v) patch(selClip.id, { in: v.currentTime }) }
      else if (e.key === ']' && selClip?.type === 'video') { const v = videoRef.current; if (v) patch(selClip.id, { out: v.currentTime }) }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [proj, sel, playhead, total, exportOpen, reelOpen])

  if (!proj) return <div className="p-6 text-zinc-500">Loading…</div>
  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 flex min-h-0">
        <aside className="w-64 border-r border-zinc-800 flex flex-col shrink-0">
          <div className="p-2 border-b border-zinc-800 flex gap-1 items-center">
            <select className="bg-zinc-800 rounded px-1 py-1 text-xs flex-1" value={proj.id} onChange={async (e) => setProj(await api.project(e.target.value))}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.clips})</option>)}</select>
            <button className="btn !px-1.5" title="New project" onClick={async () => { const n = prompt('Project name', 'New reel'); if (n) { const p = await api.createProject(n); update(p); setSel(null) } }}>+</button>
            <button className="btn !px-1.5" title="Rename" onClick={async () => { const n = prompt('Rename', proj.name); if (n) update(await api.saveProject({ ...proj, name: n })) }}>✎</button>
            <button className="btn !px-1.5" title="Delete project" onClick={async () => { if (confirm('Delete this project?')) { await api.deleteProject(proj.id); const ps = await refreshProjects(); setProj(ps.length ? await api.project(ps[0].id) : await api.createProject('My reel')) } }}>🗑</button>
          </div>
          <div className="panel-title px-2 pt-2">Media (library)</div>
          <div className="flex-1 overflow-auto p-2 space-y-1">
            {media.map((m) => (
              <div key={m.path} className="flex items-center gap-2 text-xs group">
                <img src={api.thumbUrl(m.path)} className="w-14 h-9 object-cover rounded bg-black" loading="lazy" />
                <span className="truncate flex-1" title={m.name}>{m.name}</span>
                <button className="btn !px-1.5 !py-0 opacity-60 group-hover:opacity-100" onClick={() => addMedia(m.path, m.kind)}>+</button>
              </div>
            ))}
            {!media.length && <div className="text-xs text-zinc-500">Add a folder in the Library first.</div>}
          </div>
          <div className="p-2 border-t border-zinc-800 space-y-1">
            <button className="btn w-full" onClick={addTitle}>+ Title card</button>
            <MusicPicker proj={proj} onChange={update} />
            <button className="btn w-full" onClick={() => setReelOpen(true)} disabled={!store.aiAvailable} title={store.aiAvailable ? '' : 'Needs an API key'}>✨ AI: make a 30s reel</button>
          </div>
        </aside>

        <div className="flex-1 flex min-w-0">
          <div className="flex-1 bg-black flex flex-col items-center justify-center relative min-w-0">
            {player ? (player.src.includes('/thumb') ? <img src={player.src} className="max-h-full max-w-full" /> :
              <video ref={videoRef} key={player.src} src={player.src} controls className="max-h-full max-w-full" />) :
              <div className="text-zinc-600 text-sm">Select a clip to preview it, or render a draft of the whole timeline.</div>}
            {player && <span className="absolute top-2 left-2 text-xs bg-black/70 px-2 py-0.5 rounded">{player.label}</span>}
          </div>
          <aside className="w-72 border-l border-zinc-800 p-3 text-sm overflow-auto shrink-0">
            {selClip ? <ClipInspector clip={selClip} onPatch={(b) => patch(selClip.id, b)} /> : <div className="text-zinc-500 text-xs">Select a clip on the timeline to trim it, change speed, add a look or a title.</div>}
          </aside>
        </div>
      </div>

      <div className="h-44 border-t border-zinc-800 flex flex-col shrink-0">
        <div className="h-9 flex items-center gap-1 px-3 text-xs border-b border-zinc-800">
          <span className="text-zinc-400 w-24">{fmt(playhead)} / {fmt(total)}</span>
          <button className="btn" onClick={split} title="S">✂ Split at playhead</button>
          <button className="btn" onClick={shots}>Split into shots</button>
          <button className="btn" onClick={del} disabled={!sel} title="Delete">Remove</button>
          <button className="btn" onClick={() => { const i = proj.clips.findIndex((c) => c.id === sel); if (i > 0) move(i, i - 1) }} disabled={!sel}>◀ Move</button>
          <button className="btn" onClick={() => { const i = proj.clips.findIndex((c) => c.id === sel); if (i >= 0 && i < proj.clips.length - 1) move(i, i + 1) }} disabled={!sel}>Move ▶</button>
          <div className="flex-1" />
          <button className="btn" onClick={renderDraft} disabled={!proj.clips.length}>▶ Render draft (720p)</button>
          <button className="btn-primary" onClick={() => setExportOpen(true)} disabled={!proj.clips.length} title="Cmd+E">Export…</button>
        </div>
        <div className="flex-1 relative overflow-x-auto px-3 py-2" onClick={(e) => { if (e.target === e.currentTarget) setSel(null) }}>
          <div className="relative h-full flex gap-0.5 min-w-full" style={{ width: Math.max(600, total * 40) }}>
            {proj.clips.map((c, i) => {
              const w = Math.max(48, clipDur(c) * 40)
              return (
                <div key={c.id} draggable onDragStart={() => (dragId.current = c.id)} onDragOver={(e) => e.preventDefault()} onDrop={() => { const from = proj.clips.findIndex((x) => x.id === dragId.current); if (from >= 0) move(from, i) }}
                  onClick={() => { setSel(c.id); setPlayhead(clipStart(c.id)) }}
                  className={`h-full rounded overflow-hidden cursor-pointer border-2 shrink-0 relative ${sel === c.id ? 'border-blue-500' : 'border-transparent'} ${c.type === 'title' ? 'bg-zinc-700' : c.type === 'photo' ? 'bg-emerald-900' : 'bg-indigo-900'}`} style={{ width: w }}>
                  {c.src && c.type !== 'title' && <img src={c.type === 'video' ? api.videoThumbUrl(c.src, c.in ?? 0.5, 160) : api.thumbUrl(c.src)} className="absolute inset-0 w-full h-full object-cover opacity-60" />}
                  <div className="relative p-1 text-[10px] leading-tight">
                    <div className="truncate font-medium">{c.type === 'title' ? `T: ${c.text}` : c.src?.split('/').pop()}</div>
                    <div className="text-zinc-300">{clipDur(c).toFixed(1)}s{c.speed && c.speed !== 1 ? ` ×${c.speed}` : ''}{c.look?.preset_id ? ` · ${c.look.preset_id}` : ''}{c.title?.text ? ' · title' : ''}</div>
                  </div>
                </div>
              )
            })}
            <div className="absolute top-0 bottom-0 w-px bg-red-500 pointer-events-none" style={{ left: playhead * 40 }} />
          </div>
        </div>
      </div>
      {exportOpen && <ExportModal proj={proj} onClose={() => setExportOpen(false)} onRendered={(f) => setPlayer({ src: api.fileUrl(f), label: 'export', kind: 'render' })} />}
      {reelOpen && <ReelModal proj={proj} media={media.filter((m) => m.kind === 'video').map((m) => m.path)} onClose={() => setReelOpen(false)} onDone={(p) => { update(p); setReelOpen(false) }} />}
    </div>
  )
}

function ClipInspector({ clip: c, onPatch }: { clip: VideoClip; onPatch: (b: any) => void }) {
  const presets = useStore((s) => s.presets)
  const [title, setTitle] = useState(c.title?.text ?? '')
  useEffect(() => setTitle(c.title?.text ?? ''), [c.id])
  const num = (label: string, key: string, value: number, step = 0.1, min = 0, max = 9999) => (
    <label className="flex items-center gap-2 text-xs">{label}<input type="number" step={step} min={min} max={max} className="bg-zinc-800 rounded px-1 py-0.5 w-20 ml-auto" value={Number(value.toFixed(2))} onChange={(e) => onPatch({ [key]: Number(e.target.value) })} /></label>
  )
  return (
    <div className="space-y-3">
      <div className="panel-title">{c.type} clip</div>
      {c.type === 'video' && (<>
        {num('In (s)', 'in', c.in ?? 0)}
        {num('Out (s)', 'out', c.out ?? 0, 0.1, 0, c.src_duration ?? 9999)}
        <label className="flex items-center gap-2 text-xs">Speed<select className="bg-zinc-800 rounded px-1 ml-auto" value={c.speed ?? 1} onChange={(e) => onPatch({ speed: Number(e.target.value) })}>{[0.25, 0.5, 0.75, 1, 1.5, 2, 4].map((s) => <option key={s} value={s}>×{s}</option>)}</select></label>
        <div className="text-[11px] text-zinc-500">Keys: <kbd>[</kbd> / <kbd>]</kbd> set in/out at the player position.</div>
      </>)}
      {c.type !== 'video' && num('Duration (s)', 'duration', c.duration ?? 3, 0.5, 0.5, 60)}
      {c.type === 'photo' && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={c.zoom !== false} onChange={(e) => onPatch({ zoom: e.target.checked })} /> Slow zoom (Ken Burns)</label>}
      {c.type === 'title' && (<>
        <label className="block text-xs">Text<input className="w-full bg-zinc-800 rounded px-2 py-1 mt-1" value={c.text ?? ''} onChange={(e) => onPatch({ text: e.target.value })} /></label>
        {num('Size', 'size', c.size ?? 72, 4, 16, 200)}
      </>)}
      {c.type !== 'title' && (<>
        <div className="panel-title">Look (photo preset / LUT)</div>
        <select className="bg-zinc-800 rounded px-1 py-1 w-full text-xs" value={c.look?.preset_id ?? ''} onChange={(e) => onPatch({ look: e.target.value ? { preset_id: e.target.value, strength: c.look?.strength ?? 1 } : null })}>
          <option value="">— none —</option>{presets.map((p) => <option key={p.id} value={p.id}>{p.group}: {p.name}</option>)}
        </select>
        {c.look?.preset_id && <label className="flex items-center gap-2 text-xs">Strength<input type="range" min={0} max={1} step={0.05} value={c.look.strength ?? 1} className="flex-1" onChange={(e) => onPatch({ look: { ...c.look, strength: Number(e.target.value) } })} /></label>}
        <div className="panel-title">Title overlay</div>
        <input className="w-full bg-zinc-800 rounded px-2 py-1 text-xs" placeholder="Text shown on this clip" value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => onPatch({ title: title ? { ...(c.title ?? {}), text: title, position: c.title?.position ?? 'bottom', size: c.title?.size ?? 56 } : null })} />
        {c.title?.text && <label className="flex items-center gap-2 text-xs">Position<select className="bg-zinc-800 rounded px-1 ml-auto" value={c.title.position ?? 'bottom'} onChange={(e) => onPatch({ title: { ...c.title, position: e.target.value } })}><option>top</option><option>center</option><option>bottom</option></select></label>}
      </>)}
    </div>
  )
}

function MusicPicker({ proj, onChange }: { proj: VideoProject; onChange: (p: VideoProject) => void }) {
  const [open, setOpen] = useState(false)
  const [src, setSrc] = useState(proj.music?.src ?? '')
  const [vol, setVol] = useState(proj.music?.volume ?? 0.8)
  const [fi, setFi] = useState(proj.music?.fade_in ?? 1)
  const [fo, setFo] = useState(proj.music?.fade_out ?? 2)
  const [keep, setKeep] = useState(proj.music?.keep_clip_audio ?? true)
  return (<>
    <button className="btn w-full" onClick={() => setOpen(true)}>♫ Music {proj.music ? '✓' : ''}</button>
    {open && (
      <Modal title="Music track" onClose={() => setOpen(false)}>
        <div className="space-y-3 text-sm">
          <label className="block text-xs">Audio file path (.m4a/.mp3/.wav)
            <div className="flex gap-1 mt-1"><input className="flex-1 bg-zinc-800 rounded px-2 py-1" value={src} onChange={(e) => setSrc(e.target.value)} placeholder="/Users/you/Music/track.mp3" />
              <button className="btn" onClick={async () => { const r = await fetch('/api/library/pick-file', { method: 'POST' }).then((r) => r.json()); if (r.path) setSrc(r.path) }}>Browse…</button></div></label>
          <label className="flex items-center gap-2 text-xs">Volume <input type="range" min={0} max={1} step={0.05} value={vol} onChange={(e) => setVol(Number(e.target.value))} /> {Math.round(vol * 100)}%</label>
          <label className="flex items-center gap-2 text-xs">Fade in (s) <input type="number" className="bg-zinc-800 rounded px-1 w-16" step={0.5} value={fi} onChange={(e) => setFi(Number(e.target.value))} /></label>
          <label className="flex items-center gap-2 text-xs">Fade out (s) <input type="number" className="bg-zinc-800 rounded px-1 w-16" step={0.5} value={fo} onChange={(e) => setFo(Number(e.target.value))} /></label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} /> Keep the clips' own audio underneath</label>
          <div className="flex justify-end gap-2">
            <button className="btn" onClick={async () => { onChange(await api.music(proj.id, {})); setOpen(false) }}>Remove music</button>
            <button className="btn-primary" onClick={async () => { try { onChange(await api.music(proj.id, { src, volume: vol, fade_in: fi, fade_out: fo, keep_clip_audio: keep })); setOpen(false) } catch (e: any) { alert(e.message) } }}>Save</button>
          </div>
        </div>
      </Modal>
    )}
  </>)
}

function ExportModal({ proj, onClose, onRendered }: { proj: VideoProject; onClose: () => void; onRendered: (file: string) => void }) {
  const store = useStore()
  const [aspect, setAspect] = useState('9:16')
  return (
    <Modal title="Export video" onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="flex gap-2">{[['9:16', 'Reels / TikTok 1080×1920'], ['16:9', 'YouTube 1920×1080'], ['1:1', 'Square 1080×1080']].map(([a, l]) => <button key={a} className={`btn flex-1 ${aspect === a ? '!bg-blue-700' : ''}`} onClick={() => setAspect(a)}><div className="font-medium">{a}</div><div className="text-[10px] text-zinc-400">{l}</div></button>)}</div>
        <div className="text-xs text-zinc-500">Rendered from the original files (not the proxies) with H.264 + AAC into <code>exports/</code>. Looks are applied with FFmpeg lut3d so they match the photo filters.</div>
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={async () => { const job = await api.render(proj.id, aspect, false); onClose(); store.trackJob(job, (j) => { store.toast(`Exported ${j.result.file}`); onRendered(j.result.file) }) }}>Export</button></div>
      </div>
    </Modal>
  )
}

function ReelModal({ proj, media, onClose, onDone }: { proj: VideoProject; media: string[]; onClose: () => void; onDone: (p: VideoProject) => void }) {
  const store = useStore()
  const [src, setSrc] = useState(media[0] ?? '')
  const [secs, setSecs] = useState(30)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <Modal title="AI: make a reel" onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="text-xs text-zinc-400">Claude gets a contact sheet (1 frame per second) of the clip, returns a cut list, and the timeline is rebuilt from it. You can then trim, reorder and add a look before exporting.</div>
        <label className="block text-xs">Source clip<select className="w-full bg-zinc-800 rounded px-1 py-1 mt-1" value={src} onChange={(e) => setSrc(e.target.value)}>{media.map((m) => <option key={m} value={m}>{m.split('/').pop()}</option>)}</select></label>
        <label className="flex items-center gap-2 text-xs">Target length (s)<input type="number" className="bg-zinc-800 rounded px-1 w-20" value={secs} onChange={(e) => setSecs(Number(e.target.value))} /></label>
        <textarea className="w-full bg-zinc-800 rounded px-2 py-1 text-xs h-14" placeholder="Optional note (e.g. 'energetic, start on the wide shot')" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!src || busy} onClick={async () => { setBusy(true); try { const job = await api.aiReel(proj.id, src, secs, note); const j = await store.trackJob(job); store.toast(`Reel: ${j.result.cut_list.summary}`); onDone(j.result.project) } catch { /* toast shown */ } finally { setBusy(false) } }}>{busy ? 'Working…' : 'Make reel'}</button></div>
      </div>
    </Modal>
  )
}
