# HalfPW NNUE retrain — pipeline

Augment the existing HalfPW net (gen13 ACE) with explicit geometry inputs so it
stops approximating shortest-path/tempo through hidden layers and spends capacity
on tactics. **Fine-tune the existing weights — do not retrain from scratch.**

## Why augment, not retrain
The current weights already encode tactical knowledge. A full retrain discards
it. We add new input planes (zero-initialised) and fine-tune, so learning is
correcting residuals, not relearning geometry.

## Frozen feature set (the training gate)
Soft net **inputs**, not hard rules — the certificate/race-proof layer is the only
hard override (handles every "...unless it forces a win"). Material is not counted;
the engine races on tempo.

| Feature | Status |
|---|---|
| tempo (`d_opp − d_me`) | already input (`ws[1]`) |
| per-player distance | already input (`ws[3]`, `ws[4]`) |
| wall-diff | already input (`ws[2]`, `ws[10]`) |
| tempo × opponent-wall-count (fragile lead) | NEW skip slot |
| per-player bottleneck / route-flex count (≥2-paths) | existing extractor → input |
| distance-from-player field (sparse, per cell) | NEW, layered flood |
| distance-from-path / delta field (sparse, per cell) | NEW, layered flood |

The two per-cell fields are kept **separate** (not the pre-combined CAT heat): the
net learns the interaction; pre-mixing forces it to re-separate. Walls are priced
by the BFS distance field exactly (incl. chaining), so we never hand-code a
per-wall tempo value. All features ride one flood per eval node.

## Pipeline
- `halfpw.py` — Python port of the engine forward pass (walls-present net path).
- `parity_check.py` — verifies `halfpw.forward` == `titanium eval … --json`
  bit-for-bit. **Must pass before any training.**
- (next) feature extraction: layered flood → dist-from-player + delta planes
  (sparse over corridor cells); wire into the engine `evaluate()` and the Python
  model together, measuring NPS.
- (next) data generation: self-play + mixed Ka/Ishtar games dumped as feature
  records (same JSON as `eval --json`) + search-derived target value.
- (next) train: fine-tune from `engine/baseline/net_weights.baseline.bin`,
  KataGo-style auxiliary heads (distance/wall-ownership) as training-only
  regularisers; curriculum: imitation (mixed teachers) → stop before collapse →
  self-play.
- measure: new-weights engine vs `engine/baseline/titanium_baseline.exe`. Adopt
  only on a clear win.

## Engine commands
- `titanium eval <moves>` — net eval for a position.
- `titanium eval <moves> --json` — raw inputs + eval (parity + training-data format).
- `titanium match --a <eng> --b <eng> --games N --time S` — self-play strength.

## Baseline (pre-retrain, 2026-06-15)
grafted vs plain ace-v13 @ 2s: 54–58 / 112 = 48.2% (±9.3%) ≈ −12 Elo (within
noise; even). This is the number the retrained net must beat.
