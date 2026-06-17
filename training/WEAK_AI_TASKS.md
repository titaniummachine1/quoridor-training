# Weak-AI safe task queue

Boring, bounded, verification-heavy work only. **Not** architecture, search logic, or training decisions.

```
Do not invent architecture. Do not optimize. Do not refactor.
Only verify, document, or add fail-fast guardrails.
If unsure, stop and report.
```

## Order

1. Doc consistency audit → `AUDIT_REPORT.md` (Task 1 section)
2. Build/native rules audit → `AUDIT_REPORT.md` (Task 7 section)
3. Infinite-search regression map → `AUDIT_REPORT.md` (Task 4 section)
4. Benchmark/probe script inventory → `AUDIT_REPORT.md` (Task 5 section)
5. `validate_train_ready.py` draft (additive preflight)
6. Checkpoint resume guard proposal → `AUDIT_REPORT.md` (Task 3 section)
7. Search-pressure Phase 3 gap report → `AUDIT_REPORT.md` (Task 6 section)

## Do not let weak AI touch

- `search.rs` behavior
- TT logic
- move ordering / pruning
- `net_weights.bin` blob layout
- training target formulas
- pressure head runtime integration
- Ka feature imports

## Master architecture

[`ARCHITECTURE_HANDOFF.md`](ARCHITECTURE_HANDOFF.md)
