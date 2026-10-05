'use client'

import { useEffect, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { approveCandidate, overrideReview, type AssetOut } from '@/lib/api'

/**
 * The right overlay pane, built to the reference screenshot.
 *
 * It slides over the content — the grid stays underneath and keeps its scroll
 * position, so closing returns you exactly where you were. Closes on ✕, Esc, or
 * a click outside.
 *
 * Section order follows the reference: author header, then PROMPT with its
 * reference thumbnails and a See-all collapse, then DETAILS as a label/value
 * list. Everything below that is ours: the spec, the agent's verdict, version
 * history and the actions, in the same card idiom.
 */

function Card({
  title,
  children,
  accent,
}: {
  title: string
  children: React.ReactNode
  accent?: boolean
}) {
  return (
    <section className="px-4 py-3">
      <h3
        className={`mb-2 text-[10px] font-medium uppercase tracking-wider ${
          accent ? 'text-accent' : 'text-text-lo'
        }`}
      >
        {title}
      </h3>
      <div className="rounded-lg border border-line-soft bg-ink-600 p-3">{children}</div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-[11px] text-text-lo">{label}</span>
      <span className="text-right text-[11px] text-text-hi">{value}</span>
    </div>
  )
}

export function OverlayPane({
  asset,
  index,
  onIndex,
  onClose,
}: {
  asset: AssetOut
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const candidate = asset.candidates[index]
  const router = useRouter()
  const [pending, startTransition] = useTransition()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function approve() {
    setBusy(true)
    setError(null)
    try {
      await approveCandidate(asset.id, candidate.generation_id)
      startTransition(() => router.refresh())
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed')
    } finally {
      setBusy(false)
    }
  }

  async function override() {
    setBusy(true)
    try {
      await overrideReview(candidate.generation_id)
      startTransition(() => router.refresh())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed')
    } finally {
      setBusy(false)
    }
  }

  // — keyboard shortcuts, because clearing a gate queue with a mouse is
  // slow and this is the screen people live in.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowRight' && index < asset.candidates.length - 1)
        onIndex(index + 1)
      if (event.key === 'ArrowLeft' && index > 0) onIndex(index - 1)
      if (event.key.toLowerCase() === 'a') void approve()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, asset.candidates.length, onClose, onIndex])

  if (!candidate) return null

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-ink-900/60"
        onClick={onClose}
        aria-hidden
      />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-[440px] flex-col border-l border-line bg-ink-800 shadow-2xl">
        {/* Author header */}
        <div className="flex shrink-0 items-center justify-between border-b border-line-soft px-4 py-3">
          <div className="flex items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink-500 text-[11px] text-text-mid">
              A
            </span>
            <div>
              <p className="text-[12px] text-text-hi">sheet-generator</p>
              <p className="text-[10px] text-text-lo">Agent</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-full border border-line text-text-mid hover:text-text-hi"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {/* Preview with prev/next through the group */}
          <div className="relative bg-ink-900">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={candidate.url}
              alt={asset.entity_name}
              className="max-h-[300px] w-full object-contain"
            />
            {asset.candidates.length > 1 && (
              <>
                <button
                  onClick={() => onIndex(Math.max(0, index - 1))}
                  disabled={index === 0}
                  className="absolute left-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full bg-ink-900/80 text-text-mid disabled:opacity-25"
                >
                  ‹
                </button>
                <button
                  onClick={() => onIndex(Math.min(asset.candidates.length - 1, index + 1))}
                  disabled={index === asset.candidates.length - 1}
                  className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full bg-ink-900/80 text-text-mid disabled:opacity-25"
                >
                  ›
                </button>
                <span className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-ink-900/80 px-2 py-0.5 text-[10px] text-text-mid">
                  {index + 1} / {asset.candidates.length}
                </span>
              </>
            )}
          </div>

          <div className="border-b border-line-soft px-4 py-3">
            <h2 className="text-[14px] font-medium text-text-hi">{asset.entity_name}</h2>
            <p className="text-[11px] text-text-lo">
              {asset.variant_label} · {asset.kind}
            </p>
            <p className="mt-1 font-mono text-[10px] text-text-lo/80">{asset.tag}</p>
          </div>

          {asset.prompt && (
            <Card title="Prompt">
              <p className="max-h-32 overflow-hidden text-[11px] leading-relaxed text-text-mid">
                {asset.prompt}
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] text-text-lo hover:text-text-mid">
                  See all
                </summary>
                <p className="mt-2 whitespace-pre-wrap text-[11px] leading-relaxed text-text-mid">
                  {asset.prompt}
                </p>
                {Object.entries(asset.prompt_sections).length > 0 && (
                  <div className="mt-3 space-y-2 border-t border-line-soft pt-2">
                    {Object.entries(asset.prompt_sections).map(([key, value]) => (
                      <div key={key}>
                        <p className="text-[9px] uppercase tracking-wide text-text-lo">
                          {key}
                        </p>
                        <p className="text-[11px] leading-relaxed text-text-mid">
                          {String(value)}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </details>
            </Card>
          )}

          <Card title="Details">
            <Row label="Model" value={asset.model ?? '—'} />
            <Row
              label="Size"
              value={
                candidate.width ? `${candidate.width} × ${candidate.height}` : 'unknown'
              }
            />
            <Row label="Status" value={asset.status.replace('_', ' ')} />
            <Row label="Used in" value={`${asset.scene_count} scenes`} />
          </Card>

          {candidate.verdict ? (
            <Card title="Agent review" accent={candidate.verdict === 'fail'}>
              <Row label="Verdict" value={candidate.verdict} />
              {candidate.category && <Row label="Category" value={candidate.category} />}
              {candidate.reason && (
                <p className="mt-2 border-t border-line-soft pt-2 text-[11px] leading-relaxed text-text-mid">
                  {candidate.reason}
                </p>
              )}
            </Card>
          ) : (
            <Card title="Agent review">
              <p className="text-[11px] text-text-lo">
                Not reviewed yet — the review agent runs before this reaches a gate.
              </p>
            </Card>
          )}

          {Object.keys(asset.spec).length > 0 && (
            <Card title="Spec">
              <div className="space-y-2.5">
                {[
                  ['Age and build', asset.spec.age_build],
                  ['Face and body lock', asset.spec.face_body_lock],
                  ['Hair', asset.spec.hair_grooming],
                  ['Wardrobe', asset.spec.signature_wardrobe],
                  ['Voice', asset.spec.voice_block],
                  ['Acting profile', asset.spec.acting_profile],
                ].map(([label, value]) =>
                  value ? (
                    <div key={label as string}>
                      <p className="text-[9px] uppercase tracking-wide text-text-lo">
                        {label as string}
                      </p>
                      <p className="text-[11px] leading-relaxed text-text-mid">
                        {String(value)}
                      </p>
                    </div>
                  ) : null
                )}
                {Array.isArray(asset.spec.inferred) && asset.spec.inferred.length > 0 && (
                  <div className="border-t border-line-soft pt-2">
                    <p className="text-[9px] uppercase tracking-wide text-accent">
                      Invented, not from the script ({asset.spec.inferred.length})
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {asset.spec.inferred.map((item: string, i: number) => (
                        <li key={i} className="text-[11px] leading-relaxed text-text-mid">
                          · {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </Card>
          )}

          {asset.gaps.length > 0 && (
            <Card title="Gaps" accent>
              <ul className="space-y-1">
                {asset.gaps.map((gap, i) => (
                  <li key={i} className="text-[11px] leading-relaxed text-text-mid">
                    {gap}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        {/* Actions, sticky */}
        <div className="shrink-0 border-t border-line bg-ink-700 px-4 py-3">
          {error && (
            <p className="mb-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={approve}
              disabled={busy || pending || candidate.chosen}
              className="flex-1 rounded-md border border-accent/40 bg-accent-soft px-3 py-2 text-[12px] font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-40"
            >
              {candidate.chosen ? 'Selected' : busy ? 'Saving…' : 'Select this one'}
            </button>
            {candidate.verdict === 'fail' && (
              <button
                onClick={override}
                disabled={busy || pending}
                className="rounded-md border border-line bg-ink-600 px-3 py-2 text-[12px] text-text-mid hover:text-text-hi disabled:opacity-40"
              >
                Override
              </button>
            )}
          </div>
          <p className="mt-2 text-[10px] text-text-lo/80">
            Selecting writes a G2 gate decision. <kbd>A</kbd> selects, ← → moves
            between options, <kbd>Esc</kbd> closes.
          </p>
        </div>
      </aside>
    </>
  )
}
