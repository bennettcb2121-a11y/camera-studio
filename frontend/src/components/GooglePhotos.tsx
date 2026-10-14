import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { waitJob } from '../api'
import { useStore } from '../store'
import Modal from './Modal'

export interface GStatus { configured: boolean; connected: boolean; account?: string | null; album?: string | null; import_dir: string }

export const useGoogle = create<{ status: GStatus | null; refresh: () => Promise<void> }>((set) => ({
  status: null,
  refresh: async () => { try { set({ status: await fetch('/api/google/status').then((r) => r.json()) }) } catch { /* offline */ } },
}))

async function post(url: string, body?: unknown) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || r.statusText)
  return d
}

/** Upload photos (with their edits) to the "Charlie's Camera Studio" album. */
export async function uploadToGoogle(paths: string[]) {
  const store = useStore.getState()
  try {
    const job = await post('/api/google/upload', { paths })
    store.toast(`Uploading ${paths.length} photo${paths.length > 1 ? 's' : ''} to Google Photos…`)
    const done = await store.trackJob(job)
    store.toast(`Uploaded ${done.result.uploaded} to the “${done.result.album}” album in Google Photos`)
  } catch (e: any) { store.toast(e.message, 'error') }
}

export function GooglePhotosPanel() {
  const { status, refresh } = useGoogle()
  const store = useStore()
  const [setup, setSetup] = useState(false)
  const [importing, setImporting] = useState<string | null>(null)

  useEffect(() => {
    refresh()
    const onMsg = (e: MessageEvent) => { if (e.data === 'google-connected') refresh() }
    const onFocus = () => refresh()
    window.addEventListener('message', onMsg); window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('focus', onFocus) }
  }, [])

  const startImport = async () => {
    try {
      const { job, picker_uri } = await post('/api/google/import')
      window.open(picker_uri, '_blank')
      setImporting('Pick photos in the Google Photos tab, then click Done there…')
      store.trackJob(job)
      const done = await waitJob(job.id, (j) => setImporting(j.message))
      if (done.result.imported) {
        store.toast(`Imported ${done.result.imported} from Google Photos`)
        await store.loadFolders(); await store.setFolder(done.result.folder)
      } else store.toast('Nothing was picked')
    } catch (e: any) { store.toast(e.message, 'error') } finally { setImporting(null) }
  }

  if (!status) return null
  return (
    <div className="space-y-1.5">
      <div className="panel-title flex items-center gap-1">Google Photos {status.connected && <span className="text-green-400 normal-case">●</span>}</div>
      {!status.configured && <button className="btn w-full" onClick={() => setSetup(true)}>Set up Google Photos…</button>}
      {status.configured && !status.connected && (<>
        <button className="btn-primary w-full" onClick={() => window.open('/api/google/connect', '_blank')}>Connect Google Photos</button>
        <button className="text-[11px] text-zinc-500 underline" onClick={() => setSetup(true)}>change OAuth client</button>
      </>)}
      {status.connected && (<>
        <button className="btn w-full" disabled={!!importing} onClick={startImport}>⬇ Import from Google Photos</button>
        {importing && <div className="text-[11px] text-blue-300">{importing}</div>}
        <div className="text-[11px] text-zinc-500 truncate" title={status.account ?? ''}>{status.account ?? 'connected'} · <button className="underline" onClick={async () => { await post('/api/google/disconnect'); refresh() }}>disconnect</button></div>
      </>)}
      {setup && <SetupModal onClose={() => { setSetup(false); refresh() }} />}
    </div>
  )
}

function SetupModal({ onClose }: { onClose: () => void }) {
  const [err, setErr] = useState<string | null>(null)
  const [ok, setOk] = useState(false)
  const upload = async (f: File) => {
    setErr(null)
    const fd = new FormData(); fd.append('file', f)
    const r = await fetch('/api/google/client', { method: 'POST', body: fd })
    if (r.ok) setOk(true); else setErr((await r.json().catch(() => ({}))).detail || 'Upload failed')
  }
  return (
    <Modal title="Set up Google Photos (one time, ~5 minutes)" onClose={onClose}>
      <div className="text-sm space-y-3">
        <p className="text-zinc-400 text-xs">Google requires each app to have its own sign-in key. You make it once in your Google account; nothing is shared with anyone else.</p>
        <ol className="list-decimal ml-5 space-y-2 text-xs text-zinc-300">
          <li>Open <a className="underline text-blue-300" href="https://console.cloud.google.com/projectcreate" target="_blank">console.cloud.google.com/projectcreate</a> and create a project called <b>Camera Studio</b>.</li>
          <li>Enable both APIs (make sure the new project is selected at the top):{' '}
            <a className="underline text-blue-300" href="https://console.cloud.google.com/apis/library/photospicker.googleapis.com" target="_blank">Photos Picker API</a> and{' '}
            <a className="underline text-blue-300" href="https://console.cloud.google.com/apis/library/photoslibrary.googleapis.com" target="_blank">Photos Library API</a> → <b>Enable</b>.</li>
          <li>Open <a className="underline text-blue-300" href="https://console.cloud.google.com/auth/overview" target="_blank">Google Auth Platform</a> → <b>Get started</b>: app name “Camera Studio”, your email, Audience <b>External</b>, finish. Then under <b>Audience → Test users</b> add your own Gmail address.</li>
          <li>Go to <a className="underline text-blue-300" href="https://console.cloud.google.com/auth/clients" target="_blank">Clients</a> → <b>Create client</b> → Application type <b>Desktop app</b> → Create → <b>Download JSON</b>.</li>
          <li>Choose that downloaded file here:</li>
        </ol>
        <input type="file" accept=".json,application/json" className="text-xs" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        {err && <div className="text-xs text-red-300">{err}</div>}
        {ok && (
          <div className="space-y-2">
            <div className="text-xs text-green-300">Saved. Last step: sign in and allow access.</div>
            <button className="btn-primary" onClick={() => { window.open('/api/google/connect', '_blank'); onClose() }}>Connect Google Photos</button>
            <div className="text-[11px] text-zinc-500">Google will warn “Google hasn't verified this app” — that's expected for your own private app: click <b>Continue</b>.</div>
          </div>
        )}
      </div>
    </Modal>
  )
}
