import { useEffect, useState } from 'react'
import { api } from '../api'
import Modal from './Modal'

export default function FolderBrowser({ onPick, onClose }: { onPick: (p: string) => void; onClose: () => void }) {
  const [state, setState] = useState<{ path: string; parent: string; dirs: string[]; media: Record<string, number> } | null>(null)
  const [typed, setTyped] = useState('')
  const go = async (p?: string) => { try { const s = await api.browse(p); setState(s); setTyped(s.path) } catch { /* ignore */ } }
  useEffect(() => { go() }, [])
  return (
    <Modal title="Choose a folder" onClose={onClose}>
      <div className="flex gap-2 mb-2">
        <input className="flex-1 bg-zinc-800 rounded px-2 py-1 text-sm" value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go(typed)} />
        <button className="btn" onClick={() => go(typed)}>Go</button>
      </div>
      {state && (
        <>
          <div className="text-xs text-zinc-500 mb-2">
            {Object.entries(state.media).map(([k, v]) => `${v} ${k}${v === 1 ? '' : 's'}`).join(', ') || 'no photos or videos directly here'}
          </div>
          <div className="max-h-72 overflow-auto border border-zinc-800 rounded">
            <div className="px-2 py-1 text-sm hover:bg-zinc-800 cursor-pointer text-zinc-400" onClick={() => go(state.parent)}>↑ ..</div>
            {state.dirs.map((d) => (
              <div key={d} className="px-2 py-1 text-sm hover:bg-zinc-800 cursor-pointer" onDoubleClick={() => go(d)} onClick={() => setTyped(d)}>📁 {d.split('/').pop()}</div>
            ))}
          </div>
          <div className="flex justify-end gap-2 mt-3">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn-primary" onClick={() => onPick(typed || state.path)}>Use this folder</button>
          </div>
        </>
      )}
    </Modal>
  )
}
