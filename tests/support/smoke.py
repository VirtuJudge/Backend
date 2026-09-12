from typing import Any

from psycopg.errors import InsufficientPrivilege


class FakeCursor:
    def __init__(
        self,
        select_1_result: tuple[int, ...] = (1,),
        pgvector_result: tuple[str, ...] = ("vector",),
        cross_schema_fail: bool = True,
    ) -> None:
        self.select_1_result = select_1_result
        self.pgvector_result = pgvector_result
        self.cross_schema_fail = cross_schema_fail
        self.executed_statements: list[str] = []
        self._current_result: Any = None

    def execute(self, query: str, params: Any = None) -> None:
        self.executed_statements.append(query)
        if "SELECT 1" in query and "FROM" not in query:
            self._current_result = self.select_1_result
        elif "pg_extension" in query:
            self._current_result = self.pgvector_result
        elif "probe_table" in query:
            if self.cross_schema_fail:
                raise InsufficientPrivilege("permission denied for schema")
            self._current_result = (1,)
        elif "SELECT note FROM" in query:
            self._current_result = ("backend_ok",)

    def fetchone(self) -> Any:
        return self._current_result

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class FakeSyncRedis:
    def __init__(
        self,
        store: dict[str, str] | None = None,
        ping_result: bool = True,
        rpush_handler: Any | None = None,
    ) -> None:
        self.store = store if store is not None else {}
        self.ttls: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.ping_result = ping_result
        self.deleted_keys: list[str] = []
        self.rpush_handler = rpush_handler

    def ping(self) -> bool:
        return self.ping_result

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, *keys: str) -> int:
        for k in keys:
            self.store.pop(k, None)
            self.deleted_keys.append(k)
        return len(keys)

    def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        if value in items:
            items.remove(value)
            return 1
        return 0

    def rpush(self, key: str, value: str) -> int:
        if self.rpush_handler:
            self.rpush_handler(key, value)
        if key not in self.lists:
            self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])

    def close(self) -> None:
        pass
