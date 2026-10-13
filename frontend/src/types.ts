export interface WB { temp: number; tint: number }
export interface HSL { hue: number[]; sat: number[]; lum: number[] }
export interface LutSetting { name: string | null; strength: number }
export interface Crop { x: number; y: number; w: number; h: number; rotate: number; aspect: string }

export interface EditSettings {
  wb: WB
  exposure: number
  contrast: number
  highlights: number
  shadows: number
  whites: number
  blacks: number
  curve: number[][]
  hsl: HSL
  vibrance: number
  saturation: number
  lut: LutSetting
  clarity: number
  clahe: number
  denoise: number
  sharpen: number
  crop: Crop
  vignette: number
  lens_correction: boolean
}

export const HSL_BANDS = ['red', 'orange', 'yellow', 'green', 'aqua', 'blue', 'purple', 'magenta'] as const
export const ASPECTS = ['free', 'original', '1:1', '4:5', '9:16', '3:2', '16:9'] as const

export function defaultSettings(): EditSettings {
  return {
    wb: { temp: 0, tint: 0 },
    exposure: 0, contrast: 0, highlights: 0, shadows: 0, whites: 0, blacks: 0,
    curve: [[0, 0], [1, 1]],
    hsl: { hue: Array(8).fill(0), sat: Array(8).fill(0), lum: Array(8).fill(0) },
    vibrance: 0, saturation: 0,
    lut: { name: null, strength: 1 },
    clarity: 0, clahe: 0, denoise: 0, sharpen: 0,
    crop: { x: 0, y: 0, w: 1, h: 1, rotate: 0, aspect: 'free' },
    vignette: 0,
    lens_correction: false,
  }
}

export interface LibraryItem {
  path: string; folder: string; name: string; kind: 'photo' | 'video'; ext: string
  size: number; mtime: number; rating: number; has_edits: number
}

export interface HistoryEntry { label: string; ts: number; source: string }
export interface History { index: number; entries: HistoryEntry[] }

export interface PhotoInfo {
  path: string; name: string; is_raw: boolean; width: number; height: number
  exif: Record<string, any>; settings: EditSettings; history: History; rating: number; mockup_favorites: string[]
}

export interface Histogram { r: number[]; g: number[]; b: number[]; lum: number[]; stats: Record<string, number> }

export interface Preset { id: string; name: string; group: string; description?: string; settings: Partial<EditSettings>; user: boolean; sources?: string[] }

export interface MockupTemplate { id: string; name: string; slots: { corners: number[][] }[]; blend: Record<string, any>; overlay?: string }

export interface Job {
  id: string; kind: string; status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number; message: string; result: any; error: string | null; created: number; finished: number | null
  live?: { path: string; settings: EditSettings; label: string; seq: number } | null
}

export interface VideoClip {
  id: string; type: 'video' | 'photo' | 'title'; src?: string; in?: number; out?: number; speed?: number
  src_duration?: number; has_audio?: boolean; duration?: number; zoom?: boolean
  look?: { preset_id?: string; strength?: number } | null
  title?: { text: string; position?: string; size?: number; color?: string; duration?: number | null } | null
  text?: string; size?: number; color?: string; bg?: string
}
export interface VideoProject {
  id: string; name: string; fps: number; clips: VideoClip[]; duration?: number
  music: { src: string; volume: number; fade_in: number; fade_out: number; keep_clip_audio?: boolean } | null
}
