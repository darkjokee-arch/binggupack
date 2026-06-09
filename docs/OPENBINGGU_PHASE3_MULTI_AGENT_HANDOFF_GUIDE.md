# BingguPack Phase 3 — Multi-Agent Handoff Guide (DESIGN/문서 ONLY)

> 목적: 사용자가 만든 pack(또는 로컬 candidate memory)을 **Claude·Codex·ChatGPT·Gemini가 같은 맥락으로 이어받게** 하는 방법.
> ⚠️ 문서/가이드만. 코드·write·push·OpenCrab·Neo4j·MCP write 0. 기준: v0.2.0-rc1(read/dry-run + pack validation + local persistence opt-in).
> 관련: `OPENBINGGU_PACK_CONTRACT.md`(RC) · `OPENBINGGU_CONSUMER_READER_CONTRACT_DESIGN.md` · A3 reader 검증(`A3_REAL_READER_RESULT`·`A3_COMPLEX_READER_RESULT`).

## 1. 한 줄
pack = **핵심 문장 노드 + 동사형 edge + 모든 edge의 evidence_refs + evidence index**의 묶음(JSONL canonical). 여러 AI가 이 pack을 읽어 **evidence 기반으로** 동일 맥락을 이어받는다. pack은 candidate이며 받은 쪽이 자동 병합하지 않는다.

## 2. pack consumer contract 요약
- **입력**: pack(manifest + `graph/nodes.jsonl` + `graph/edges.jsonl` + `evidence/index.jsonl`[+evidence_chunk]).
- **읽기 규칙**:
  1. 노드 = 핵심 문장(단어 아님). 5종(문서/증거/개념/상태/판단).
  2. edge = 동사형 관계(supports_judgment/contradicts/refines/causes/blocks/enables/depends_on/supersedes). **모든 edge는 evidence_refs 필수**.
  3. 모든 노드/엣지는 **candidate**(promotion_allowed=0). 받은 AI가 confirmed로 올리지 않는다(사람 검토 = Phase 4).
  4. evidence_refs가 가리키는 evidence index 항목을 **근거로만** 답한다. evidence 없는 주장 = 생성 금지.
- **출력 상태**(consumer reader contract): 읽음/근거제시/모름/충돌표시/검토필요 — 추측 생성 금지.

## 3. evidence_refs 기반 답변 규칙 (모든 모델 공통)
1. **추측 금지**: pack에 evidence_refs로 뒷받침되지 않는 사실을 새로 만들지 않는다. 모르면 "pack에 근거 없음"이라고 답한다.
2. **evidence 없는 edge는 candidate**: node→node 관계를 새로 제안할 때 evidence 직접성이 없으면 confirmed가 아닌 **candidate 제안**으로만 표시.
3. **출처 표기**: 답변 시 어떤 evidence_id/node_id에 근거했는지 밝힌다(raw 경로/secret 금지, id만).
4. **충돌 보존**: contradicts edge가 있으면 양쪽을 다 제시하고 임의 결론 금지(사람 판단 영역).
5. **자동 병합/승격 금지**: 받은 pack을 자기 그래프/메모리에 자동 반영하지 않는다(검토 후 수동).

## 4. 모델별 prompt template

### Claude
```
You are reading a BingguPack (evidence-backed context pack).
Rules:
- Treat every node as a candidate (not confirmed). Do not promote.
- Answer ONLY from evidence_refs. If a claim has no evidence_ref, say "no evidence in pack".
- Cite node_id / evidence_id you relied on (ids only, never raw paths/secrets).
- If a contradicts edge exists, present both sides; do not resolve it yourself.
- Do not merge this pack into any store automatically.
Pack: <manifest + nodes.jsonl + edges.jsonl + evidence/index.jsonl>
Task: <user question>
```

### Codex (gpt-5.x)
```
Context: BingguPack v1 (graph/nodes.jsonl, graph/edges.jsonl, evidence/index.jsonl).
Constraints: candidate-only; evidence-grounded answers; cite ids; no auto-merge; preserve contradictions.
If evidence is missing for a needed fact, return "INSUFFICIENT_EVIDENCE" rather than guessing.
Pack: <...>   Task: <...>
```

### ChatGPT
```
You will read an evidence-backed pack (nodes/edges/evidence JSONL).
- Answer strictly from evidence_refs; no fabrication. Missing → "not in pack".
- Cite node_id/evidence_id. Keep contradictions open. Never auto-apply or promote.
Pack: <...>   Task: <...>
```

### Gemini
```
Read this BingguPack (nodes.jsonl + edges.jsonl + evidence/index.jsonl).
Answer only with evidence_refs support; cite ids; if unsupported say "no evidence".
All nodes/edges are candidates — do not confirm/merge. Preserve contradicts edges.
Pack: <...>   Task: <...>
```

## 5. 검증된 근거 (재인용)
A3 멀티에이전트 reader 검증(synthetic + 실 Codex/Gemini): 핵심판단/edge/layer/confirmed 0 일치, 복잡 pack 3 reader GATE=GO. → 본 가이드의 "evidence 기반·candidate·추측 금지" 규칙이 실 reader에서 재현 가능함을 뒷받침(synthetic/실 reader 기준).

## 6. HOLD
confirmed 생성/promote(Phase 4) · pack을 OpenCrab으로 finalize/upload(Phase 5) · 운영 store 자동 병합 · MCP write 도구 · 자동 수집 daemon(Phase 6).

## 7. 다음
- 공개 RC docs에 본 가이드 + prompt template 반영(별도 GO, write opt-in 무관·문서).
- toy pack으로 4모델 handoff 데모 시나리오(read-only) 작성 후보.
