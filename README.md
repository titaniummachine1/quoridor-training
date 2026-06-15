# Titanium Quoridor — local workspace

Four-repo layout for the Titanium Quoridor project. Push once with `setup_repos.ps1` (see [SETUP_REPOS.md](SETUP_REPOS.md)).

| Folder         | GitHub repo                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------- |
| `engine/`      | [titaniummachine1/titanium-quoridor](https://github.com/titaniummachine1/titanium-quoridor)                         |
| `site/`        | [titaniummachine1/Titanium-Quoridor-Website](https://github.com/titaniummachine1/Titanium-Quoridor-Website)         |
| `coordinator/` | [titaniummachine1/Titanium-Quoridor-Coordinator](https://github.com/titaniummachine1/Titanium-Quoridor-Coordinator) |
| `test-client/` | [titaniummachine1/titanium-quoridor-test-client](https://github.com/titaniummachine1/titanium-quoridor-test-client) |

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
