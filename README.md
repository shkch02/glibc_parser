# glibc parser

glibc의 래퍼(syscall wrapper) 함수들을 분석하여 실제 호출되는 커널 syscall과 인자 매핑을 추출하고, Redis 기반 "source of truth"에 저장하기 위한 PoC 프로젝트입니다. 현재는 아키텍처 골격을 먼저 구현한 상태이며, 실제 Redis 연동 및 libclang 기반 파싱 로직은 이후 단계에서 채워 넣을 예정입니다.

## 프로젝트 구조

```
glibc_parser/
├── src/
│   ├── main.py          # 실행 엔트리포인트, 환경변수 로딩 및 워크플로 제어
│   ├── ast_parser.py    # libclang 파싱 스텁 (현재는 printf 로깅)
│   └── redis_helper.py  # Redis 연동 스텁 (현재는 printf 로깅)
├── k8s/
│   └── glibc-parser-job.yaml  # K8s Job 매니페스트 초안
├── workspace/           # glibc 소스코드를 수동으로 배치하는 공간
├── requirements.txt     # 파이썬 의존성 목록
└── Dockerfile           # python:3.10-slim 기반 컨테이너 정의
```

## 환경 변수

| 변수명 | 기본값 | 설명 |
| ------ | ------ | ---- |
| `GLIBC_VERSION` | `2.35` | 분석 대상 glibc 버전 |
| `TARGET_ARCH` | `x86_64` | 대상 ISA 아키텍처 |
| `WORKSPACE_DIR` | `workspace` | glibc 소스를 둘 루트 경로 |
| `REDIS_HOST` | `localhost` | Redis 호스트명 (현재는 출력만 수행) |
| `REDIS_PORT` | `6379` | Redis 포트 |
| `REDIS_PASSWORD` | `""` | Redis 패스워드 |

## 실행 흐름(현재 스텁 버전)

1. 환경변수를 읽어 구성값을 정리합니다.
2. `workspace/<버전>` 디렉터리를 생성하여 glibc 소스가 들어갈 공간을 마련합니다.
3. glibc 소스 다운로드 함수는 주석으로 남겨 두고, 대신 수동 배치를 안내하는 메시지를 출력합니다.
4. Redis 클라이언트와 AST 파서를 초기화합니다. (실제 네트워크/파싱 대신 printf 로깅만 수행)
5. `open` 래퍼 함수에 대한 가짜 결과를 만들어 Redis에 저장하는 것처럼 출력합니다.

## 수동 준비 사항

- `workspace/glibc-<GLIBC_VERSION>/` 아래에 실제 glibc 소스 트리를 직접 복사하거나 압축을 풀어 두어야 합니다.
- Redis 연결과 파싱 로직은 아직 구현되지 않았으므로, 현재 버전은 동작 시 로그로 흐름만 확인할 수 있습니다.

## 차후 계획

- `download_glibc_source()` 함수의 주석을 실제 다운로드/압축해제 로직으로 복원
- `RedisClient`를 실제 Redis 라이브러리로 교체하고 에러 처리 추가
- `GlibcAstParser`에 libclang 기반 AST 순회 및 syscall 추출 로직 구현
- `open` 외의 래퍼 함수 목록을 확장하고 결과를 Redis 스키마에 맞춰 저장
- K8s Job 매니페스트에 실제 이미지 이름과 ConfigMap/Secret 연동을 반영

## 로컬 테스트

```powershell
cd C:\Users\CAD2\Desktop\2025summer\glibc_parser
python -m src.main
```

실제 Redis나 glibc 파싱은 수행되지 않으며, 구조 확인용 로그만 출력됩니다. 이후 단계에서 printf 구문을 Redis 연결 코드와 실제 파서 결과로 교체할 예정입니다.

