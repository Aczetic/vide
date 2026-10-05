'use client'

import { useState } from 'react'
import { OverlayPane } from './OverlayPane'
import type { AssetOut, CandidateOut } from '@/lib/api'

/**
 * The asset grid, built to the reference screenshot.
 *
 * **Justified rows, not column masonry.** The reference lays images out in rows
 * of uniform height with native aspect ratios preserved, filling the width edge
 * to edge — a different algorithm from Pinterest-style columns. Each tile's
 * flex-grow is its aspect ratio, so a row divides itself in proportion to how
 * wide each image actually is.
 *
 * Chrome is near-zero, as in the reference: no titles, no status text on the
 * tile. But this product has to surface five states the reference never needed,
 * so status is a one-pixel hairline on the tile edge — all the signal, none of
 * the footprint. The full badge row is behind the density toggle.
 */

function statusColour(status: string, verdict: string | null): string {
  if (verdict === 'fail') return 'bg-red-500/70'
  if (status === 'approved') return 'bg-emerald-500/70'
  if (status === 'awaiting_gate') return 'bg-accent/80'
  if (status === 'generating') return 'bg-blue-400/60'
  if (status === 'stale') return 'bg-orange-500/70'
  return 'bg-transparent'
}

function Tile({
  candidate,
  asset,
  dense,
  onOpen,
}: {
  candidate: CandidateOut
  asset: AssetOut
  dense: boolean
  onOpen: () => void
}) {
  const ratio = candidate.width && candidate.height ? candidate.width / candidate.height : 16 / 9
  return (
    <button
      onClick={onOpen}
      className="group relative min-w-0 overflow-hidden bg-ink-600"
      style={{ flexGrow: ratio, flexBasis: `${ratio * 180}px` }}
      aria-label={`${asset.entity_name} candidate`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={candidate.url}
        alt={`${asset.entity_name} — ${asset.variant_label ?? ''}`}
        className="h-full w-full object-cover transition-opacity group-hover:opacity-90"
        loading="lazy"
      />

      {/* Status as a hairline, not a badge — the reference tiles carry almost
          no chrome, and five badges per tile would destroy the density. */}
      <span
        className={`absolute inset-x-0 bottom-0 h-[2px] ${statusColour(
          asset.status,
          candidate.verdict
        )}`}
      />

      {/* Author mark, bottom-left, as in the reference. */}
      <span className="absolute bottom-1.5 left-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-ink-900/80 text-[8px] font-medium text-text-mid">
        A
      </span>

      {candidate.chosen && (
        <span className="absolute right-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-[9px] text-ink-900">
          ✓
        </span>
      )}

      {dense && (
        <span className="absolute left-1.5 top-1.5 rounded bg-ink-900/85 px-1.5 py-0.5 text-[9px] text-text-mid">
          {asset.status.replace('_', ' ')}
        </span>
      )}
    </button>
  )
}

export function AssetGrid({
  assets,
  emptyHint,
}: {
  assets: AssetOut[]
  emptyHint: React.ReactNode
}) {
  const [open, setOpen] = useState<{ asset: AssetOut; index: number } | null>(null)
  const [dense, setDense] = useState(false)
  const [filter, setFilter] = useState<string>('all')
  const [grouped, setGrouped] = useState(true)

  const visible = assets.filter((a) => {
    if (filter === 'all') return true
    if (filter === 'awaiting') return a.status === 'awaiting_gate' || a.status === 'agent_review'
    if (filter === 'approved') return a.status === 'approved'
    if (filter === 'rejected') return a.candidates.some((c) => c.verdict === 'fail')
    return true
  })

  if (!assets.length) return <>{emptyHint}</>

  const flat = visible.flatMap((asset) =>
    asset.candidates.map((candidate, index) => ({ asset, candidate, index }))
  )

  return (
    <div className="flex h-full flex-col">
      {/* Grid header — breadcrumb left, controls right, as in the reference. */}
      <div className="flex shrink-0 items-center justify-between border-b border-line-soft px-4 py-2.5">
        <div className="flex items-center gap-1.5 text-[12px]">
          <span className="text-text-lo">All</span>
          <span className="text-text-lo">/</span>
          <span className="text-text-hi">
            {visible.length} {visible.length === 1 ? 'element' : 'elements'}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {[
            ['all', 'All'],
            ['awaiting', 'Awaiting decision'],
            ['approved', 'Approved'],
            ['rejected', 'Agent-rejected'],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-md border px-2 py-1 text-[11px] transition-colors ${
                filter === key
                  ? 'border-line bg-ink-600 text-text-hi'
                  : 'border-transparent text-text-lo hover:text-text-mid'
              }`}
            >
              {label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-line" />
          <button
            onClick={() => setGrouped((g) => !g)}
            className="rounded-md border border-line px-2 py-1 text-[11px] text-text-mid hover:text-text-hi"
          >
            {grouped ? 'Grouped' : 'Flat'}
          </button>
          <button
            onClick={() => setDense((d) => !d)}
            className="rounded-md border border-line px-2 py-1 text-[11px] text-text-mid hover:text-text-hi"
          >
            View
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-1">
        {grouped ? (
          <div className="space-y-4">
            {visible.map((asset) => (
              <section key={asset.id}>
                <div className="flex items-baseline gap-2 px-2 py-1.5">
                  <h3 className="text-[12px] font-medium text-text-hi">
                    {asset.entity_name}
                  </h3>
                  <span className="text-[11px] text-text-lo">
                    {asset.variant_label} · {asset.candidates.length} option
                    {asset.candidates.length === 1 ? '' : 's'}
                  </span>
                  <span className="font-mono text-[10px] text-text-lo/70">{asset.tag}</span>
                  {asset.gaps.length > 0 && (
                    <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                      {asset.gaps.length} gap{asset.gaps.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-[3px]">
                  {asset.candidates.map((candidate, index) => (
                    <Tile
                      key={candidate.generation_id}
                      candidate={candidate}
                      asset={asset}
                      dense={dense}
                      onOpen={() => setOpen({ asset, index })}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="flex flex-wrap gap-[3px]">
            {flat.map(({ asset, candidate, index }) => (
              <Tile
                key={candidate.generation_id}
                candidate={candidate}
                asset={asset}
                dense={dense}
                onOpen={() => setOpen({ asset, index })}
              />
            ))}
          </div>
        )}
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
