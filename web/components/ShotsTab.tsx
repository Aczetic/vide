'use client'

import { useState } from 'react'
import { OverlayPane } from './OverlayPane'
import type { AssetOut, SceneShotsOut, ShotOut } from '@/lib/api'

/**
 * Tab 5. Episode → scene → shot, with each shot's card and keyframe
 * candidates.
 *
 * The spatial map is shown once per scene, not per shot, because that is what
 * it is — written once and pasted unchanged into every shot of the scene.
 * Repeating it per shot would misrepresent the one rule that keeps a room from
 * rearranging itself between cuts.
 */

function Field({ label, value }: { label: string; value?: string | number | null }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="flex gap-2">
      <span className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-text-lo">
        {label}
      </span>
      <span className="text-[11px] leading-relaxed text-text-mid">{value}</span>
    </div>
  )
}

function ShotRow({
  shot,
  onOpenKeyframe,
}: {
  shot: ShotOut
  onOpenKeyframe: (shot: ShotOut, index: number) => void
}) {
  const [open, setOpen] = useState(false)
  const card = shot.card ?? {}

  return (
    <div className="border-b border-line-soft last:border-b-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-ink-700"
      >
        <span className="w-7 shrink-0 font-mono text-[12px] text-text-hi">
          {shot.letter}
        </span>
        {shot.is_master_wide && (
          <span className="shrink-0 rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 text-[9px] text-accent">
            master wide
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-text-mid">
          {card.goal ?? '—'}
        </span>
        <span className="shrink-0 text-[10px] text-text-lo">
          {card.shot_size} · {card.lens_fov_deg}° · {shot.duration_s}s
        </span>
        {shot.complexity && shot.complexity !== 'simple' && (
          <span
            className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] ${
              shot.complexity === 'complex'
                ? 'bg-red-500/15 text-red-300'
                : 'bg-ink-500 text-text-lo'
            }`}
          >
            {shot.complexity}
          </span>
        )}
        <span className="shrink-0 text-[10px] text-text-lo">
          {shot.keyframes.length > 0 ? `${shot.keyframes.length} keyframes` : 'no keyframe'}
        </span>
      </button>

      {open && (
        <div className="grid gap-4 border-t border-line-soft bg-ink-700/40 px-4 py-3 lg:grid-cols-2">
          <div className="space-y-1.5">
            <p className="text-[10px] uppercase tracking-wide text-text-lo">Material</p>
            <Field label="Action" value={card.action} />
            <Field label="Lines" value={card.lines} />
            <Field label="Cast" value={(card.characters ?? []).join(', ')} />
            <p className="pt-2 text-[10px] uppercase tracking-wide text-text-lo">Direction</p>
            <Field label="Tasks" value={card.tasks} />
            <Field label="Changes" value={card.dramaturgy} />
            <Field label="Blocking" value={card.blocking} />
            <Field label="Acting" value={card.acting} />
          </div>
          <div className="space-y-1.5">
            <p className="text-[10px] uppercase tracking-wide text-text-lo">Camera</p>
            <Field label="Size" value={card.shot_size} />
            <Field label="Lens" value={card.lens_fov_deg ? `${card.lens_fov_deg}°` : null} />
            <Field label="Angle" value={card.camera_angle} />
            <Field label="Height" value={card.camera_height} />
            <Field label="Move" value={card.camera_movement} />
            <p className="pt-2 text-[10px] uppercase tracking-wide text-text-lo">Edit</p>
            <Field label="Cut" value={card.cut_type} />
            <Field label="Hooks" value={card.hooks_into_next} />

            {shot.keyframes.length > 0 && (
              <div className="pt-3">
                <p className="mb-1.5 text-[10px] uppercase tracking-wide text-text-lo">
                  Keyframes
                </p>
                <div className="flex flex-wrap gap-1">
                  {shot.keyframes.map((k, i) => (
                    <button
                      key={k.generation_id}
                      onClick={() => onOpenKeyframe(shot, i)}
                      className="relative h-20 overflow-hidden rounded border border-line"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={k.url} alt="" className="h-full w-auto object-cover" />
                      {k.chosen && (
                        <span className="absolute right-1 top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-emerald-500 text-[8px] text-ink-900">
                          ✓
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export function ShotsTab({ scenes }: { scenes: SceneShotsOut[] }) {
  const [open, setOpen] = useState<{ asset: AssetOut; index: number } | null>(null)

  if (!scenes.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-10 text-center">
        <h2 className="text-[15px] font-medium text-text-hi">No shots planned yet</h2>
        <p className="mt-2 max-w-md text-[13px] leading-relaxed text-text-mid">
          Shot planning splits each scene into shots and writes a card for each.
          Keyframes are generated from the cards once the assets they reference
          are approved.
        </p>
        <code className="mt-4 rounded border border-line bg-ink-700 px-3 py-1.5 text-[11px] text-text-mid">
          python -m vide.shots plan
        </code>
      </div>
    )
  }

  const totalShots = scenes.reduce((n, s) => n + s.shots.length, 0)
  const withKeyframes = scenes.reduce(
    (n, s) => n + s.shots.filter((x) => x.keyframes.length > 0).length,
    0
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-4 border-b border-line-soft px-4 py-2.5 text-[11px]">
        <span className="text-text-hi">{scenes.length} scenes</span>
        <span className="text-text-mid">{totalShots} shots</span>
        <span className="text-text-lo">{withKeyframes} with keyframes</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {scenes.map((scene) => (
          <section key={scene.scene_id} className="border-b border-line">
            <div className="sticky top-0 z-10 bg-ink-800/95 px-4 py-2 backdrop-blur">
              <div className="flex items-baseline gap-2">
                <h3 className="text-[12px] font-medium text-text-hi">
                  EP{scene.episode} SC{scene.scene_label}
                </h3>
                <span className="truncate font-mono text-[10px] text-text-lo">
                  {scene.heading}
                </span>
                <span className="ml-auto text-[10px] text-text-lo">
                  {scene.shots.length} shots
                </span>
              </div>
            </div>

            {scene.spatial_map && (
              <details className="border-b border-line-soft bg-ink-700/30 px-4 py-2">
                <summary className="cursor-pointer text-[11px] text-text-mid marker:text-text-lo">
                  Spatial map — written once, inherited by every shot in this scene
                </summary>
                <p className="mt-2 whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-text-mid">
                  {scene.spatial_map}
                </p>
              </details>
            )}

            {scene.shots.map((shot) => (
              <ShotRow
                key={shot.id}
                shot={shot}
                onOpenKeyframe={(s, i) =>
                  setOpen({
                    asset: {
                      id: s.id,
                      entity_type: 'keyframe',
                      entity_name: `EP${scene.episode} SC${scene.scene_label}${s.letter}`,
                      variant_label: s.card?.goal ?? 'keyframe',
                      kind: 'view',
                      tag: `@key_${scene.episode}_${scene.scene_label}${s.letter}`,
                      status: s.status,
                      gaps: [],
                      scene_count: 1,
                      candidates: s.keyframes,
                      spec: {},
                      prompt: null,
                      prompt_sections: {},
                      model: null,
                    },
                    index: i,
                  })
                }
              />
            ))}
          </section>
        ))}
      </div>

      {open && (
        <OverlayPane
          asset={open.asset}
          index={open.index}
          onIndex={(i) => setOpen({ asset: open.asset, index: i })}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}
