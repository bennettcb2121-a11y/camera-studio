import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { isTyping, useStore } from '../store'
import { ASPECTS, HSL_BANDS, defaultSettings, type EditSettings, type History, type Histogram as H, type PhotoInfo } from '../types'
import Slider from '../components/Slider'
import Histogram from '../components/Histogram'
import CurveEditor from '../components/CurveEditor'
import Modal from '../components/Modal'
import { Stars } from './Library'

type Tab = 'adjust' | 'ai' | 'history'

export default function Editor() {
  const store = useStore()
  const path = store.current!
  const photos = useMemo(() => store.items.filter((i) => i.kind === 'photo'), [store.items])
  const [info, setInfo] = useState<PhotoInfo | null>(null)
  const [settings, setSettingsState] = useState<EditSettings>(defaultSettings())
  const settingsRef = useRef(settings)
  const [history, setHistory] = useState<History>({ index: -1, entries: [] })
  const [preview, setPreview] = useState<string | null>(null)
  const [beforeUrl, setBeforeUrl] = useState<string | null>(null)
  const [hist, setHist] = useState<H | null>(null)
  const [showBefore, setShowBefore] = useState(false)
  const [cropMode, setCropMode] = useState(false)
  const [tab, setTab] = useState<Tab>('adjust')
  const [strip, setStrip] = useState<{ id: string | null; name: string; group: string; thumb: string }[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [renderMs, setRenderMs] = useState(0)
  const [hslMode, setHslMode] = useState<'hue' | 'sat' | 'lum'>('sat')
  const stripRef = useRef<HTMLDivElement>(null)

  // ---- preview scheduling: latest request wins, never more than one in flight
  const inflight = useRef(false)
  const queued = useRef<{ s: EditSettings; size: number; crop: boolean } | null>(null)
  const runPreview = useCallback(async (s: EditSettings, size: number, uncropped = false) => {
    if (inflight.current) { queued.current = { s, size, crop: uncropped }; return }
    inflight.current = true
    try {
      const eff = uncropped ? { ...s, crop: { ...s.crop, x: 0, y: 0, w: 1, h: 1 } } : s
      const r = await api.preview(path, eff, size)
      setPreview((old) => { if (old) URL.revokeObjectURL(old); return r.url })
      if (r.histogram) setHist(r.histogram)
      setRenderMs(r.ms)
    } catch (e: any) { store.toast(e.message, 'error') } finally {
      inflight.current = false
      if (queued.current) { const q = queued.current; queued.current = null; runPreview(q.s, q.size, q.crop) }
    }
  }, [path])

  const load = useCallback(async () => {
    const i = await api.info(path)
    setInfo(i); setSettingsState(i.settings); settingsRef.current = i.settings; setHistory(i.history)
    setBeforeUrl(null); setCropMode(false)
    runPreview(i.settings, 2048)
    api.filterStrip(path).then((r) => setStrip(r.filters)).catch(() => {})
  }, [path, runPreview])
  useEffect(() => { load() }, [load])

  // live change (slider drag) vs commit (release → history entry + full-res preview)
  const change = (partial: Partial<EditSettings> | ((s: EditSettings) => EditSettings)) => {
    const next = typeof partial === 'function' ? partial(settingsRef.current) : { ...settingsRef.current, ...partial }
    settingsRef.current = next; setSettingsState(next)
    runPreview(next, 1024, cropMode)
  }
  const commit = async (label: string, partial?: Partial<EditSettings>) => {
    const next = partial ? { ...settingsRef.current, ...partial } : settingsRef.current
    settingsRef.current = next; setSettingsState(next)
    runPreview(next, 2048, cropMode)
    try {
      const r = await api.setSettings(path, next, label)
      setHistory(r.history)
      store.loadItems()
    } catch (e: any) { store.toast(e.message, 'error') }
  }
  const applyResult = (r: { settings: EditSettings; history: History }) => {
    settingsRef.current = r.settings; setSettingsState(r.settings); setHistory(r.history); runPreview(r.settings, 2048, cropMode)
    api.filterStrip(path).then((x) => setStrip(x.filters)).catch(() => {})
  }
  const undo = async () => applyResult(await api.undo(path))
  const redo = async () => applyResult(await api.redo(path))
  const auto = async () => { applyResult(await api.auto(path)); store.toast('Auto applied') }
  const reset = async () => applyResult(await api.reset(path))
  const applyFilter = async (id: string | null, strength?: number) => applyResult(await api.applyPreset(path, id, strength))

  const cycleFilter = (d: number) => {
    if (!strip.length) return
    const idx = strip.findIndex((f) => f.id === settings.lut.name)
    const n = (idx + d + strip.length) % strip.length
    applyFilter(strip[n].id)
    stripRef.current?.children[n]?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' })
  }
  const movePhoto = (d: number) => {
    const i = photos.findIndex((p) => p.path === path)
    const n = photos[(i + d + photos.length) % photos.length]
    if (n) store.setCurrent(n.path)
  }
  const toggleBefore = async (on: boolean) => {
    if (on && !beforeUrl) { const r = await api.preview(path, settingsRef.current, 2048, true, false); setBeforeUrl(r.url) }
    setShowBefore(on)
  }
  const copy = () => { store.setClipboard({ source: path }); store.toast('Settings copied — select photos and press Cmd/Ctrl+V') }
  const paste = async () => {
    if (!store.clipboard) return store.toast('Nothing copied yet', 'error')
    const targets = store.selected.filter((p) => p !== store.clipboard!.source)
    if (!targets.includes(path) && path !== store.clipboard.source) targets.push(path)
    if (!targets.length) return store.toast('Select the photos to paste onto in the Library', 'error')
    const r = await api.copySettings(store.clipboard.source, targets)
    store.toast(`Pasted settings onto ${r.applied.length} photo(s)`)
    if (r.applied.includes(path)) load()
    store.loadItems()
  }

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (isTyping()) return
      const mod = e.metaKey || e.ctrlKey
      if (e.key === '\\') { e.preventDefault(); if (!e.repeat) toggleBefore(true) }
      else if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo() }
      else if (mod && e.key.toLowerCase() === 'c') { e.preventDefault(); copy() }
      else if (mod && e.key.toLowerCase() === 'v') { e.preventDefault(); paste() }
      else if (mod && e.key.toLowerCase() === 'e') { e.preventDefault(); setExportOpen(true) }
      else if (mod) return
      else if (e.key === 'ArrowRight') { e.preventDefault(); cycleFilter(1) }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); cycleFilter(-1) }
      else if (e.key === ']') movePhoto(1)
      else if (e.key === '[') movePhoto(-1)
      else if (e.key.toLowerCase() === 'a') auto()
      else if (e.key.toLowerCase() === 'r') reset()
      else if (e.key.toLowerCase() === 'c') setCropMode((c) => { runPreview(settingsRef.current, 2048, !c); return !c })
      else if (e.key === 'Escape' && cropMode) { setCropMode(false); runPreview(settingsRef.current, 2048, false) }
    }
    const up = (e: KeyboardEvent) => { if (e.key === '\\') toggleBefore(false) }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [path, strip, settings.lut.name, photos, cropMode, store.clipboard, store.selected, beforeUrl])

  if (!info) return <div className="p-6 text-zinc-500">Loading…</div>
  const ex = info.exif
  const S = settings
  const slider = (key: keyof EditSettings, label: string, min: number, max: number, step = 1) => (
    <Slider label={label} value={S[key] as number} min={min} max={max} step={step} onChange={(v) => change({ [key]: v } as any)} onCommit={(v) => commit(label, { [key]: v } as any)} />
  )

  return (
    <div className="h-full flex">
      <div className="flex-1 flex flex-col min-w-0">
        <div className="h-10 flex items-center gap-1 px-3 border-b border-zinc-800 text-sm shrink-0">
          <button className="btn" onClick={() => movePhoto(-1)} title="[">◀</button>
          <button className="btn" onClick={() => movePhoto(1)} title="]">▶</button>
          <span className="ml-2 truncate text-zinc-300">{info.name}</span>
          <span className="text-zinc-500 text-xs ml-2">{info.width}×{info.height}{info.is_raw ? ' RAW' : ''}</span>
          <span className="ml-2"><Stars value={info.rating} onChange={async (r) => { await store.rate(path, r); setInfo({ ...info, rating: r }) }} /></span>
          <div className="flex-1" />
          <button className="btn" onClick={undo} disabled={history.index < 0} title="Cmd+Z">↶ Undo</button>
          <button className="btn" onClick={redo} disabled={history.index >= history.entries.length - 1} title="Shift+Cmd+Z">↷ Redo</button>
          <button className="btn" onClick={auto} title="A">✨ Auto</button>
          <button className={`btn ${cropMode ? '!bg-blue-700' : ''}`} onClick={() => { setCropMode(!cropMode); runPreview(settingsRef.current, 2048, !cropMode) }} title="C">⛶ Crop</button>
          <button className="btn" onClick={reset} title="R">Reset</button>
          <button className="btn" onClick={copy} title="Cmd+C">Copy</button>
          <button className="btn" onClick={paste} title="Cmd+V">Paste</button>
          <button className="btn-primary" onClick={() => setExportOpen(true)} title="Cmd+E">Export…</button>
        </div>
        <div className="flex-1 min-h-0 bg-black relative flex items-center justify-center p-2" onPointerDown={(e) => { if (e.button === 0 && !cropMode && (e.target as HTMLElement).tagName === 'IMG') toggleBefore(true) }} onPointerUp={() => showBefore && toggleBefore(false)}>
          {preview && <ImageWithCrop src={showBefore && beforeUrl ? beforeUrl : preview} cropMode={cropMode} settings={S} onCrop={(c) => change({ crop: c })} onCropCommit={(c) => commit('Crop', { crop: c })} />}
          {showBefore && <span className="absolute top-3 left-3 bg-black/70 px-2 py-0.5 rounded text-xs">BEFORE</span>}
          <span className="absolute bottom-2 right-3 text-[10px] text-zinc-600">{renderMs.toFixed(0)} ms</span>
          {cropMode && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-zinc-900/90 rounded px-3 py-2 flex items-center gap-2 text-xs">
              {ASPECTS.map((a) => <button key={a} className={`btn !py-0.5 ${S.crop.aspect === a ? '!bg-blue-700' : ''}`} onClick={() => {
                const ratio = aspectRatio(a, info.width, info.height)
                let c = { ...S.crop, aspect: a }
                if (ratio) { const r = centredCrop(info.width, info.height, ratio); c = { ...c, ...r } }
                else if (a === 'free') { /* keep */ } else c = { ...c, x: 0, y: 0, w: 1, h: 1 }
                commit('Crop ' + a, { crop: c })
              }}>{a}</button>)}
              <span className="text-zinc-400 ml-2">Rotate</span>
              <input type="range" min={-15} max={15} step={0.1} value={S.crop.rotate} className="w-32" onChange={(e) => change({ crop: { ...S.crop, rotate: Number(e.target.value) } })} onPointerUp={() => commit('Rotate', { crop: settingsRef.current.crop })} />
              <span className="w-10 text-right">{S.crop.rotate.toFixed(1)}°</span>
              <button className="btn !py-0.5" onClick={() => commit('Clear crop', { crop: { x: 0, y: 0, w: 1, h: 1, rotate: 0, aspect: 'free' } })}>Clear</button>
              <button className="btn-primary !py-0.5" onClick={() => { setCropMode(false); runPreview(settingsRef.current, 2048, false) }}>Done</button>
            </div>
          )}
        </div>
        <div className="h-[118px] border-t border-zinc-800 shrink-0 flex flex-col">
          <div className="flex items-center px-3 pt-1 text-xs text-zinc-400 gap-3">
            <span className="panel-title">Filters</span><span>← → to cycle</span>
            {S.lut.name && (<>
              <span className="ml-auto">Strength</span>
              <input type="range" min={0} max={1} step={0.05} value={S.lut.strength} className="w-32" onChange={(e) => change({ lut: { ...S.lut, strength: Number(e.target.value) } })} onPointerUp={() => commit('Filter strength', { lut: settingsRef.current.lut })} />
              <span className="w-8">{Math.round(S.lut.strength * 100)}%</span>
              <button className="btn !py-0 !px-1.5" onClick={async () => { const name = prompt('Preset name'); if (name) { await api.savePreset(name, path); store.loadPresets(); store.toast('Preset saved'); api.filterStrip(path).then((x) => setStrip(x.filters)) } }}>Save as preset</button>
            </>)}
          </div>
          <div ref={stripRef} className="flex gap-1.5 overflow-x-auto px-3 py-1 flex-1">
            {strip.map((f) => (
              <div key={f.id ?? 'none'} className={`shrink-0 w-[120px] cursor-pointer rounded overflow-hidden border-2 ${S.lut.name === f.id ? 'border-blue-500' : 'border-transparent hover:border-zinc-600'}`} onClick={() => applyFilter(f.id)} title={f.group}>
                <img src={`data:image/jpeg;base64,${f.thumb}`} className="w-[120px] h-[64px] object-cover" />
                <div className="text-[10px] text-center truncate px-1 text-zinc-300">{f.name}</div>
              </div>
            ))}
            {!strip.length && <div className="text-xs text-zinc-600 self-center">Rendering filter previews…</div>}
          </div>
        </div>
      </div>

      <aside className="w-[330px] border-l border-zinc-800 flex flex-col shrink-0">
        <div className="p-3 border-b border-zinc-800"><Histogram data={hist} /></div>
        <div className="flex border-b border-zinc-800 text-sm">
          {(['adjust', 'ai', 'history'] as Tab[]).map((t) => <button key={t} className={`flex-1 py-1.5 ${tab === t ? 'bg-zinc-800 text-white' : 'text-zinc-400'}`} onClick={() => setTab(t)}>{t === 'ai' ? 'AI' : t[0].toUpperCase() + t.slice(1)}</button>)}
        </div>
        <div className="flex-1 overflow-auto p-3 text-sm">
          {tab === 'adjust' && (
            <div className="space-y-3">
              <div className="text-[11px] text-zinc-500">
                {ex.model ?? ''} · ISO {ex.iso ?? '?'} · {ex.shutter ?? '?'}s · f/{ex.aperture ?? '?'} · {ex.focal_length ?? '?'}mm{ex.lens ? ` · ${ex.lens}` : ''}
              </div>
              <Section title="White balance">
                <Slider label="Temp" value={S.wb.temp} min={-100} max={100} onChange={(v) => change({ wb: { ...S.wb, temp: v } })} onCommit={(v) => commit('Temp', { wb: { ...settingsRef.current.wb, temp: v } })} />
                <Slider label="Tint" value={S.wb.tint} min={-100} max={100} onChange={(v) => change({ wb: { ...S.wb, tint: v } })} onCommit={(v) => commit('Tint', { wb: { ...settingsRef.current.wb, tint: v } })} />
              </Section>
              <Section title="Light">
                {slider('exposure', 'Exposure', -5, 5, 0.01)}
                {slider('contrast', 'Contrast', -100, 100)}
                {slider('highlights', 'Highlights', -100, 100)}
                {slider('shadows', 'Shadows', -100, 100)}
                {slider('whites', 'Whites', -100, 100)}
                {slider('blacks', 'Blacks', -100, 100)}
              </Section>
              <Section title="Tone curve">
                <CurveEditor points={S.curve} onChange={(pts) => change({ curve: pts })} onCommit={(pts) => commit('Curve', { curve: pts })} />
                <button className="btn mt-1 !text-xs" onClick={() => commit('Reset curve', { curve: [[0, 0], [1, 1]] })}>Reset curve</button>
              </Section>
              <Section title="Colour">
                {slider('vibrance', 'Vibrance', -100, 100)}
                {slider('saturation', 'Saturation', -100, 100)}
                <div className="flex gap-1 mt-1 mb-1">{(['hue', 'sat', 'lum'] as const).map((m) => <button key={m} className={`btn !py-0 !text-xs ${hslMode === m ? '!bg-zinc-600' : ''}`} onClick={() => setHslMode(m)}>{m === 'hue' ? 'Hue' : m === 'sat' ? 'Saturation' : 'Luminance'}</button>)}</div>
                {HSL_BANDS.map((b, i) => (
                  <Slider key={b} label={b} value={S.hsl[hslMode][i]} min={-100} max={100}
                    onChange={(v) => change({ hsl: { ...S.hsl, [hslMode]: S.hsl[hslMode].map((x, j) => (j === i ? v : x)) } })}
                    onCommit={(v) => commit(`HSL ${b} ${hslMode}`, { hsl: { ...settingsRef.current.hsl, [hslMode]: settingsRef.current.hsl[hslMode].map((x, j) => (j === i ? v : x)) } })} />
                ))}
              </Section>
              <Section title="Detail">
                {slider('clarity', 'Clarity', -100, 100)}
                {slider('clahe', 'Local (CLAHE)', 0, 100)}
                {slider('denoise', 'Denoise', 0, 100)}
                {slider('sharpen', 'Sharpen', 0, 100)}
              </Section>
              <Section title="Effects">
                {slider('vignette', 'Vignette', -100, 100)}
                <label className="flex items-center gap-2 text-xs text-zinc-400 mt-1"><input type="checkbox" checked={S.lens_correction} onChange={(e) => commit('Lens correction', { lens_correction: e.target.checked })} /> Lens correction (lensfun)</label>
              </Section>
            </div>
          )}
          {tab === 'ai' && <AIPanel path={path} onApplied={() => load()} />}
          {tab === 'history' && (
            <div>
              <div className={`px-2 py-1 rounded cursor-pointer text-xs ${history.index === -1 ? 'bg-zinc-800' : 'hover:bg-zinc-900'}`} onClick={async () => applyResult(await api.jump(path, -1))}>Original</div>
              {history.entries.map((h, i) => (
                <div key={i} className={`px-2 py-1 rounded cursor-pointer text-xs flex ${history.index === i ? 'bg-zinc-800' : i > history.index ? 'text-zinc-600 hover:bg-zinc-900' : 'hover:bg-zinc-900'}`} onClick={async () => applyResult(await api.jump(path, i))}>
                  <span className="flex-1 truncate">{h.label}</span>
                  {h.source === 'ai' && <span className="text-[10px] text-purple-300 ml-2">AI</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
      {exportOpen && <ExportModal paths={store.selected.length > 1 ? store.selected : [path]} onClose={() => setExportOpen(false)} />}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <div>
      <div className="panel-title cursor-pointer select-none flex items-center" onClick={() => setOpen(!open)}><span className="mr-1 text-zinc-600">{open ? '▾' : '▸'}</span>{title}</div>
      {open && <div className="mt-1">{children}</div>}
    </div>
  )
}

export function aspectRatio(a: string, w: number, h: number): number | null {
  if (a === 'original') return w / h
  const m: Record<string, number> = { '1:1': 1, '4:5': 4 / 5, '9:16': 9 / 16, '3:2': 3 / 2, '16:9': 16 / 9 }
  return m[a] ?? null
}
export function centredCrop(w: number, h: number, ratio: number) {
  const cur = w / h
  if (cur > ratio) { const cw = ratio / cur; return { x: (1 - cw) / 2, y: 0, w: cw, h: 1 } }
  const ch = cur / ratio; return { x: 0, y: (1 - ch) / 2, w: 1, h: ch }
}

/** The preview image; in crop mode it shows the uncropped image with a draggable crop rectangle. */
function ImageWithCrop({ src, cropMode, settings, onCrop, onCropCommit }: { src: string; cropMode: boolean; settings: EditSettings; onCrop: (c: EditSettings['crop']) => void; onCropCommit: (c: EditSettings['crop']) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const drag = useRef<{ kind: string; sx: number; sy: number; c: EditSettings['crop'] } | null>(null)
  const [box, setBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 })
  const c = settings.crop
  useEffect(() => {
    const el = imgRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el); setBox({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [src])
  const ratio = aspectRatio(c.aspect, box.w, box.h)
  const start = (kind: string) => (e: React.PointerEvent) => { e.stopPropagation(); drag.current = { kind, sx: e.clientX, sy: e.clientY, c: { ...c } }; (e.currentTarget as Element).setPointerCapture(e.pointerId) }
  const move = (e: React.PointerEvent) => {
    if (!drag.current || !box.w) return
    const dx = (e.clientX - drag.current.sx) / box.w, dy = (e.clientY - drag.current.sy) / box.h
    const o = drag.current.c
    let n = { ...o }
    const minW = 0.05
    if (drag.current.kind === 'move') { n.x = clamp(o.x + dx, 0, 1 - o.w); n.y = clamp(o.y + dy, 0, 1 - o.h) }
    else {
      const k = drag.current.kind
      let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h
      if (k.includes('l')) x0 = clamp(o.x + dx, 0, x1 - minW)
      if (k.includes('r')) x1 = clamp(o.x + o.w + dx, x0 + minW, 1)
      if (k.includes('t')) y0 = clamp(o.y + dy, 0, y1 - minW)
      if (k.includes('b')) y1 = clamp(o.y + o.h + dy, y0 + minW, 1)
      if (ratio) {
        // keep aspect in image pixels: w_px / h_px = ratio
        const pxRatio = ratio * (box.h / box.w) // fraction ratio
        let w = x1 - x0, h = y1 - y0
        if (k.includes('l') || k.includes('r')) h = w / pxRatio; else w = h * pxRatio
        if (k.includes('l')) x0 = x1 - w; else x1 = x0 + w
        if (k.includes('t')) y0 = y1 - h; else y1 = y0 + h
        if (x0 < 0 || x1 > 1 || y0 < 0 || y1 > 1) return
      }
      n = { ...n, x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
    }
    onCrop(n)
  }
  const end = () => { if (drag.current) { drag.current = null; onCropCommit(settings.crop) } }
  return (
    <div ref={ref} className="relative max-w-full max-h-full">
      <img ref={imgRef} src={src} className="max-w-full max-h-[calc(100vh-11rem)] object-contain select-none" draggable={false} style={{ display: 'block' }} />
      {cropMode && box.w > 0 && (
        <div className="absolute inset-0" onPointerMove={move} onPointerUp={end}>
          <div className="absolute inset-0 bg-black/60" style={{ clipPath: `polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${c.x * 100}% ${c.y * 100}%, ${c.x * 100}% ${(c.y + c.h) * 100}%, ${(c.x + c.w) * 100}% ${(c.y + c.h) * 100}%, ${(c.x + c.w) * 100}% ${c.y * 100}%, ${c.x * 100}% ${c.y * 100}%)` }} />
          <div className="absolute border border-white/80 cursor-move" style={{ left: `${c.x * 100}%`, top: `${c.y * 100}%`, width: `${c.w * 100}%`, height: `${c.h * 100}%` }} onPointerDown={start('move')}>
            <div className="absolute inset-0 grid grid-cols-3 grid-rows-3 pointer-events-none">{Array.from({ length: 9 }).map((_, i) => <div key={i} className="border border-white/20" />)}</div>
            {['tl', 'tr', 'bl', 'br', 't', 'b', 'l', 'r'].map((k) => (
              <div key={k} onPointerDown={start(k)} className={`absolute bg-white/90 ${k.length === 2 ? 'w-3 h-3' : k === 't' || k === 'b' ? 'w-6 h-2' : 'w-2 h-6'}`}
                style={{ left: k.includes('l') ? -5 : k.includes('r') ? 'calc(100% - 7px)' : 'calc(50% - 12px)', top: k.includes('t') ? -5 : k.includes('b') ? 'calc(100% - 7px)' : 'calc(50% - 12px)',
                  cursor: k === 'tl' || k === 'br' ? 'nwse-resize' : k === 'tr' || k === 'bl' ? 'nesw-resize' : k === 't' || k === 'b' ? 'ns-resize' : 'ew-resize' }} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v))

function ExportModal({ paths, onClose }: { paths: string[]; onClose: () => void }) {
  const store = useStore()
  const [format, setFormat] = useState('jpeg')
  const [quality, setQuality] = useState(92)
  const [longEdge, setLongEdge] = useState<string>('')
  return (
    <Modal title={`Export ${paths.length} photo${paths.length > 1 ? 's' : ''}`} onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="flex gap-2">{[['jpeg', 'JPEG'], ['png', 'PNG'], ['tiff16', '16-bit TIFF']].map(([v, l]) => <button key={v} className={`btn ${format === v ? '!bg-blue-700' : ''}`} onClick={() => setFormat(v)}>{l}</button>)}</div>
        {format === 'jpeg' && <label className="flex items-center gap-2">Quality <input type="range" min={60} max={100} value={quality} onChange={(e) => setQuality(Number(e.target.value))} /> {quality}</label>}
        <label className="flex items-center gap-2">Long edge (px, blank = full) <input className="bg-zinc-800 rounded px-2 py-1 w-24" value={longEdge} onChange={(e) => setLongEdge(e.target.value)} placeholder="full" /></label>
        <div className="text-xs text-zinc-500">Files go to the <code>exports/</code> folder next to the app (original name + _edited).</div>
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={async () => {
            const job = await api.exportPhotos(paths, format, quality, longEdge ? Number(longEdge) : null)
            onClose(); store.trackJob(job, (j) => store.toast(`Exported ${j.result.files.length} file(s) to ${j.result.out_dir}`))
          }}>Export</button></div>
      </div>
    </Modal>
  )
}

// ----------------------------------------------------------------------------- AI panel
function AIPanel({ path, onApplied }: { path: string; onApplied: () => void }) {
  const store = useStore()
  const [status, setStatus] = useState<{ available: boolean; model: string; key_set: boolean; picks_logged: number } | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [progress, setProgress] = useState('')
  const [result, setResult] = useState<any>(null)
  useEffect(() => { api.aiStatus().then(setStatus).catch(() => setStatus({ available: false, model: '', key_set: false, picks_logged: 0 })) }, [])
  const run = async (mode: string) => {
    setBusy(mode); setResult(null); setProgress('Starting…')
    try {
      const job = await api.aiRun(path, mode, note)
      const done = await store.trackJob(job)
      setResult(done.result)
      if (mode !== 'best_of_n' && mode !== 'critique') onApplied()
      if (mode !== 'best_of_n') onApplied()
    } catch (e: any) { setResult({ text: 'Failed: ' + e.message }) } finally { setBusy(null); setProgress('') }
  }
  const pick = async (chosen: any) => {
    const rejected = result.top.filter((t: any) => t !== chosen).concat(result.all.filter((a: any) => !result.top.some((t: any) => t.index === a.index)))
    await api.aiPick(path, chosen, rejected)
    store.toast(`Applied "${chosen.name}" and logged your pick (${(status?.picks_logged ?? 0) + 1} so far)`)
    setStatus((s) => s && { ...s, picks_logged: s.picks_logged + 1 })
    setResult(null); onApplied()
  }
  if (!status) return <div className="text-xs text-zinc-500">Checking for an API key…</div>
  if (!status.available) return (
    <div className="text-sm text-zinc-300 space-y-2">
      <div className="font-medium">Add your Claude API key to use the assistant</div>
      <ol className="list-decimal ml-4 text-zinc-400 space-y-1 text-xs">
        <li>Get a key at console.anthropic.com</li>
        <li>Copy <code>.env.example</code> to <code>.env</code> in the app folder</li>
        <li>Paste the key after <code>ANTHROPIC_API_KEY=</code></li>
        <li>Restart the app (./run.sh or run.bat)</li>
      </ol>
    </div>
  )
  return (
    <div className="space-y-3">
      <div className="text-[11px] text-zinc-500">{status?.model} · {status?.picks_logged ?? 0} taste picks logged</div>
      <textarea className="w-full bg-zinc-900 border border-zinc-800 rounded p-2 text-xs h-16" placeholder="Optional note for Claude (e.g. 'keep it natural', 'moody film look')" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="grid grid-cols-2 gap-1">
        {[['critique', 'Critique', 'Explain issues, no edits'], ['auto_edit', 'Auto-edit', 'Apply an edit, explain each change'], ['research', 'Research', 'Web-search the genre, save a preset'], ['best_of_n', 'Best-of-N', 'Compare 5 edits, pick your favourite']].map(([m, l, d]) => (
          <button key={m} className="btn text-left !py-1.5" disabled={!!busy} onClick={() => run(m)} title={d}><div className="font-medium">{busy === m ? '… working' : l}</div><div className="text-[10px] text-zinc-500">{d}</div></button>
        ))}
      </div>
      {busy && <div className="text-xs text-blue-300 animate-pulse">{progress || 'Claude is looking at the photo…'}</div>}
      {result && (
        <div className="space-y-2">
          {result.text && <div className="text-xs whitespace-pre-wrap text-zinc-200 bg-zinc-900 rounded p-2">{result.text}</div>}
          {result.actions?.length > 0 && (
            <div className="text-xs text-zinc-400 space-y-1">
              {result.actions.map((a: any, i: number) => <div key={i}>• {a.label}{a.delta ? ': ' + Object.entries(a.delta).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ') : ''}{a.template_ids ? ': ' + a.template_ids.join(', ') : ''}</div>)}
            </div>
          )}
          {result.sources?.length > 0 && <div className="text-[11px] text-zinc-500">Sources: {result.sources.map((s: string, i: number) => <a key={i} href={s} target="_blank" className="underline mr-1">{new URL(s).hostname}</a>)}</div>}
          {result.preset && <div className="text-xs text-green-300">Saved preset "{result.preset.name}" — it's in the filter strip.</div>}
          {result.top && (
            <div className="space-y-2">
              <div className="panel-title">Top 3 — pick one</div>
              {result.top.map((t: any) => (
                <div key={t.index} className="bg-zinc-900 rounded overflow-hidden">
                  <img src={`data:image/jpeg;base64,${t.image_b64}`} className="w-full" />
                  <div className="p-2 text-xs"><div className="font-medium">{t.name}</div><div className="text-zinc-400">{t.verdict || t.rationale}</div>
                    <button className="btn-primary mt-1 !text-xs" onClick={() => pick(t)}>Use this</button></div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
