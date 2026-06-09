# toy_notes (synthetic 작업 메모)

> 합성 예제용 메모입니다. 실제 프로젝트·개인정보·비밀값 없음.

## 프로젝트 개요
- 이름: toy-widget
- 목적: 작은 위젯 라이브러리를 빌드/테스트하는 예시.

## 빌드
- 빌드: `make build`
- 산출물: `dist/` 폴더에 위젯 번들 생성.

## 테스트
- 테스트: `make test`
- 단위 테스트는 `tests/` 폴더의 합성 케이스만 사용.

## 결정 메모
- 위젯 색상 팔레트는 기본 3색으로 고정한다.
- 빌드 캐시는 로컬 임시 폴더만 사용하고, 외부 전송은 하지 않는다.

## 메모 (claim)
- claim-1: toy-widget 은 make build 로 빌드한다.
- claim-2: toy-widget 의 테스트는 make test 로 실행한다.
- claim-3: 색상 팔레트는 기본 3색이다.
