import Link from 'next/link'
import { Shell } from '@/components/Shell'
import { getQueue, type QueueItem } from '@/lib/api'

export const dynamic = 'force-dynamic'

/**
 * Review Queue — everything awaiting a decision, across all projects.
 *
 * This is the screen that makes "five people, five projects" work. Gates are
 * the only thing allowed to block, so a cross-project view of what is blocked
 * is the difference between a reviewer hunting for work and clearing it.
 */

const GATE_LABEL: Record<string, string> = {
  G0: 'Story approval',
  G1: 'Entity merges & story-days',
  G2: 'Asset selection',
  G3: 'Keyframe selection',
}

function Row({ item }: { item: QueueItem }) {
  const href =
    item.gate === 'G1'
      ? `/projects/${item.project_id}/story`
      : item.kind === 'keyframe'
        ? `/projects/${item.project_id}/shots`
        : item.kind === 'character'
          ? `/projects/${item.project_id}/characters`
          : `/projects/${item.project_id}/scenes`

  return (
    <Link
      href={href}
      className="flex items-center gap-3 border-b border-line-soft px-4 py-2.5 transition-colors last:border-b-0 hover:bg-ink-700"
    >
      {item.thumbnail ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={item.thumbnail}
          alt=""
          className="h-10 w-14 shrink-0 rounded border border-line object-cover"
        />
      ) : (
        <span className="flex h-10 w-14 shrink-0 items-center justify-center rounded border border-line bg-ink-600 text-[9px] text-text-lo">
          {item.gate}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-[12px] text-text-hi">{item.title}</p>
        <p className="truncate text-[10px] text-text-lo">
          {item.project_name} · {item.subtitle ?? item.kind.replace('_', ' ')}
        </p>
      </div>
      {item.candidates > 0 && (
        <span className="shrink-0 text-[10px] text-text-lo">
          {item.candidates} option{item.candidates === 1 ? '' : 's'}
        </span>
      )}
      <span className="shrink-0 rounded border border-line bg-ink-600 px-1.5 py-0.5 text-[10px] text-text-mid">
        {item.gate}
      </span>
    </Link>
  )
}

export default async function ReviewQueuePage() {
  let items: QueueItem[] = []
  let error: string | null = null
  try {
    items = await getQueue()
  } catch {
    error = 'Could not reach the API.'
  }

  const byGate = items.reduce<Record<string, QueueItem[]>>((acc, item) => {
    ;(acc[item.gate] ??= []).push(item)
    return acc
  }, {})

  return (
    <Shell
      activeSection="review"
      header={
        <>
          <div>
            <h1 className="text-[13px] font-medium text-text-hi">Review Queue</h1>
            <p className="text-[11px] text-text-lo">
              {items.length} item{items.length === 1 ? '' : 's'} awaiting a decision
            </p>
          </div>
          <span className="rounded-md border border-line bg-ink-700 px-2 py-1 text-[11px] text-text-lo">
            admin
          </span>
        </>
      }
    >
      <div className="p-5">
        {error && <p className="text-[13px] text-text-mid">{error}</p>}

        {!error && items.length === 0 && (
          <div className="rounded-lg border border-dashed border-line p-8 text-center">
            <p className="text-[13px] text-text-mid">Nothing is waiting on you.</p>
            <p className="mt-1 text-[11px] text-text-lo">
              Gates are the only thing that blocks progress — an empty queue means
              nothing is held up.
            </p>
          </div>
        )}

        <div className="space-y-5">
          {Object.entries(byGate).map(([gate, rows]) => (
            <section key={gate} className="overflow-hidden rounded-xl border border-line">
              <div className="flex items-baseline gap-2 border-b border-line bg-ink-700 px-4 py-2">
                <h2 className="text-[12px] font-medium text-text-hi">{gate}</h2>
                <span className="text-[11px] text-text-lo">
                  {GATE_LABEL[gate] ?? ''}
                </span>
                <span className="ml-auto text-[11px] text-text-lo">{rows.length}</span>
              </div>
              {rows.map((item) => (
                <Row key={`${item.gate}-${item.id}`} item={item} />
              ))}
            </section>
          ))}
        </div>
      </div>
    </Shell>
  )
}
