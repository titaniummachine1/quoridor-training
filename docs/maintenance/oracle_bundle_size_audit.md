# Oracle code-only bundle size audit (2026-06-20)

## Summary

| Bundle | Files | Bytes | Manifest SHA256 |
| ------ | ----- | ----- | --------------- |
| Pre-audit (`dist/oracle_upload_code`) | 195 | 20,434,429 | `64217e94d4a812c28eee1544750b274ff970fc28efb14f714b60b7914f4efb7f` |
| Audited (`dist/oracle_upload_code_audited`) | 196 | 1,217,436 | `7788d1cd7ce5de14ba63d5a80f932807d0dd62fee9cac38efdf2751b120aa56e` |

The ~13× size increase was **not** from the production package. It was almost entirely one generated inventory file plus pytest cache accidentally included in an earlier bundle build.

## Root cause of growth (20.43 MB → 1.56 MB baseline)

| Path | Bytes | % of old bundle | Classification | Required on Oracle |
| ---- | ----- | ----------------- | -------------- | ------------------ |
| `docs/maintenance/repository_inventory.json` | 19,220,580 | 94.1% | Generated maintenance inventory (gitignored) | **no** — excluded |
| `training/.pytest_cache/v/cache/nodeids` | 20,954 | 0.1% | Pytest cache | **no** — excluded |
| Remaining legitimate code/docs | ~1,192,895 | ~5.8% | Production + docs | yes |

## Accidental artifact scan (pre-audit bundle)

| Pattern / path | Found | Verdict |
| -------------- | ----- | ------- |
| `*.pt` / checkpoints | none | OK |
| `*.db` / `*.duckdb` | none in code bundle | OK |
| `*.parquet` / dataset | none (`training/data/` excluded) | OK |
| `runs/` / `checkpoints/` | none | OK |
| `.pytest_cache/` | **yes** | **accidental — fixed** |
| `repository_inventory.json` | **yes** | **accidental — fixed** |
| `training/tests/fixtures/*.policy.bin.gz` | 73 B golden sidecar | **yes** — intentional test fixture |
| `*.exe` / `engine/target/` | none | OK |

## Packaging rule changes

Added to `FORBIDDEN_BUNDLE_PREFIXES` / `is_forbidden_bundle_path`:

- `docs/maintenance/repository_inventory.json`
- `docs/maintenance/gate_evidence_bundle_*`
- `training/.pytest_cache/`

Regression: `test_bundle_excludes_repository_inventory_json` in `training/tests/test_teacher_value.py`.

## Audited bundle — top directories

| Directory | Bytes | % |
| --------- | ----- | - |
| `training/` | 984,579 | 80.9% |
| `tools/` | 136,104 | 11.2% |
| `scripts/` | 55,714 | 4.6% |
| `docs/` | 38,078 | 3.1% |

No file ≥ 250 KB in the audited bundle.

## Operator note

Rebuild after packaging changes:

```powershell
python scripts/oracle/build_upload_bundle.py `
  --output dist/oracle_upload_code_audited `
  --code-only
python scripts/oracle/verify_upload_bundle.py dist/oracle_upload_code_audited
```
