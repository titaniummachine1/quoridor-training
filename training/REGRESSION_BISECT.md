# Regression bisect runbook

Evidence-based investigation when engine strength drops. **Do not blame NN training until search/session/binary differences are isolated.**

See also: [`AUDIT_REPORT.md`](AUDIT_REPORT.md) (infinite-search map), [`ARCHITECTURE_HANDOFF.md`](ARCHITECTURE_HANDOFF.md) § _Terminology_ (`pbff_*` = binary/bitboard flood fill for wall legality — not a separate search mode).

---

## 1. Purpose

Find the exact commit or commit range where playing strength or search behavior changed — especially around the infinite-search / `session_v15` experiment era.

---

## 2. Current knowns

| Item                                    | Status                                                                                                                            |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Known good commit                       | **UNKNOWN** — fill after first bisect pass                                                                                        |
| Known weak commit                       | **UNKNOWN** — fill from repro                                                                                                     |
| `session_v15` / infinite-search default | **Not active** — `engine/src/main.rs` routes v15 to standard warm `run_ace_session_stdio`; comment says infinite session disabled |
| `run_infinite_benchmark.py`             | Repeated **match batches**, not engine infinite-search mode                                                                       |

---

## 3. Required controlled variables

Hold constant across A/B:

- `RUSTFLAGS="-C target-cpu=native"` build
- Same `net_weights.bin` (or explicitly tag weight file in notes)
- Same time control (e.g. 5s / 10s)
- Same opening set / book flag
- Same benchmark script and engine flags
- Same machine when possible

---

## 4. Metrics (collect before/after each candidate)

| Metric              | Script / method                                                               |
| ------------------- | ----------------------------------------------------------------------------- |
| Eval parity         | `python training/parity_check.py` → 6/6                                       |
| Preflight           | `python training/validate_train_ready.py`                                     |
| NPS                 | `titanium bench` or pool timing logs                                          |
| Fixed-suite eval    | `parity_check` / `plateau_probe` positions                                    |
| Wall-heavy suite    | TBD — tag positions in probe set                                              |
| Eval drift          | `python training/plateau_probe.py`                                            |
| Self-play winrate   | `python training/run_benchmarks.py --only v15-vs-ti-pure-5s` or pool manifest |
| Root move agreement | `plateau_probe` move-change rate                                              |
| TT / root stability | Manual: same position, same depth, compare PV / bestmove across builds        |

---

## 5. Suggested commands (placeholders)

```powershell
# Build (native)
cd engine
$env:RUSTFLAGS = "-C target-cpu=native"
cargo build --release -p titanium
cd ..

# Preflight
python training/validate_train_ready.py
python training/parity_check.py
python training/regression_triage.py

# Fixed / drift probes
python training/plateau_probe.py

# Strength smoke (local)
python training/run_benchmarks.py --only v15-vs-ti-pure-5s

# Optional engine bisect (golden worktree)
# training/run_bisect_and_overnight.ps1  # long — only when bisect range is defined
```

Record for each run:

- git commit (parent + `engine/` submodule SHA)
- `titanium.exe` SHA256 (`python training/engine_identity.py`)
- metric outputs

---

## 6. Rule

**Do not blame NN** until:

1. Same binary + same weights + parity 6/6 on both sides
2. Search/session mode documented (warm session vs experimental)
3. Fixed-suite or self-play delta reproduced with controlled variables

If eval parity holds but search strength drops → suspect search policy, TT reuse, session routing, or time control — not `train.py`.

---

## 7. Fill-in log (template)

```text
Date:
Good commit:
Bad commit:
Weights file:
Time control:
Benchmark:
Result:
Notes:
```
