# -*- coding: utf-8 -*-
"""person_pack_assemble — <home>/.claude 자산 → 개인 온톨로지 팩 보조 소스 조립.

각 사용자의 ~/.claude/memory 자산(박제 전체 + traj 교훈 추출 + debate 결정 추출)을
출처 라벨과 함께 <home>/.binggupack/person_split_sources/ 에 조립 —
person_pack_split_upload 의 소스가 된다.
시크릿·인증서 인접 파일명/설명은 제외(레벨1) + repo 모듈이 경로 마스킹·PII/secret 잔존
문서 제외(레벨2)로 이중 방어. 재실행 멱등(전체 재조립).
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

MEM = Path.home() / ".claude" / "memory"
# person_split_sources = 개인 온톨로지 정체성/교훈 팩(person_pack_split_upload) 전용 소스.
# 판단팩 보조소스(person_pack_sources)와 분리 — 섞으면 판단팩이 폭증해 finalize 한도 초과.
OUT = Path.home() / ".binggupack" / "person_split_sources"

SECRET_NAME_RX = re.compile(
    r"cert|인증서|간편인증|password|passwd|apikey|api_key|credential|secret|magicline|secukit|oacx",
    re.I)
LESSON_RX = re.compile(r"★|교훈|실수[:：]|개선점[:：]|오판|함정|재발|금지")
DECISION_RX = re.compile(r"ship_recommendation|consensus|채택|GO|BLOCK|REFINE", re.I)


def _header(kind, origin):
    return "[출처: %s / 원본: %s / 자동조립 — AI 정리 자료(사용자 발화 아님)]\n\n" % (kind, origin)


def collect_pajae():
    files = []
    for base, tag in [(MEM, "작업박제"), (MEM / "박제", "작업박제")]:
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.md")):
            name = p.name
            if name.startswith(("MEMORY", "_")) or SECRET_NAME_RX.search(name):
                continue
            head = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"status:\s*deprecated", head[:800]):
                continue
            if SECRET_NAME_RX.search(head[:800]):
                continue
            files.append((tag, p, head))
    return files


def extract_traj_lessons():
    """traj 파일에서 교훈성 라인만 — 월별 다이제스트."""
    monthly = defaultdict(list)
    tdir = MEM / "traj"
    if not tdir.is_dir():
        return monthly
    for p in sorted(tdir.glob("*.md")):
        m = re.search(r"(20\d{2})(\d{2})", p.name)
        month = "%s-%s" % (m.group(1), m.group(2)) if m else "misc"
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        got = [ln.strip() for ln in lines if LESSON_RX.search(ln) and 15 <= len(ln.strip()) <= 400]
        if got:
            monthly[month].append("### %s\n%s" % (p.stem, "\n".join("- " + g.lstrip("-# ") for g in got[:12])))
    return monthly


def extract_debate_decisions():
    monthly = defaultdict(list)
    ddir = MEM / "debate"
    if not ddir.is_dir():
        return monthly
    for p in sorted(ddir.rglob("*_final.md")):
        m = re.search(r"(20\d{2})(\d{2})", p.name)
        month = "%s-%s" % (m.group(1), m.group(2)) if m else "misc"
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        got = [ln.strip() for ln in lines if DECISION_RX.search(ln) and 10 <= len(ln.strip()) <= 300]
        if got:
            monthly[month].append("### %s\n%s" % (p.stem[:80], "\n".join("- " + g for g in got[:5])))
    return monthly


def main():
    if not MEM.is_dir():
        print("메모리 자산 없음: %s — ~/.claude/memory 가 있어야 조립 가능" % MEM)
        return 1
    if OUT.exists():
        import shutil
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    n_paja = 0
    for tag, p, text in collect_pajae():
        (OUT / ("paja_%s" % p.name)).write_text(_header(tag, p.name) + text, encoding="utf-8")
        n_paja += 1
    tl = extract_traj_lessons()
    for month, blocks in sorted(tl.items()):
        (OUT / ("trajlesson_%s.md" % month)).write_text(
            _header("작업교훈(traj 추출)", "traj/%s" % month)
            + ("# %s 작업 교훈·실수 이벤트\n\n" % month) + "\n\n".join(blocks), encoding="utf-8")
    dd = extract_debate_decisions()
    for month, blocks in sorted(dd.items()):
        (OUT / ("debate_%s.md" % month)).write_text(
            _header("토론결정(debate 추출)", "debate/%s" % month)
            + ("# %s 4-CLI 토론 결정 기록\n\n" % month) + "\n\n".join(blocks), encoding="utf-8")
    total_kb = sum(f.stat().st_size for f in OUT.glob("*")) / 1024
    print("조립 완료: 박제 %d + traj다이제스트 %d + debate다이제스트 %d = %d개 (%.0fKB) → %s"
          % (n_paja, len(tl), len(dd), n_paja + len(tl) + len(dd), total_kb, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
