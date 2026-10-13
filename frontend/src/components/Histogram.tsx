import type { Histogram as H } from '../types'

export default function Histogram({ data }: { data: H | null }) {
  const w = 256, h = 80
  if (!data) return <div className="h-20 bg-zinc-900 rounded" />
  const path = (arr: number[]) => {
    const n = arr.length
    const pts = arr.map((v, i) => `${(i / (n - 1)) * w},${h - Math.min(1, v) * (h - 2)}`)
    return `M0,${h} L${pts.join(' L')} L${w},${h} Z`
  }
  const s = data.stats
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20 bg-zinc-900 rounded" preserveAspectRatio="none" style={{ mixBlendMode: 'normal' }}>
        <path d={path(data.r)} fill="rgba(255,80,80,0.45)" />
        <path d={path(data.g)} fill="rgba(80,255,120,0.4)" />
        <path d={path(data.b)} fill="rgba(90,140,255,0.45)" />
        <path d={path(data.lum)} fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth={1} />
      </svg>
      <div className="flex justify-between text-[10px] text-zinc-500 mt-0.5">
        <span className={s.clipped_black > 0.01 ? 'text-blue-300' : ''}>blacks {(s.clipped_black * 100).toFixed(1)}%</span>
        <span>median {(s.median * 100).toFixed(0)}%</span>
        <span className={s.clipped_white > 0.01 ? 'text-red-300' : ''}>whites {(s.clipped_white * 100).toFixed(1)}%</span>
      </div>
    </div>
  )
}
