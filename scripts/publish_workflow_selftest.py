# -*- coding: utf-8 -*-
"""BingguPack — publish.yml fail-closed 계약 정적 selftest (P1-C · Track release-integrity).

.github/workflows/publish.yml 을 파싱해, PyPI 업로드가 다음을 강제하는지 검증한다:
  · publish_branch_ref_blocked        — 비-tag ref 는 업로드 전 exit 1
  · publish_tag_version_mismatch_blocked — tag != pyproject version 이면 차단
  · publish_about_version_mismatch_blocked — pyproject != __about__ SSOT 이면 차단
  · publish_exact_rc_tag_allowed      — v<pyproject-version>(RC=v1.19.0rc1, 하이픈 없음) 는 통과
  · publish_tag_head_bound            — tag commit == checkout HEAD
  · publish_dirty_tree_blocked        — working tree dirty 면 차단
  · publish_no_token_fallback         — API 토큰/비밀번호 fallback 없음(OIDC 전용)
  · publish_release_autotrigger_disabled — GitHub Release published 자동 트리거 비활성
  · publish_privatepath_scan_registered  — private_path_scan 을 소스+빌드산출물에서 실행
  · publish_third_party_actions_pinned   — 외부 배포 action 은 immutable commit SHA 에 고정

정적 검사만(워크플로 실행 안 함) · read-only · FS write 0 · 네트워크 0.
CLI: python scripts/publish_workflow_selftest.py [--selftest]
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLISH_YML = os.path.join(BASE, ".github", "workflows", "publish.yml")
PYPROJECT = os.path.join(BASE, "pyproject.toml")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _pyproject_version(raw):
    m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', raw, re.MULTILINE)
    return m.group(1) if m else None


def _active_on_block(text):
    """on: 블록에서 주석(#) 제외한 활성 라인만 모아 반환."""
    lines = text.splitlines()
    out, in_on = [], False
    for ln in lines:
        stripped = ln.strip()
        if re.match(r'^on\s*:', ln):
            in_on = True
            continue
        if in_on:
            # 다음 최상위 키(들여쓰기 0, 알파벳)에서 on: 블록 종료
            if ln and not ln[0].isspace() and not stripped.startswith("#"):
                break
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
    return out


def run_selftest():
    yml = _read(PUBLISH_YML)
    pyver = _pyproject_version(_read(PYPROJECT))
    checks = []

    def rec(cid, ok, detail=""):
        checks.append((cid, bool(ok), detail))

    # 1) branch ref 차단
    rec("publish_branch_ref_blocked",
        ('"${GITHUB_REF_TYPE}" != "tag"' in yml) and re.search(
            r'GITHUB_REF_TYPE\}"\s*!=\s*"tag"[\s\S]{0,200}?exit 1', yml),
        "비-tag ref → exit 1")

    # 2) tag != pyproject version 차단
    rec("publish_tag_version_mismatch_blocked",
        re.search(r'"\$\{TAG_VERSION\}"\s*!=\s*"\$\{PYPROJECT_VERSION\}"[\s\S]{0,200}?exit 1', yml),
        "tag != version → exit 1")

    # 3) pyproject != __about__ SSOT 차단
    rec("publish_about_version_mismatch_blocked",
        ("__about__.py" in yml) and re.search(
            r'"\$\{PYPROJECT_VERSION\}"\s*!=\s*"\$\{ABOUT_VERSION\}"[\s\S]{0,200}?exit 1', yml),
        "pyproject != __about__ → exit 1")

    # 4) exact RC tag 통과 — v<version> 에서 v 제거 == version, RC 는 하이픈 없음
    tag = "v" + (pyver or "")
    stripped = tag[1:] if tag.startswith("v") else tag
    rec("publish_exact_rc_tag_allowed",
        (pyver is not None) and (stripped == pyver) and ("-" not in pyver)
        and ('${GITHUB_REF_NAME#v}' in yml),
        "tag=v%s → %s == pyproject %s (하이픈 없음)" % (pyver, stripped, pyver))

    # 5) tag commit == checkout HEAD
    rec("publish_tag_head_bound",
        ("git rev-parse HEAD" in yml) and ("GITHUB_SHA" in yml),
        "HEAD == GITHUB_SHA")

    # 6) dirty tree 차단
    rec("publish_dirty_tree_blocked",
        re.search(r'git status --porcelain[\s\S]{0,200}?exit 1', yml),
        "dirty → exit 1")

    # 7) 토큰 fallback 없음(OIDC 전용)
    token_antipatterns = [r'\bpassword\s*:', r'PYPI_API_TOKEN', r'TWINE_PASSWORD',
                          r'TWINE_USERNAME', r'secrets\.PYPI', r'api[-_]token']
    has_token = any(re.search(p, yml, re.IGNORECASE) for p in token_antipatterns)
    rec("publish_no_token_fallback",
        (not has_token) and ("id-token: write" in yml) and ("pypa/gh-action-pypi-publish" in yml),
        "OIDC 전용 · 토큰 anti-pattern 0")

    # 8) release published 자동 트리거 비활성
    on_lines = _active_on_block(yml)
    has_dispatch = any(l.startswith("workflow_dispatch") for l in on_lines)
    has_release_trigger = any(l.startswith("release") for l in on_lines)
    rec("publish_release_autotrigger_disabled",
        has_dispatch and (not has_release_trigger),
        "on=%s" % ",".join(on_lines) if on_lines else "on=(none)")

    # 9) private_path_scan 등록(소스 + 빌드 산출물)
    rec("publish_privatepath_scan_registered",
        ("private_path_scan.py --source" in yml) and ("private_path_scan.py --tree" in yml),
        "source + built-artifact 스캔")

    # 10) supply-chain 변경 방지: 외부 publish action은 움직이는 tag/branch가 아닌 SHA 고정
    publish_action = re.search(
        r"uses:\s*pypa/gh-action-pypi-publish@([^\s#]+)", yml
    )
    publish_ref = publish_action.group(1) if publish_action else ""
    rec("publish_third_party_actions_pinned",
        bool(re.fullmatch(r"[0-9a-f]{40}", publish_ref)),
        "pypa publish ref=%s" % (publish_ref or "missing"))

    print("=" * 74)
    print("BingguPack publish.yml fail-closed 계약 정적 selftest")
    print("=" * 74)
    npass = sum(1 for _, ok, _ in checks if ok)
    for cid, ok, detail in checks:
        print("%s %-42s %s" % ("[OK]" if ok else "[X]", cid, detail))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(checks)))
    gate = "GO" if npass == len(checks) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


def main(argv):
    if not argv or argv[0] == "--selftest":
        return run_selftest()
    print("usage: publish_workflow_selftest.py [--selftest]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
