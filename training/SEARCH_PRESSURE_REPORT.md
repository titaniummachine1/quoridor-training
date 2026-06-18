# Search-Pressure Experiment Report

Date: 2026-06-18

## Status

The pressure architecture is **not wired into Titanium search**. The native
alpha-beta label pipeline and frozen linear sidecar are working, but runtime
activation still requires a diagnostic export and pressure-off/pressure-on
search A/B.

## Data checked

- 1,000 compact native shallow/deep rows collected from completed games.
- 966 rows remained after excluding terminal and already-proven mate/race nodes.
- 666 distinct source games; train/validation splitting is by whole game.
- 203 paired zero-ink 50/400-visit rows collected; 200 were used for correlation.
- Expanded tensors are regenerated only during training and are not stored.

## Architecture ablation

`hidden32` sees only the existing wall/pawn accumulator and failed its first
native holdout: MSE 0.2234 versus constant baseline 0.1859, with 0% top-quartile
capture.

`rich` adds cheap distance/wall scalars and route summaries. Across five grouped
splits it beat baseline four times, but one split regressed by 0.9%.

`routefull` remains a frozen linear head but sees the existing full route vectors.
Across five grouped splits it beat baseline every time:

| Seed | MSE improvement | High-quartile capture |
|---:|---:|---:|
| 7 | +6.1% | 47.8% |
| 19 | +0.5% | 34.8% |
| 43 | +20.5% | 45.8% |
| 101 | +0.2% | 39.3% |
| 2026 | +5.5% | 34.8% |

The saved candidate is bound to the SHA-256 of its base HalfPW weights. It is
marked `native_validated`, not fully `validated`.

### Fixed-holdout follow-up

The native set was expanded to 4,999 rows and the split changed to a stable
game-key hash, so appending future labels cannot move existing games across the
train/validation boundary. On the same 122 held-out games:

| Feature tap | MSE | Constant baseline | Improvement | High-quartile capture |
|---|---:|---:|---:|---:|
| hidden32 | 0.16234 | 0.16870 | +3.77% | 31.4% |
| rich summaries | 0.15568 | 0.16870 | +7.72% | 40.5% |
| routefull linear | 0.13957 | 0.16870 | +17.27% | 43.8% |

This confirms that the original 32-unit tap discarded important route geometry.
`routefull` is the native diagnostic candidate; it still does not control search.

## Zero-ink decision

Zero-ink pressure was compared with Titanium pressure on the same positions.
Of 200 rows, 177 remained after native mate/race overrides:

- Pearson correlation: +0.0205
- Spearman correlation: -0.0289

This is no meaningful agreement. Zero visit allocation must not be mixed into
the alpha-beta extension/reduction scalar. It remains useful as a separate
future move-ordering or policy target. The trainer rejects zero rows by default.

## Next safe gate

1. Export routefull pressure inference as diagnostics only and benchmark its node cost.
2. Reject the design if pressure inference materially reduces search throughput.
3. Map pressure to at most one ply of reduction relief/extra reduction behind a flag.
4. Preserve mate, exact TT, forced-move, and tactical overrides.
5. Run identical-opening pressure-off versus pressure-on matches before enabling it.

No legal move may be pruned solely from pressure.
