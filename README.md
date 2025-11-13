# glibc parser

glibc 래퍼(syscall wrapper) 함수가 호출하는 커널 syscall을 역추적하여 Wrapper → Kernel 매핑을 수집하는 프로젝트입니다.  
현재 버전(v2)은 libclang 기반 AST 파서와 텍스트 Fallback을 모두 지원하며, Redis 연동은 구조만 남겨둔 상태(printf 로깅)입니다.

---

## 주요 기능

- **바텀업 분석**: `internal_syscallN`, `INTERNAL_SYSCALL*`, `SYSCALL_CANCEL*`, `__libc_do_syscall` 등 최하위 호출 지점에서 시작해 상위 래퍼 함수까지 역추적
- **다중 파싱 라운드**: 기본 플래그 + `-D_TIME_BITS=64` 조합으로 각각 TU를 파싱하여 조건부 컴파일 분기 누락 최소화
- **매크로/직접 호출/인라인 ASM 처리**
  - AST에서 `MacroExpansion`, `CallExpr`, `AsmStmt` 탐지
  - 인라인 asm은 힌트 수준의 SyscallCallInfo로 기록
  - AST 파싱이 실패할 경우 텍스트 기반 Fallback으로 보완
- **데이터 구조**
  - Wrapper → `SyscallCallInfo[]`
  - `SyscallCallInfo(kernel_symbol, raw_arguments, conditional_context, source_location, origin_macro)`

---

## 디렉터리 구조

```
glibc_parser/
├── src/
│   ├── main.py          # 실행 엔트리포인트 (환경변수 로딩, 파서 호출, Redis 스텁 로깅)
│   ├── ast_parser.py    # v2 AST 파서 (libclang + Fallback)
│   └── redis_helper.py  # Redis 스텁 (연결/저장 동작을 print로 대체)
├── k8s/
│   └── glibc-parser-job.yaml  # K8s Job 매니페스트 초안
├── workspace/           # glibc 소스를 수동 배치하는 경로 (버전별 하위 폴더)
├── requirements.txt     # 파이썬 의존성 (clang, redis, requests)
└── Dockerfile           # python:3.10-slim 기반 컨테이너 정의
```

---

## 사전 준비 사항

| 항목 | 내용 |
| ---- | ---- |
| glibc 소스 | `workspace/glibc-<버전>/`에 직접 복사 또는 압축 해제 |
| 시스템 패키지 (로컬 실행 시) | Ubuntu 기준 `apt install libclang-dev` 권장 |
| Python 패키지 | `pip install -r requirements.txt` (clang, redis, requests) |
| libclang 경로 | 비표준 위치 사용 시 `LIBCLANG_PATH=/path/to/libclang.so` 지정 |

> **중요**: Docker 이미지에도 glibc 소스는 포함되지 않습니다. 실행 시 호스트 디렉터리를 `/app/workspace`에 마운트해야 합니다.

---

## 환경 변수

| 변수명 | 기본값 | 설명 |
| ------ | ------ | ---- |
| `GLIBC_VERSION` | `2.35` | 사용할 glibc 소스 버전 (`workspace/glibc-<버전>` 폴더명과 일치) |
| `TARGET_ARCH` | `x86_64` | 분석 대상 아키텍처 (`_build_default_clang_args`에서 사용) |
| `WORKSPACE_DIR` | `workspace` | glibc 소스 루트 경로 (상대/절대 경로 모두 가능) |
| `REDIS_HOST` | `localhost` | Redis 호스트 (현재는 로그용) |
| `REDIS_PORT` | `6379` | Redis 포트 (현재는 로그용) |
| `REDIS_PASSWORD` | `""` | Redis 패스워드 (현재는 로그용) |
| `LIBCLANG_PATH` | unset | libclang 공유 라이브러리 경로 (선택) |

---

## 실행 방법

### 1. Docker 사용

1. 이미지 빌드
   ```bash
   docker build -t glibc-parser:v2 .
   ```

2. 실행  
   glibc 소스가 `/home/USER/glibc-sources/glibc-2.42/`에 있다고 가정할 때:
   ```bash
    docker run --rm \
      -v /home/USER/glibc-sources:/app/workspace \
      -e GLIBC_VERSION=2.42 \
      -e TARGET_ARCH=x86_64 \
      glibc-parser:v2
   ```
   - 컨테이너 내부에서는 `/app/workspace/glibc-2.42/` 경로가 존재해야 합니다.
   - 추가 플래그가 필요하면 `-e LIBCLANG_PATH=/usr/lib/llvm-16/lib/libclang.so` 같은 환경변수를 추가하세요.

### 2. 로컬 실행 (Ubuntu 예시)

```bash
# 1) 의존성 설치
sudo apt-get update && sudo apt-get install -y libclang-dev
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2) glibc 소스 배치 (예: workspace/glibc-2.42/)
# 3) 필요 시 환경변수 설정
export GLIBC_VERSION=2.42
export TARGET_ARCH=x86_64
# export LIBCLANG_PATH=/usr/lib/llvm-16/lib/libclang.so

# 4) 실행
python -m src.main
```

로그에는 파싱 결과 또는 실패 원인이 출력됩니다. Redis 저장 동작은 아직 스텁이며 실제 네트워크 연결은 수행하지 않습니다.

---

## 파서 활용 팁

- 기본 엔트리포인트(`src/main.py`)는 `open` 래퍼 함수를 대상으로 동작 검증용 파싱을 수행합니다.
- 대규모 분석을 수행하려면 `src/ast_parser.py`의 `GlibcAstParser.run_full_analysis()`를 직접 호출하여 Wrapper → `SyscallCallInfo[]` 매핑을 얻을 수 있습니다.
- 파싱에 실패한다면 `_build_default_clang_args()`를 참고해 추가적인 `-I`, `-D` 플래그를 전달하거나, `LIBCLANG_PATH`를 명시적으로 지정해 보세요.

---

## 향후 계획

- `download_glibc_source()` 함수 복원 및 예외 처리
- Redis 스텁을 실제 클라이언트로 교체하고 오류/재시도 정책 추가
- compile_commands.json 활용, 추가 매크로 패턴 지원 등 파싱 정확도 향상
- 파서 결과 검증용 테스트 벤치 및 CI 파이프라인 마련

---

## 변경 이력 (요약)

- **v1**: 프로젝트 골격 + Redis/파서 스텁
- **v2**: 다중 라운드 libclang 파서, 텍스트 Fallback, README 전면 갱신, Docker 실행 가이드 추가
