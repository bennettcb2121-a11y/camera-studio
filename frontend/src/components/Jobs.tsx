import { useState } from 'react'
import { useStore } from '../store'

export function JobsBar() {
  const jobs = useStore((s) => s.jobs)
  const [open, setOpen] = useState(false)
  const active = jobs.filter((j) => j.status === 'queued' || j.status === 'running')
  if (!jobs.length) return null
  return (
    <div className="relative">
      <button className="btn flex items-center gap-2" onClick={() => setOpen(!open)}>
        {active.length ? <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" /> : <span className="w-2 h-2 rounded-full bg-zinc-500" />}
        Jobs {active.length ? `(${active.length})` : ''}
      </button>
      {open && (
        <div className="absolute right-0 top-9 w-96 bg-zinc-900 border border-zinc-700 rounded shadow-xl p-2 z-40 max-h-80 overflow-auto">
          {jobs.map((j) => (
            <div key={j.id} className="py-1.5 border-b border-zinc-800 last:border-0 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-200">{j.kind}</span>
                <span className={`ml-auto ${j.status === 'error' ? 'text-red-400' : j.status === 'done' ? 'text-green-400' : 'text-blue-300'}`}>{j.status}</span>
              </div>
              {(j.status === 'running' || j.status === 'queued') && (
                <div className="h-1 bg-zinc-800 rounded mt-1"><div className="h-1 bg-blue-500 rounded" style={{ width: `${Math.round(j.progress * 100)}%` }} /></div>
              )}
              <div className="text-zinc-500 truncate">{j.message || (j.error ? j.error.split('\n')[0] : '')}</div>
              {j.status === 'done' && j.result?.files && <div className="text-zinc-400 truncate">→ {j.result.out_dir}</div>}
              {j.status === 'done' && j.result?.zip && <div className="text-zinc-400 truncate">→ {j.result.zip}</div>}
              {j.status === 'done' && j.result?.file && <div className="text-zinc-400 truncate">→ {j.result.file}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2">
      {toasts.map((t) => (
        <div key={t.id} className={`px-3 py-2 rounded shadow-lg text-sm max-w-md ${t.kind === 'error' ? 'bg-red-900 text-red-100' : 'bg-zinc-800 text-zinc-100'}`}>{t.text}</div>
      ))}
    </div>
  )
}
