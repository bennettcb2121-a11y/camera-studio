import { useRef } from 'react'

interface Props {
  label: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void      // while dragging (live preview)
  onCommit?: (v: number) => void     // on release (history entry)
  format?: (v: number) => string
}

export default function Slider({ label, value, min, max, step = 1, onChange, onCommit, format }: Props) {
  const dragging = useRef(false)
  const last = useRef(value)
  const fmt = format ?? ((v: number) => (step < 1 ? v.toFixed(2) : String(Math.round(v))))
  const reset = () => { onChange(0); onCommit?.(0) }
  return (
    <div className="flex items-center gap-2 py-0.5 group">
      <div className="w-24 text-xs text-zinc-400 truncate cursor-pointer select-none" onDoubleClick={reset} title="Double-click to reset">{label}</div>
      <input type="range" min={min} max={max} step={step} value={value} className="flex-1"
        onChange={(e) => { const v = Number(e.target.value); last.current = v; onChange(v); if (!dragging.current) onCommit?.(v) }}
        onPointerDown={() => { dragging.current = true }}
        onPointerUp={() => { if (dragging.current) { dragging.current = false; onCommit?.(last.current) } }}
        onKeyUp={(e) => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) onCommit?.(last.current) }}
      />
      <input type="number" className="w-14 bg-zinc-900 border border-zinc-800 rounded px-1 text-xs text-right text-zinc-300" value={fmt(value)} step={step} min={min} max={max}
        onChange={(e) => { const v = Math.max(min, Math.min(max, Number(e.target.value))); if (!Number.isNaN(v)) { onChange(v); onCommit?.(v) } }} />
    </div>
  )
}
