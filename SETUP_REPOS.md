# Repo distribution

This folder is a **local staging area** for four GitHub repos. After pushing, work in the individual repos; keep this layout only if you still use it as a workspace.

```
Quoridor best AI/
├── engine/        → titaniummachine1/titanium-quoridor
├── site/          → titaniummachine1/Titanium-Quoridor-Website  (+ engine submodule)
├── coordinator/   → titaniummachine1/Titanium-Quoridor-Coordinator
└── test-client/   → titaniummachine1/titanium-quoridor-test-client
```

## One-shot push (recommended)

Close VS Code / any git tools first (avoids stale `index.lock`), then:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\gitProjects\Quoridor best AI\setup_repos.ps1"
```

The script:

1. Cleans stale locks and any broken per-folder `.git` dirs
2. Inits/commits/pushes all four folders to their remotes
3. Adds `titanium-quoridor` as a submodule at `site/engine/`

**Engine note:** `titanium-quoridor` already has a `main` branch with the old monorepo layout. The script force-pushes engine-only `main`. Feature branches (`acev7-port`, `acev8-port`, etc.) are untouched.

After a successful run, delete the obsolete monorepo git dir:

```powershell
Remove-Item -Recurse -Force "C:\gitProjects\Quoridor best AI\.git"
```

## Manual push (if you prefer)

```powershell
cd "C:\gitProjects\Quoridor best AI\engine"
git init -b main
git add -A; git commit -m "Titanium engine: UCI + WASM + ACE v10 port"
git remote add origin https://github.com/titaniummachine1/titanium-quoridor.git
git push -u origin main --force   # replaces monorepo main with engine-only tree
```

Repeat for `site/`, `coordinator/`, `test-client/` with their remotes (no `--force` needed on empty repos).

Then wire the submodule:

```powershell
cd "C:\gitProjects\Quoridor best AI\site"
git submodule add https://github.com/titaniummachine1/titanium-quoridor.git engine
git commit -m "engine submodule"; git push
```

## Verify after push

```powershell
cd "C:\gitProjects\Quoridor best AI\engine"
cargo test
cargo run --release -- uci       # uci / isready / position startpos / go movetime 500 / quit
wasm-pack build --release --no-default-features --features wasm
```

## Deploy coordinator (Phase 4/5)

See `coordinator/README.md` — `wrangler login`, KV namespace, secrets, deploy, then add a push webhook on **titanium-quoridor**.
