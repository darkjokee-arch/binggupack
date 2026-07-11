<div align="center">

<img src="assets/logo.svg" width="110" alt="BingguPack logo">

# BingguPack

**AI memory under your control.**
Record the judgments, preferences and lessons you build up while working with AI — but **only what you approve becomes active memory.**

_Git-like review and commit for what an AI remembers about you._

[![PyPI](https://img.shields.io/pypi/v/binggupack?color=3775A9&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/binggupack/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://pypi.org/project/binggupack/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Consent-first · Local-first · Auditable · Model-agnostic**

[⚡ 60-second demo](#-60-second-demo) · [🧭 How it works](#-how-it-works) · [🛡 Safety](#-safety-model) · [🇰🇷 한국어](README.md)

</div>

---

> This is a concise English entry point. The full documentation is Korean-first — see [README.md](README.md).

## ⚡ 60-second demo

```bash
pip install binggupack
binggu demo            # no API key, no network, runs in an isolated temp ledger
```

One `binggu demo` shows the whole idea: **find memory candidates in a conversation → you approve → only approved items are committed to a local ledger → recall them from a fresh process → see what each memory is based on.** The demo runs in a temporary folder and cleans up after itself. Start a real ledger with `binggu init`.

For CI/automation: `binggu demo --non-interactive` — approval is simulated **only inside the isolated demo home**; it is not a way to bypass the real human-approval path.

## 🧭 How it works

BingguPack is **not** a tool that hoovers up every conversation. It turns durable judgments, preferences and lessons into *candidates*, and **only what you approve** becomes active memory.

| Stage | What | How |
|---|---|---|
| **Candidate** | A memorable line becomes a candidate (auto-collected, *not* saved) | *(automatic)* · `binggu preview "<text>"` |
| **Review** | Look at the candidates | `binggu inbox` |
| **Commit** | Only the ones you pick land in the local ledger (human approval required) | `SAVE n` in chat · `binggu save` |
| **Recall** | Bring relevant memories back for the next task | `binggu recall "question"` |
| **Explain** | Why this memory, and its history | `binggu explain <memory-id>` |
| **Replace** | Swap out or retire a memory that's now wrong | `binggu replace` · `binggu forget <id>` |

**Core (pip) vs Bridge (remote relay).** The local CLI, stdio MCP, ledger, recall and explain all work offline from `pip install` alone (Core). A separate remote relay (the hosted Cloudflare Worker) lets you **mark save intents** from your phone / web / ChatGPT and pull them home (Bridge) — the intent alone never writes; a candidate becomes **active** memory only when you approve the exact-bound bundle on your PC. **Your local machine is always the source of truth**; the relay only forwards (it writes nothing to the ledger).

## 🛡 Safety model

- **No unapproved commits.** What becomes **active** memory always passes an explicit human approval. Approval is grounded in an **out-of-band anchor** — your keyboard `SAVE n` (recorded by a UserPromptSubmit hook), CLI TTY input, or an owner-issued trusted approval event. An agent that merely reproduces the confirm string (`SAVE 1`, `PAIR …`, `DEPRECATE …`) does **not** get human approval; MCP write paths are **fail-closed** without a real anchor. **Honest boundary:** on a host where the *same* agent also holds filesystem/shell tools, this is fail-closed **routing** (auto-save prevention + a non-interactive owner path), **not** a hard control — the approval store itself is not yet isolated from such an agent (protected writer is design-only, RFC not implemented). See [SECURITY.md](SECURITY.md) for the deployment-dependent threat model.
- **Candidates auto-collect; commits don't.** Memorable lines may be gathered into temporary candidates automatically. Turning a candidate into an active memory always requires your approval.
- **Tamper-evident, not tamper-proof.** A hash chain + Merkle root detect accidental or partial corruption of the local SQLite ledger. This is **not** a cryptographic seal against an attacker who fully controls your machine — see [SECURITY.md](SECURITY.md) for the threat model.
- **Local-first.** The operating source of truth is local (`~/.binggupack`). Cloud is a read-only / masked side channel.

These promises are enforced by regression tests — see the "검증되는 불변식 (invariants enforced by tests)" section in [SECURITY.md](SECURITY.md).

## Install & next steps

```bash
pip install binggupack
binggu start            # create your local ledger
binggu                  # home screen: status + what to do next
```

- Local CLI + **stdio MCP** (Claude Code, Codex) work from `pip install` alone. See [INSTALL.md](INSTALL.md).
- The **ChatGPT / web connector** save channel (the Bridge) needs the hosted worker source, so `git clone` and run it separately (the pip wheel does not bundle `hosted/`).

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
