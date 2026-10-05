import { notFound } from 'next/navigation'
import { PROJECT_TABS, Shell } from '@/components/Shell'
import { PlotTab } from '@/components/PlotTab'
import { StoryTab } from '@/components/StoryTab'
import { PendingGrid } from '@/components/PendingGrid'
import { api, getAssets, getShots } from '@/lib/api'
import { AssetGrid } from '@/components/AssetGrid'
import { ScenesTab } from '@/components/ScenesTab'
import { ShotsTab } from '@/components/ShotsTab'

export const dynamic = 'force-dynamic'

const VALID = ['plot', 'story', 'characters', 'scenes', 'shots'] as const
type TabKey = (typeof VALID)[number]

export default async function ProjectTabPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; tab: string }>
  searchParams: Promise<{ scene?: string; view?: string }>
}) {
  const { id, tab } = await params
  const { scene, view } = await searchParams
  if (!VALID.includes(tab as TabKey)) notFound()

  const project = await api.project(id).catch(() => null)
  if (!project) notFound()

  const tabs = PROJECT_TABS(id)
  const awaiting = project.counts.questions ?? 0

  return (
    <Shell
      activeSection="project"
      projectId={id}
      activeTab={tab}
      tabs={tabs}
      header={
        <>
          <div className="flex items-baseline gap-3">
            <h1 className="text-[13px] font-medium text-text-hi">{project.name}</h1>
            <span className="text-[11px] text-text-lo">
              {project.client_name ?? 'no client'} · {project.code} · {project.status}
            </span>
          </div>
          <div className="flex items-center gap-4 text-[11px]">
            {awaiting > 0 && (
              <span className="rounded-md bg-accent-soft px-2 py-1 font-medium text-accent">
                {awaiting} awaiting decision
              </span>
            )}
            <span className="text-text-lo">
              {project.counts.builds ?? 0} builds planned
            </span>
            <span className="rounded-md border border-line bg-ink-700 px-2 py-1 text-text-lo">
              admin
            </span>
          </div>
        </>
      }
    >
      {tab === 'plot' && <PlotTab project={project} />}
      {tab === 'story' && <StoryTab projectId={id} selectedSceneId={scene} />}
      {tab === 'characters' && (
        <AssetGrid
          assets={await getAssets(id, 'character').catch(() => [])}
          emptyHint={
            <PendingGrid
              title="Characters"
              milestone="a later stage"
              what="character sheets and their state variants"
              planned={project.counts.characters ?? 0}
              plannedLabel="characters resolved from the script"
              projectId={id}
            />
          }
        />
      )}
      {tab === 'scenes' && (
        <ScenesTab
          projectId={id}
          view={view === 'props' ? 'props' : 'locations'}
          locations={await getAssets(id, 'location').catch(() => [])}
          props={await getAssets(id, 'prop').catch(() => [])}
          screens={await getAssets(id, 'screen_insert').catch(() => [])}
          vehicles={await getAssets(id, 'vehicle').catch(() => [])}
          plannedSets={project.counts.sub_areas ?? 0}
          plannedProps={project.counts.props ?? 0}
        />
      )}
      {tab === 'shots' && <ShotsTab scenes={await getShots(id).catch(() => [])} />}
    </Shell>
  )
}
