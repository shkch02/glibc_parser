from typing import Dict, Any


class RedisClient:
    """
    Structured placeholder for Redis interactions.

    Actual network operations are deferred; current implementation uses prints.
    """

    def __init__(self, host: str, port: int, password: str) -> None:
        self.host = host
        self.port = port
        self.password = password
        self._connected = False

    def connect(self) -> None:
        print(
            "[glibc-parser] RedisClient.connect() called "
            f"(host={self.host}, port={self.port})"
        )
        self._connected = True

    def store_syscall_mapping(self, wrapper_symbol: str, payload: Dict[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("RedisClient.store_syscall_mapping() called before connect().")

        print(
            "[glibc-parser] RedisClient.store_syscall_mapping() called "
            f"for `{wrapper_symbol}` with payload={payload}"
        )

