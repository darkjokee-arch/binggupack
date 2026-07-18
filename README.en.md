<div align="center">

<img src="assets/logo.svg" width="110" alt="BingguPack logo">

# BingguPack

### Git for AI memory

**Your AI can propose memories. Only you decide what becomes active.**
Every memory starts as a reviewable proposal — and **only what you approve** becomes active memory.

> *"Git for AI memory" is a metaphor for a **Git-like review-and-commit workflow** — it does not implement an actual Git repository or Pull Request protocol.*

[![PyPI](https://img.shields.io/pypi/v/binggupack?color=3775A9&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/binggupack/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://pypi.org/project/binggupack/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Consent-first · Local-first · Auditable · Model-agnostic**

[⚡ 60-second demo](#-60-second-demo) · [🧭 How it works](#-how-it-works) · [🛡 Safety](#-safety-model) · [🇰🇷 한국어](README.md)

</div>

---

> Maintained English translation of the Korean-first docs — see [README.md](README.md) for the full documentation. Safety boundaries have a single source of truth in [SECURITY.md](SECURITY.md) (this page summarizes; it does not redefine them). In sync as of **v1.22.0**.

## ⚡ 60-second demo

```bash
pip install binggupack
binggu demo            # no API key, no network, runs in an isolated temp ledger
```

One `binggu demo` shows the whole idea: **find memory candidates in a conversation → you approve → only approved items are committed to a local ledger → recall them from a fresh process → see what each memory is based on.** The demo runs in a temporary folder and cleans up after itself. Start a real ledger with `binggu init`.

For CI/automation: `binggu demo --non-interactive` — approval is simulated **only inside the isolated demo home**; it is not a way to bypass the real human-approval path.

## 🧭 How it works

**Memory PR** — one sentence: *"Every memory starts as a reviewable proposal (a Memory PR); only what you approve becomes active memory."* "Memory PR" is an explanatory **alias** for this flow — internal commands and the data model are unchanged.

|  | Your AI | You | Result |
|---|---|---|---|
| 🟡 **Memory PR** | proposes a candidate from the conversation | — | not active memory yet |
| ✅ **Human commit** | — | approve the exact candidate with `SAVE n` | committed to the local ledger |
| 🔎 **Recall** | — | recall an approved memory from a fresh process / session | back, with provenance |

BingguPack is **not** a tool that hoovers up every conversation. It turns durable judgments, preferences and lessons into *candidates*, and **only what you approve** becomes active memory.

| Git | BingguPack | Command |
|---|---|---|
| Pull Request | a memory candidate (Memory PR) | *(automatic)* · `binggu preview "<text>"` |
| Review | review the candidates | `binggu inbox` |
| Approve & Merge | you type `SAVE n` → commit as active memory | `SAVE n` in chat · `binggu save` |
| Commit history | approval / replace / retire history | `binggu explain <memory-id>` |
| Blame / provenance | who said it and on what evidence | `binggu explain <memory-id>` |
| Revert / supersede | swap out or retire a memory that's now wrong | `binggu replace` · `binggu forget <id>` |

> Unlike Git, there is no actual merge, branch or diff protocol — the mapping is a **conceptual metaphor**, and the commit gate is a single human-typed `SAVE n`.

**Core (pip) vs Bridge (remote relay).** The local CLI, stdio MCP, ledger, recall and explain all work offline from `pip install` alone (Core). A separate remote relay (the hosted Cloudflare Worker) lets you **mark save intents** from your phone / web / ChatGPT and pull them home (Bridge) — the intent alone never writes; a candidate becomes **active** memory only after you review the pulled bundle's preview on your PC and type `SAVE n` yourself. **Your local machine is always the source of truth**; the relay only forwards (it writes nothing to the ledger).

## 🛡 Safety model

- **No unapproved commits.** What becomes **active** memory always passes an explicit human approval. For the save path the human proof is a single principle — **preview + a human-typed `SAVE n`**: inside a Claude Code session, your keyboard `SAVE n` is recorded by a UserPromptSubmit hook and bound to the exact preview you saw (preview reference + picked indices, with a freshness window); the AI cannot pass through that hook, so merely reproducing the confirm string (`SAVE 1`, `PAIR …`, `DEPRECATE …`) does **not** get human approval. In a plain terminal outside an agent session, typing the command yourself *is* the `SAVE n` (no isatty check). Inside an agent session (identified by the `CLAUDECODE` env var) the gate is deny-only — without the hook anchor the actor stays `reader`. Owner-issued trusted approval events remain the channel for non-save mutations (`accept`/`unaccept`/`due`/`resolve`, edges import), and MCP write paths are **fail-closed** without a real anchor/approval. **Honest boundary:** the `CLAUDECODE` guard only denies — an agent that controls its own environment variables, or a non-interactive script in a plain terminal, passes the terminal path as human (looser than the old isatty gate; write still requires the exact confirm phrase and all content gates). And on a host where the *same* agent also holds filesystem/shell tools, this is fail-closed **routing**, **not** a hard control — the approval store itself is not yet isolated from such an agent (protected writer is design-only, RFC not implemented). See [SECURITY.md](SECURITY.md) for the deployment-dependent threat model.
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
