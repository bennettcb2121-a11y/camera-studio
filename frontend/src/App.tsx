import { useEffect, useState } from 'react'
import { isTyping, useStore, type Mode } from './store'
import Library from './screens/Library'
import Editor from './screens/Editor'
import Mockups from './screens/Mockups'
import Video from './screens/Video'
import { JobsBar, Toasts } from './components/Jobs'
import Modal from './components/Modal'

const MODES: { id: Mode; label: string; key: string }[] = [
  { id: 'library', label: 'Library', key: 'L' },
  { id: 'edit', label: 'Edit', key: 'E' },
  { id: 'mockups', label: 'Mockups', key: 'M' },
  { id: 'video', label: 'Video', key: 'V' },
]

export default function App() {
  const { mode, setMode, loadFolders, loadPresets, checkAI, current } = useStore()
  const [help, setHelp] = useState(false)

  useEffect(() => { loadFolders(); loadPresets(); checkAI() }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === '?') { setHelp((h) => !h); return }
      const m = MODES.find((x) => x.key.toLowerCase() === e.key.toLowerCase())
      if (m && !e.shiftKey && !e.repeat) {
        if ((m.id === 'edit' || m.id === 'mockups') && !current) return
        setMode(m.id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current])

  return (
    <div className="h-full flex flex-col">
      <header className="h-10 flex items-center gap-1 px-3 border-b border-zinc-800 bg-zinc-950 shrink-0">
        <div className="font-semibold text-sm mr-4 text-zinc-100">Charlie's Camera Studio</div>
        {MODES.map((m) => (
          <button key={m.id} onClick={() => setMode(m.id)} disabled={(m.id === 'edit' || m.id === 'mockups') && !current}
            className={`px-3 py-1 rounded text-sm ${mode === m.id ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-100'} disabled:opacity-30`}>
            {m.label} <kbd className="ml-1">{m.key}</kbd>
          </button>
        ))}
        <div className="flex-1" />
        <JobsBar />
        <button className="btn ml-2" onClick={() => setHelp(true)} title="Keyboard shortcuts (?)">?</button>
      </header>
      <main className="flex-1 min-h-0">
        {mode === 'library' && <Library />}
        {mode === 'edit' && <Editor />}
        {mode === 'mockups' && <Mockups />}
        {mode === 'video' && <Video />}
      </main>
      <Toasts />
      {help && <Modal title="Keyboard shortcuts" onClose={() => setHelp(false)}><Shortcuts /></Modal>}
    </div>
  )
}

function Shortcuts() {
  const rows: [string, string][] = [
    ['L / E / M / V', 'Switch to Library / Edit / Mockups / Video'],
    ['?', 'This help'],
    ['Library: ← → ↑ ↓', 'Move selection (Shift/Cmd-click for multi-select)'],
    ['Library: Enter', 'Open in editor (videos open in Video)'],
    ['Library: 1–5 / 0', 'Star rating / clear'],
    ['Library: F', 'Cycle filter: all / photos / videos'],
    ['Edit: \\ (hold)', 'Before / after'],
    ['Edit: ← →', 'Previous / next filter'],
    ['Edit: [ ]', 'Previous / next photo'],
    ['Edit: Cmd/Ctrl+Z, Shift+Cmd+Z', 'Undo / redo'],
    ['Edit: A, R, C', 'Auto, Reset, toggle Crop tool'],
    ['Edit: Cmd/Ctrl+C / V', 'Copy settings / paste to selected photos'],
    ['Edit: Cmd/Ctrl+E', 'Export'],
    ['Edit: Esc', 'Leave crop tool'],
    ['Mockups: ← →', 'Previous / next mockup'],
    ['Mockups: ↑ ↓', 'Previous / next photo'],
    ['Mockups: F', 'Toggle favourite'],
    ['Mockups: Cmd/Ctrl+E', 'Export all mockups as ZIP'],
    ['Video: Space', 'Play / pause'],
    ['Video: S', 'Split clip at playhead'],
    ['Video: Delete', 'Remove selected clip'],
    ['Video: ← →', 'Move playhead 1 s (Shift: 0.1 s)'],
    ['Video: [ ]', 'Set clip in / out point at playhead'],
    ['Video: Cmd/Ctrl+E', 'Export'],
  ]
  return (
    <table className="text-sm w-full"><tbody>
      {rows.map(([k, v]) => <tr key={k} className="border-b border-zinc-800"><td className="py-1 pr-4 text-zinc-300 whitespace-nowrap"><kbd>{k}</kbd></td><td className="py-1 text-zinc-400">{v}</td></tr>)}
    </tbody></table>
  )
}
