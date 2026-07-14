# BingguPack - Memory PR 30-second demo (reproducible - offline - isolated).
#
#   AI proposes 3 memory candidates -> you approve exactly 1
#   -> a *fresh process* recalls exactly that 1 (verified by content digest).
#
# No network, no API key. Runs in an isolated temporary ledger; your operating
# ledger (~/.binggupack/ledger.sqlite) is never written. The demo FAILS if the
# child-process recall fails - there is no silent same-process fallback.
$ErrorActionPreference = "Stop"

if (-not (Get-Command binggu -ErrorAction SilentlyContinue)) {
    pip install --quiet binggupack
}

# Ephemeral run (auto-cleans the temp ledger on exit):
binggu demo --non-interactive

# To keep an isolated home you can inspect afterwards, run instead:
#   binggu demo --non-interactive --home ./_memory_pr_demo_home --keep
#   binggu --ledger ./_memory_pr_demo_home/ledger.sqlite list
