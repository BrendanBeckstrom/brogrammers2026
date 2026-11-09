import { useEffect, useState } from 'react'
import { fetchVersion } from './version'

export const TAGLINE = "Don't watch 300 hours of KubeCon. Watch the 20 minutes that matter to your team."

export default function App() {
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchVersion().then((v) => {
      if (!cancelled) setVersion(v)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col px-4">
      <main className="py-12">
        <h1 className="text-3xl font-semibold">Debrief</h1>
        <p className="mt-2 text-gray-600">{TAGLINE}</p>
      </main>
      <footer className="mt-auto py-4 text-xs text-gray-500">
        {version ? `version ${version}` : null}
      </footer>
    </div>
  )
}
