import { useRef, useState } from 'react'

interface Props { points: number[][]; onChange: (pts: number[][]) => void; onCommit: (pts: number[][]) => void }

/** Tone curve: drag points, click to add, double-click a point to remove. */
export default function CurveEditor({ points, onChange, onCommit }: Props) {
  const ref = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const S = 160
  const toSvg = (p: number[]) => [p[0] * S, (1 - p[1]) * S]
  const fromEvent = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, 1 - (e.clientY - r.top) / r.height))]
  }
  const sorted = [...points].sort((a, b) => a[0] - b[0])
  const d = sorted.map((p, i) => `${i ? 'L' : 'M'}${toSvg(p).join(',')}`).join(' ')
  return (
    <svg ref={ref} viewBox={`0 0 ${S} ${S}`} className="w-full aspect-square bg-zinc-900 rounded touch-none select-none"
      onPointerDown={(e) => {
        const [x, y] = fromEvent(e)
        const hit = points.findIndex((p) => Math.hypot(p[0] - x, p[1] - y) < 0.06)
        if (hit >= 0) { setDrag(hit); (e.target as Element).setPointerCapture?.(e.pointerId); return }
        const pts = [...points, [x, y]]
        onChange(pts); setDrag(pts.length - 1)
      }}
      onPointerMove={(e) => {
        if (drag === null) return
        const [x, y] = fromEvent(e)
        const pts = points.map((p, i) => (i === drag ? (i === 0 ? [0, y] : i === 1 && points[1][0] === 1 && points.length === 2 ? [1, y] : [x, y]) : p))
        onChange(pts)
      }}
      onPointerUp={() => { if (drag !== null) { setDrag(null); onCommit(points) } }}
      onDoubleClick={(e) => {
        const r = ref.current!.getBoundingClientRect()
        const x = (e.clientX - r.left) / r.width, y = 1 - (e.clientY - r.top) / r.height
        const hit = points.findIndex((p) => Math.hypot(p[0] - x, p[1] - y) < 0.06)
        if (hit > 0 && points.length > 2 && !(points[hit][0] === 0 || points[hit][0] === 1)) { const pts = points.filter((_, i) => i !== hit); onChange(pts); onCommit(pts) }
      }}>
      {[0.25, 0.5, 0.75].map((g) => <g key={g}><line x1={g * S} y1={0} x2={g * S} y2={S} stroke="#27272a" /><line x1={0} y1={g * S} x2={S} y2={g * S} stroke="#27272a" /></g>)}
      <line x1={0} y1={S} x2={S} y2={0} stroke="#3f3f46" strokeDasharray="3 3" />
      <path d={d} fill="none" stroke="#e4e4e7" strokeWidth={1.5} />
      {points.map((p, i) => { const [cx, cy] = toSvg(p); return <circle key={i} cx={cx} cy={cy} r={4} fill={i === drag ? '#60a5fa' : '#e4e4e7'} /> })}
    </svg>
  )
}
