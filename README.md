# glibc parser

glibc의 래퍼(syscall wrapper) 함수들을 분석하여 실제 호출되는 커널 syscall과 인자 매핑을 추출하고, Redis 기반 "source of truth"에 저장하기 위한 프로젝트입니다.  
현재 버전은 v2 AST 파서를 포함하며, libclang 기반 다중 라운드 파싱(조건부 컴파일 대응), 매크로/직접호출/인라인 ASM 탐지, 텍스트 Fallback을 제공합니다. Redis 연동은 구조만 남기고 printf 로깅으로 대체되어 있습니다.

## 프로젝트 구조

```
glibc_parser/
├── src/
│   ├── main.py          # 실행 엔트리포인트, 환경변수 로딩 및 워크플로 제어
│   ├── ast_parser.py    # v2 AST 파서: libclang 기반 + 텍스트 Fallback
│   └── redis_helper.py  # Redis 연동 스텁(구조만 유지, printf 로깅)
├── k8s/
│   └── glibc-parser-job.yaml  # K8s Job 매니페스트 초안
├── workspace/           # glibc 소스코드를 수동으로 배치하는 공간
├── requirements.txt     # 파이썬 의존성 목록
└── Dockerfile           # python:3.10-slim 기반 컨테이너 정의
```

## 주요 기능 (v2)

- **바텀업 분석**: `internal_syscallN`, `INTERNAL_SYSCALL*`, `SYSCALL_CANCEL*`, `__libc_do_syscall` 등 최하위 호출로부터 상위 래퍼 함수까지 역추적
- **다중 파싱 라운드(Multi-Pass)**: 기본 플래그와 `-D_TIME_BITS=64` 조합으로 TU를 각각 파싱해 조건부 컴파일 분기 포착
- **매크로/직접호출/ASM 처리**:
  - libclang AST에서 `MacroExpansion`, `CallExpr`, `AsmStmt` 탐지
  - 조건부 컨텍스트(가까운 IfStmt 조건) 추출
  - 파싱 불가능 상황에서 텍스트 기반 Fallback
- **데이터 구조**:
  - Wrapper → `SyscallCallInfo[]` 매핑
  - `SyscallCallInfo(kernel_symbol, raw_arguments, conditional_context, source_location, origin_macro)`

## 환경 변수

| 변수명 | 기본값 | 설명 |
| ------ | ------ | ---- |
| `GLIBC_VERSION` | `2.35` | 분석 대상 glibc 버전 |
| `TARGET_ARCH` | `x86_64` | 대상 ISA 아키텍처 |
| `WORKSPACE_DIR` | `workspace` | glibc 소스를 둘 루트 경로 |
| `REDIS_HOST` | `localhost` | Redis 호스트명 (현재는 출력만 수행) |
| `REDIS_PORT` | `6379` | Redis 포트 |
| `REDIS_PASSWORD` | `""` | Redis 패스워드 |

## 실행 환경(우분투)

- Ubuntu (권장)
- 시스템 패키지: `libclang-dev` (Dockerfile에 포함)
- Python 의존성: `requirements.txt` (clang, redis, requests)
- libclang 동적 로딩 경로가 비표준인 경우 `LIBCLANG_PATH` 환경변수로 지정 가능

## 실행 흐름

1. 환경변수를 읽어 구성값을 정리합니다.
2. `workspace/<버전>` 디렉터리를 생성하여 glibc 소스가 들어갈 공간을 마련합니다.
3. glibc 소스 다운로드 함수는 주석 상태이며, 현재는 수동 배치를 안내합니다.
4. v2 AST 파서를 초기화하여 대상 C 파일을 다중 라운드로 파싱하고 Wrapper→SyscallCallInfo 매핑을 생성합니다.
5. 결과가 존재하면 Redis 헬퍼(스텁)에 전달되고, 현재는 printf 로깅으로 대체됩니다.

## 사용법

### 1) glibc 소스 준비(수동)

- `workspace/glibc-<GLIBC_VERSION>/` 아래에 실제 glibc 소스 트리를 직접 복사하거나 압축을 풀어 두어야 합니다.
- 예: `workspace/glibc-2.35/` 내부에 `sysdeps/`, `include/`, 각종 `.c` 파일이 존재해야 합니다.

### 2) 로컬 실행(우분투)

```bash
export GLIBC_VERSION=2.35
export TARGET_ARCH=x86_64
# 필요 시 libclang 경로 지정
# export LIBCLANG_PATH=/usr/lib/llvm-16/lib/libclang.so
python -m src.main
```

### 3) 단일 파일/심볼 테스트 팁
- `src/main.py`는 데모로 `open` 심볼을 분석합니다. 다른 심볼을 실험하려면 해당 부분을 바꾸거나 `GlibcAstParser.run_full_analysis()`를 직접 호출하세요.
- 컴파일 플래그가 부족해 파싱이 실패하면 `_build_default_clang_args()`를 참고해 `-I`, `-D`를 보강하세요.

## 결과 스키마(요약)

- Wrapper → `SyscallCallInfo[]` 매핑
- `SyscallCallInfo` 필드:
  - `kernel_symbol` 예: `"openat"`, `"pselect6_time64"`
  - `raw_arguments` 예: `["AT_FDCWD", "path", "flags", "mode"]`
  - `conditional_context` 예: `"if (need_time64)"`
  - `source_location` 예: `"select.c:78"`
  - `origin_macro` 예: `"SYSCALL_CANCEL"`

## 다음 단계

- `download_glibc_source()` 실제 구현 복원(주석 해제 및 예외 처리)
- Redis 연동 실구현: 커넥션, HSET 저장 로직, 에러/재시도 정책
- 파싱 정확도 향상: 컴파일 데이터베이스(compile_commands.json) 사용, 매크로 확장 토큰 정밀 분석
- 커버리지 확대: 더 많은 래퍼 함수 자동 탐지 및 회귀 테스트 추가

