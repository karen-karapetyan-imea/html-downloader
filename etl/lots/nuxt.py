"""Decode a Nuxt 3 `__NUXT_DATA__` payload (devalue array with index references)."""

from __future__ import annotations

from typing import Any

# Wrappers Nuxt puts around reactive state. The payload we want is the inner value.
_UNWRAP = frozenset({"ShallowReactive", "Reactive", "ShallowRef", "Ref"})


def decode_nuxt_data(payload: list[Any]) -> Any:
    """Resolve index 0. Integers stored in the array are numbers; integers inside objects are indexes."""
    memo: dict[int, Any] = {}
    resolving: set[int] = set()

    def resolve(index: int) -> Any:
        if index < 0 or index >= len(payload):
            return None
        if index in memo:
            return memo[index]
        if index in resolving:
            return None
        resolving.add(index)
        value = payload[index]
        if isinstance(value, bool) or value is None or isinstance(value, str | float):
            resolved: Any = value
        elif isinstance(value, int):
            resolved = value
        elif isinstance(value, list) and _is_wrapper(value):
            resolved = resolve(value[1])
        elif isinstance(value, list):
            resolved = []
            memo[index] = resolved
            resolved.extend(_element(item) for item in value)
        elif isinstance(value, dict):
            resolved = {}
            memo[index] = resolved
            for key, item in value.items():
                resolved[key] = _element(item)
        else:
            resolved = value
        memo[index] = resolved
        resolving.discard(index)
        return resolved

    def _element(item: Any) -> Any:
        if isinstance(item, bool) or not isinstance(item, int):
            return item
        return resolve(item)

    return resolve(0)


def _is_wrapper(value: list[Any]) -> bool:
    return (
        len(value) == 2
        and isinstance(value[0], str)
        and value[0] in _UNWRAP
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )
