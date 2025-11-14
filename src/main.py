import os
from pathlib import Path
from typing import Dict, Any

from .ast_parser import GlibcAstParser
from .redis_helper import RedisClient


def load_environment() -> Dict[str, Any]:
    """Collect runtime configuration from environment variables."""
    return {
        "glibc_version": os.getenv("GLIBC_VERSION", "2.35"),
        "target_arch": os.getenv("TARGET_ARCH", "x86_64"),
        "workspace_dir": Path(os.getenv("WORKSPACE_DIR", "workspace")).resolve(),
        "redis_host": os.getenv("REDIS_HOST", "localhost"),
        "redis_port": int(os.getenv("REDIS_PORT", "6379")),
        "redis_password": os.getenv("REDIS_PASSWORD", ""),
    }


def prepare_workspace(workspace_path: Path, glibc_version: str) -> Path:
    """Ensure the workspace directory exists and contains version-specific folders."""
    workspace_path.mkdir(parents=True, exist_ok=True)
    target_dir = workspace_path / f"glibc-{glibc_version}"
    target_dir.mkdir(exist_ok=True)
    return target_dir


def download_glibc_source(archive_dir: Path, glibc_version: str) -> None:
    """
    Placeholder for glibc source download logic.

    The actual implementation is deferred; for now, the code remains as comments.
    """
    # import tarfile
    # import tempfile
    # import requests
    #
    # archive_url = (
    #     f"https://ftp.gnu.org/gnu/libc/glibc-{glibc_version}.tar.gz"
    # )
    # archive_path = archive_dir / f"glibc-{glibc_version}.tar.gz"
    #
    # if archive_path.exists():
    #     print(f"[glibc-parser] Archive already present at {archive_path}")
    #     return
    #
    # print(f"[glibc-parser] Downloading glibc source from {archive_url}")
    # response = requests.get(archive_url, timeout=60)
    # response.raise_for_status()
    #
    # with open(archive_path, "wb") as archive_file:
    #     archive_file.write(response.content)
    #
    # with tarfile.open(archive_path, "r:gz") as tar:
    #     tar.extractall(path=archive_dir)
    #
    # print(f"[glibc-parser] Extracted glibc sources to {archive_dir}")
    print(
        "[glibc-parser] download_glibc_source is currently disabled. "
        "Please place the glibc sources manually."
    )


def main() -> None:
    config = load_environment()
    print("[glibc-parser] Starting with configuration:")
    for key, value in config.items():
        if "password" in key:
            value = "***"
        print(f"  - {key}: {value}")

    workspace_root = prepare_workspace(config["workspace_dir"], config["glibc_version"])
    download_glibc_source(workspace_root, config["glibc_version"])

    redis_client = RedisClient(
        host=config["redis_host"],
        port=config["redis_port"],
        password=config["redis_password"],
    )
    redis_client.connect()

    parser = GlibcAstParser(
        glibc_root=workspace_root,
        target_arch=config["target_arch"],
    )

    parse_result = parser.parse_wrapper_function("open")
    status = parse_result.get("status")

    if status != "parsed":
        print(
            "[glibc-parser] WARN: Parsing did not succeed. "
            f"status={status} message={parse_result.get('message', '')}"
        )
        if 'source_path' in parse_result:	
            print(f" - Analyzed File: {parse_result['source_path']}")
    else:
        redis_client.store_syscall_mapping("open", parse_result)

    print("[glibc-parser] Execution completed.")


if __name__ == "__main__":
    main()

