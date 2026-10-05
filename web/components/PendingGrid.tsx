import Link from 'next/link'

/**
 * The honest empty state for the three image-grid tabs.
 *
 * Characters, Scenes and Shots are asset grids, and no asset has been generated
 * yet — that is a later stage, a later stage and a later stage. Rather than showing a blank panel, this states
 * what will fill it, what milestone fills it, and what the pipeline has already
 * worked out. The emptiness is the build order showing through, not a fault.
 */
export function PendingGrid({
  title,
  milestone,
  what,
  planned,
  plannedLabel,
  projectId,
}: {
  title: string
  milestone: string
  what: string
  planned: number
  plannedLabel: string
  projectId: string
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center p-10 text-center">
      {/* A skeleton of the justified-row grid this becomes, so the layout reads
          before there is anything to lay out. */}
      <div
        aria-hidden
        className="mb-8 flex w-full max-w-2xl flex-col gap-1 opacity-[0.18]"
      >
        {[
          [3, 2, 4, 2],
          [2, 5, 2],
          [4, 2, 3, 3],
        ].map((row, i) => (
          <div key={i} className="flex gap-1">
            {row.map((span, j) => (
              <div
                key={j}
                className="h-14 rounded bg-text-lo"
                style={{ flexGrow: span }}
              />
            ))}
          </div>
        ))}
      </div>

      <h2 className="text-[15px] font-medium text-text-hi">
        No {title.toLowerCase()} generated yet
      </h2>
      <p className="mt-2 max-w-md text-[13px] leading-relaxed text-text-mid">
        This tab fills with {what} at{' '}
        <span className="font-medium text-text-hi">{milestone}</span>. Nothing has
        been generated because asset generation has not been built yet.
      </p>

      <div className="mt-6 rounded-lg border border-line bg-ink-700 px-5 py-3">
        <p className="text-[20px] font-medium tabular-nums text-text-hi">{planned}</p>
        <p className="text-[11px] text-text-lo">{plannedLabel}</p>
      </div>

      <Link
        href={`/projects/${projectId}/story`}
        className="mt-6 text-[12px] text-text-mid underline underline-offset-4 hover:text-text-hi"
      >
        See what the script gave us →
      </Link>
    </div>
  )
}
