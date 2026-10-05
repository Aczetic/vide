import Link from 'next/link'
import { AssetGrid } from './AssetGrid'
import { PendingGrid } from './PendingGrid'
import type { AssetOut } from '@/lib/api'

/**
 * Tab 4. Locations and sets, with props behind a toggle.
 *
 * The two tiers are shown as they are built: a geography master teaches the
 * model the whole room and is never a first frame, and coverage plates in the
 * delivery ratio are what actually seed a shot. Grouping by element keeps a
 * master visually next to the plates that inherit from it.
 */
export function ScenesTab({
  projectId,
  view,
  locations,
  props,
  screens,
  vehicles,
  plannedSets,
  plannedProps,
}: {
  projectId: string
  view: 'locations' | 'props'
  locations: AssetOut[]
  props: AssetOut[]
  screens: AssetOut[]
  vehicles: AssetOut[]
  plannedSets: number
  plannedProps: number
}) {
  const propLike = [...props, ...vehicles, ...screens]
  const shown = view === 'locations' ? locations : propLike

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-line-soft px-4 py-2">
        {(
          [
            ['locations', 'Locations', locations.length],
            ['props', 'Props', propLike.length],
          ] as const
        ).map(([key, label, count]) => (
          <Link
            key={key}
            href={`/projects/${projectId}/scenes?view=${key}`}
            className={`rounded-md border px-2.5 py-1 text-[12px] transition-colors ${
              view === key
                ? 'border-line bg-ink-600 text-text-hi'
                : 'border-transparent text-text-lo hover:text-text-mid'
            }`}
          >
            {label}
            <span className="ml-1.5 text-[10px] text-text-lo">{count}</span>
          </Link>
        ))}
        {view === 'locations' && locations.length > 0 && (
          <p className="ml-3 text-[10px] text-text-lo">
            geography masters are 16:9 and never a first frame — coverage plates
            inherit the room from them
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1">
        <AssetGrid
          assets={shown}
          emptyHint={
            <PendingGrid
              title={view === 'locations' ? 'Location plates' : 'Props'}
              milestone="a later stage"
              what={
                view === 'locations'
                  ? 'geography masters and coverage plates'
                  : 'prop sheets, vehicle interiors and screen graphics'
              }
              planned={view === 'locations' ? plannedSets : plannedProps}
              plannedLabel={
                view === 'locations'
                  ? 'sets identified from the script'
                  : 'props identified from the script'
              }
              projectId={projectId}
            />
          }
        />
      </div>
    </div>
  )
}
