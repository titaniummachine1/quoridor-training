# Titanium Quoridor — local workspace

Five-repo layout. **Engine** and **site** are separate git repos inside this folder; the **root** git is the **training pipeline** backup (`quoridor-training` on GitHub).

| Folder / root | GitHub repo | Role |
| ------------- | ----------- | ---- |
| `engine/` | [titaniummachine1/titanium-quoridor](https://github.com/titaniummachine1/titanium-quoridor) | Rust engine (UCI, WASM, ACE v13, v15 search) |
| `site/` | [titaniummachine1/Titanium-Quoridor-Website](https://github.com/titaniummachine1/Titanium-Quoridor-Website) | Playable UI, benchmarks, JS ace-v13 anchor |
| `coordinator/` | [titaniummachine1/Titanium-Quoridor-Coordinator](https://github.com/titaniummachine1/Titanium-Quoridor-Coordinator) | Cloudflare Worker SPRT coordinator |
| `test-client/` | [titaniummachine1/titanium-quoridor-test-client](https://github.com/titaniummachine1/titanium-quoridor-test-client) | Distributed match worker |
| **this root** | [titaniummachine1/quoridor-training](https://github.com/titaniummachine1/quoridor-training) | HalfPW NNUE training, overnight pool, supervision |

Push once with `setup_repos.ps1` (four sub-repos) and `push_training.ps1` (root training repo — create GitHub repo first; see [SETUP_REPOS.md](SETUP_REPOS.md)).

## Quick start

**Engine** (`engine/`):

```bash
cd engine && cargo test --release && cargo build --release
cargo run --release --bin titanium -- perft 3   # 2_062_264 nodes
```

Movegen architecture: `engine/docs/MOVEGEN.md`

**Website** (`site/web/`):

```bash
cd site/web && npm install && npm run dev
```

**Coordinator** (`coordinator/`): see `coordinator/README.md`

**Test client** (`test-client/`): see `test-client/README.md`
