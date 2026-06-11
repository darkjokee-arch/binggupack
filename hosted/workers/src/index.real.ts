// U2 — 실 pack entry (GO-HOSTED-REALPACK-LOCAL).
// 데이터 = private: data/packs.json은 gitignore + 배포 머신 로컬에만 존재 (json module 임베드).
// clean clone에는 data가 없어 빌드 실패 = fail-closed (의도된 동작).
// 게이트(realpack_gate.py G1~G6) 전건 통과 시에만 data/packs.json 생성이 허용된다.

import { PackStore, makeFetchHandler } from "./index";
import { loadPacks } from "./load_packs";
import packsRaw from "../data/packs.json";

export default makeFetchHandler(new PackStore(loadPacks(packsRaw)));
export { __test } from "./index"; // S28 절단 경로 검증용 (런타임 미사용)
