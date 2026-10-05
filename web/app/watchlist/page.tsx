import Link from 'next/link'
import { Shell } from '@/components/Shell'
import { api, type ProjectTile } from '@/lib/api'

export const dynamic = 'force-dynamic'

function Progress({ tile }: { tile: ProjectTile }) {
  const entries = Object.entries(tile.progress)
  if (!entries.length) {
    return <p className="text-[11px] text-text-lo">nothing planned yet</p>
  }
  const total = entries.reduce((n, [, v]) => n + v.total, 0)
  const approved = entries.reduce((n, [, v]) => n + v.approved, 0)
  return (
    <div className="space-y-1.5">
      <div className="h-1 w-full overflow-hidden rounded-full bg-ink-500">
        <div
          className="h-full bg-accent"
          style={{ width: `${total ? (approved / total) * 100 : 0}%` }}
        />
      </div>
      <p className="text-[11px] text-text-lo">
        {approved} of {total} builds approved
      </p>
    </div>
  )
}

export default async function WatchlistPage() {
  let projects: ProjectTile[] = []
  let error: string | null = null
  try {
    projects = await api.projects()
  } catch {
    error = 'Could not reach the API. Is it running on port 8000?'
  }

  return (
    <Shell
      activeSection="watchlist"
      header={
        <>
          <div>
            <h1 className="text-[13px] font-medium text-text-hi">Watchlist</h1>
            <p className="text-[11px] text-text-lo">
              {projects.length} project{projects.length === 1 ? '' : 's'}
            </p>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-text-lo">
            <span className="rounded-md border border-line bg-ink-700 px-2 py-1">
              admin
            </span>
          </div>
        </>
      }
    >
      <div className="p-6">
        {error && (
          <div className="rounded-lg border border-line bg-ink-700 p-4 text-[13px] text-text-mid">
            {error}
          </div>
        )}

        {!error && projects.length === 0 && (
          <div className="rounded-lg border border-dashed border-line p-8 text-center">
            <p className="text-[13px] text-text-mid">No projects yet.</p>
            <p className="mt-1 text-[11px] text-text-lo">
              Run the pipeline on a script to create one.
            </p>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {projects.map((tile) => (
            <Link
              key={tile.id}
              href={`/projects/${tile.id}/plot`}
              className="group overflow-hidden rounded-xl border border-line bg-ink-700 transition-colors hover:border-ink-500"
            >
              {/* Cover. No key art exists for now, so the placeholder states
                  that rather than showing a broken image. */}
              <div className="flex aspect-[16/9] items-center justify-center border-b border-line-soft bg-ink-600">
                <span className="text-[11px] text-text-lo">no key art yet</span>
              </div>

              <div className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="truncate text-[14px] font-medium text-text-hi">
                      {tile.name}
                    </h2>
                    <p className="text-[11px] text-text-lo">
                      {tile.client_name ?? 'no client set'} · {tile.format.replace('_', '-')}
                    </p>
                  </div>
                  {tile.awaiting_decision > 0 && (
                    <span className="shrink-0 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent">
                      {tile.awaiting_decision}
                    </span>
                  )}
                </div>

                <div className="flex gap-4 text-[11px] text-text-mid">
                  <span>{tile.episodes} episodes</span>
                  <span>{tile.scenes} scenes</span>
                </div>

                <Progress tile={tile} />

                <div className="flex items-center justify-between border-t border-line-soft pt-2.5 text-[11px]">
                  <span className="text-text-lo">{tile.status}</span>
                  <span className="text-text-lo">{tile.code}</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </Shell>
  )
}
