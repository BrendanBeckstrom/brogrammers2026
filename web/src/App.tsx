export const TAGLINE = "Don't watch 300 hours of KubeCon. Watch the 20 minutes that matter to your team."

export default function App() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-semibold">Debrief</h1>
      <p className="mt-2 text-gray-600">{TAGLINE}</p>
    </main>
  )
}
