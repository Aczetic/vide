import Link from 'next/link'
import { api, type SceneDetail } from '@/lib/api'

/**
 * Tab 2: the episode → scene tree on the left, the selected scene's text
 * with its breakdown alongside, plus the G1 questions and the gaps list.
 *
 * This is the tab that carries the extraction pipeline's whole output, so it is the one that has to
 * make three days of reading legible in an hour.
 */

function Chip({
  children,
  tone = 'default',
}: {
  children: React.ReactNode
  tone?: 'default' | 'warn' | 'accent'
}) {
  const tones = {
    default: 'border-line bg-ink-600 text-text-mid',
    warn: 'border-accent/30 bg-accent-soft text-accent',
    accent: 'border-line bg-ink-500 text-text-hi',
  }
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

function SceneBody({ scene }: { scene: SceneDetail }) {
  const b = scene.breakdown ?? {}
  const list = (key: string): string[] => (Array.isArray(b[key]) ? b[key] : [])

  const sections: [string, React.ReactNode][] = [
    [
      'In the scene',
      scene.characters.length ? (
        <div className="flex flex-wrap gap-1">
          {scene.characters.map((c) => (
            <Chip key={c.id} tone={c.speaks ? 'accent' : 'default'}>
              {c.name}
              {c.tier && <span className="ml-1 opacity-60">{c.tier}</span>}
            </Chip>
          ))}
        </div>
      ) : null,
    ],
    [
      'Sets implied by the action',
      (b.implied_locations ?? []).length ? (
        <ul className="space-y-1.5">
          {(b.implied_locations ?? []).map((loc: any, i: number) => (
            <li key={i} className="rounded border border-line-soft bg-ink-600 px-2.5 py-1.5">
              <p className="text-[12px] text-text-hi">{loc.name_raw}</p>
              <p className="mt-0.5 text-[11px] italic text-text-lo">“{loc.evidence}”</p>
            </li>
          ))}
        </ul>
      ) : null,
    ],
    [
      'Props',
      list('props').length ? (
        <div className="flex flex-wrap gap-1">
          {list('props').map((p, i) => (
            <Chip key={i}>{p}</Chip>
          ))}
        </div>
      ) : null,
    ],
    [
      'Screens & inserts',
      list('screens_inserts').length ? (
        <div className="flex flex-wrap gap-1">
          {list('screens_inserts').map((s, i) => (
            <Chip key={i}>{s}</Chip>
          ))}
        </div>
      ) : null,
    ],
    [
      'Wardrobe',
      list('wardrobe_cues').length ? (
        <div className="flex flex-wrap gap-1">
          {list('wardrobe_cues').map((w, i) => (
            <Chip key={i}>{w}</Chip>
          ))}
        </div>
      ) : null,
    ],
    [
      'State (continuity-critical)',
      list('state_cues').length ? (
        <div className="flex flex-wrap gap-1">
          {list('state_cues').map((s, i) => (
            <Chip key={i} tone="warn">
              {s}
            </Chip>
          ))}
        </div>
      ) : null,
    ],
  ]

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {/* Left: the script itself */}
      <div className="space-y-4">
        <div>
          <p className="font-mono text-[11px] text-text-lo">{scene.heading}</p>
          {scene.source_span && (
            <p className="mt-1 text-[10px] text-text-lo/70">
              lines {String((scene.source_span as any).start_line)}–
              {String((scene.source_span as any).end_line)} of the source
            </p>
          )}
        </div>

        {scene.action_text && (
          <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-text-mid">
            {scene.action_text}
          </p>
        )}

        {scene.dialogue.length > 0 && (
          <div className="space-y-2.5 border-t border-line-soft pt-4">
            {scene.dialogue.map((line) => (
              <div key={line.order}>
                <div className="flex flex-wrap items-baseline gap-1.5">
                  <span className="text-[11px] font-medium tracking-wide text-text-hi">
                    {line.speaker}
                  </span>
                  {line.delivery_action && (
                    <span className="text-[10px] text-text-lo">{line.delivery_action}</span>
                  )}
                  {line.delivery_emotion && (
                    <span className="text-[10px] italic text-accent/80">
                      {line.delivery_emotion}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-[12px] leading-relaxed text-text-mid">{line.line}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Right: what the pipeline extracted */}
      <div className="space-y-4 lg:border-l lg:border-line-soft lg:pl-5">
        {scene.summary && (
          <div>
            <h4 className="mb-1 text-[10px] uppercase tracking-wide text-text-lo">
              What happens
            </h4>
            <p className="text-[12px] leading-relaxed text-text-mid">{scene.summary}</p>
          </div>
        )}
        {scene.emotional_beat && (
          <div>
            <h4 className="mb-1 text-[10px] uppercase tracking-wide text-text-lo">Beat</h4>
            <p className="text-[12px] leading-relaxed text-text-mid">{scene.emotional_beat}</p>
          </div>
        )}

        {sections.map(([label, node]) =>
          node ? (
            <div key={label}>
              <h4 className="mb-1.5 text-[10px] uppercase tracking-wide text-text-lo">
                {label}
              </h4>
              {node}
            </div>
          ) : null
        )}

        {scene.gaps.length > 0 && (
          <div>
            <h4 className="mb-1.5 text-[10px] uppercase tracking-wide text-accent">
              The script does not say ({scene.gaps.length})
            </h4>
            <ul className="space-y-1">
              {scene.gaps.map((gap, i) => (
                <li
                  key={i}
                  className="rounded border border-accent/20 bg-accent-soft/40 px-2.5 py-1.5 text-[11px] leading-relaxed text-text-mid"
                >
                  {gap}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

export async function StoryTab({
  projectId,
  selectedSceneId,
}: {
  projectId: string
  selectedSceneId?: string
}) {
  const [episodes, questions, gaps] = await Promise.all([
    api.episodes(projectId),
    api.questions(projectId),
    api.gaps(projectId),
  ])

  const firstScene = episodes.find((e) => e.scenes.length)?.scenes[0]
  const sceneId = selectedSceneId ?? firstScene?.id
  const scene = sceneId ? await api.scene(sceneId).catch(() => null) : null

  return (
    <div className="flex h-full min-h-0">
      {/* Episode → scene tree */}
      <aside className="w-60 shrink-0 overflow-auto border-r border-line-soft">
        {episodes.map((episode) => (
          <div key={episode.id} className="border-b border-line-soft last:border-b-0">
            <div className="sticky top-0 bg-ink-800/95 px-3 py-1.5 backdrop-blur">
              <p className="text-[11px] font-medium text-text-lo">
                {episode.label ?? `EP${episode.number}`}
              </p>
            </div>
            {episode.scenes.map((s) => {
              const active = s.id === sceneId
              return (
                <Link
                  key={s.id}
                  href={`/projects/${projectId}/story?scene=${s.id}`}
                  className={`block px-3 py-1.5 text-[11px] transition-colors ${
                    active ? 'bg-ink-600 text-text-hi' : 'text-text-mid hover:bg-ink-700'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      SC{s.number_label} · {s.location_raw ?? '—'}
                    </span>
                    {s.gap_count > 0 && (
                      <span className="shrink-0 text-[9px] text-accent">{s.gap_count}</span>
                    )}
                  </div>
                  <div className="mt-0.5 flex gap-1.5 text-[9px] text-text-lo">
                    <span>{s.time_of_day}</span>
                    {s.story_day && <span>· {s.story_day}</span>}
                    {s.intercut_group && <span>· intercut</span>}
                  </div>
                </Link>
              )
            })}
          </div>
        ))}
      </aside>

      {/* Scene + breakdown */}
      <div className="min-w-0 flex-1 overflow-auto">
        {/* G1 — the decisions that change the build count */}
        {questions.filter((q) => !q.resolved).length > 0 && (
          <details className="border-b border-line-soft bg-ink-700/50">
            <summary className="cursor-pointer px-5 py-3 text-[12px] text-text-hi marker:text-text-lo">
              <span className="font-medium text-accent">
                {questions.filter((q) => !q.resolved).length} decisions
              </span>{' '}
              <span className="text-text-mid">
                need a human — each one changes the build count (G1)
              </span>
            </summary>
            <ul className="space-y-2 px-5 pb-4">
              {questions
                .filter((q) => !q.resolved)
                .map((q) => (
                  <li
                    key={q.id}
                    className="rounded-lg border border-line bg-ink-600 px-3.5 py-3"
                  >
                    <div className="mb-1.5 flex items-start gap-2">
                      <Chip>{q.entity_type.replace('_', ' ')}</Chip>
                    </div>
                    <p className="text-[12px] leading-relaxed text-text-hi">{q.question}</p>
                    {q.recommendation && (
                      <p className="mt-1.5 text-[11px] leading-relaxed text-text-lo">
                        <span className="text-text-mid">Suggested:</span>{' '}
                        {q.recommendation}
                      </p>
                    )}
                    <div className="mt-2.5 flex gap-2">
                      <button
                        disabled
                        className="cursor-not-allowed rounded-md border border-line bg-ink-500 px-2.5 py-1 text-[11px] text-text-lo"
                      >
                        Merge
                      </button>
                      <button
                        disabled
                        className="cursor-not-allowed rounded-md border border-line bg-ink-500 px-2.5 py-1 text-[11px] text-text-lo"
                      >
                        Keep separate
                      </button>
                      <span className="self-center text-[10px] text-text-lo/70">
                        answering lands with the gate UI
                      </span>
                    </div>
                  </li>
                ))}
            </ul>
          </details>
        )}

        {gaps.length > 0 && (
          <details className="border-b border-line-soft">
            <summary className="cursor-pointer px-5 py-3 text-[12px] text-text-mid marker:text-text-lo">
              <span className="font-medium text-text-hi">{gaps.length} gaps</span> — things
              the script never specifies
            </summary>
            <ul className="max-h-72 space-y-1 overflow-auto px-5 pb-4">
              {gaps.map((gap, i) => (
                <li key={i} className="flex gap-2 text-[11px] leading-relaxed">
                  <Link
                    href={`/projects/${projectId}/story?scene=${gap.scene_id}`}
                    className="shrink-0 font-mono text-text-lo hover:text-text-hi"
                  >
                    {gap.scene_label}
                  </Link>
                  <span className="text-text-mid">{gap.text}</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        <div className="p-5">
          {scene ? (
            <SceneBody scene={scene} />
          ) : (
            <p className="text-[12px] text-text-lo">Select a scene.</p>
          )}
        </div>
      </div>
    </div>
  )
}
