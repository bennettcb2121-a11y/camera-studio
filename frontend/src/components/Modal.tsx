import { useEffect, type ReactNode } from 'react'

export default function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', k, true)
    return () => window.removeEventListener('keydown', k, true)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center" onMouseDown={onClose}>
      <div className={`bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl ${wide ? 'w-[90vw] h-[88vh]' : 'w-[520px] max-h-[85vh]'} flex flex-col`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center px-4 py-2 border-b border-zinc-800">
          <div className="font-semibold text-sm">{title}</div>
          <div className="flex-1" />
          <button className="btn" onClick={onClose}>✕</button>
        </div>
        <div className="p-4 overflow-auto flex-1 min-h-0">{children}</div>
      </div>
    </div>
  )
}
