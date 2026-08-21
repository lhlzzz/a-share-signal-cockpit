"""Shared live-host binding for forward-runner extraction modules."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Tuple


def create_host_binding(
    namespace: Dict[str, Any],
    required_from_host: Iterable[str],
    optional_from_host: Iterable[str] = (),
    *,
    preserve_existing_on_missing: bool = False,
) -> Tuple[Callable[[Any], None], Callable[[], None], Callable[[Callable[..., Any]], Callable[..., Any]]]:
    """Build bind, refresh, and wrapper functions backed by a module namespace."""
    required_names = tuple(required_from_host)
    optional_names = tuple(optional_from_host)
    namespace.setdefault('_HOST', None)

    def inject_host() -> None:
        host = namespace.get('_HOST')
        if host is None:
            return
        missing = []
        for name in required_names:
            if hasattr(host, name):
                namespace[name] = getattr(host, name)
            elif not preserve_existing_on_missing or name not in namespace:
                missing.append(name)
        for name in optional_names:
            if hasattr(host, name):
                namespace[name] = getattr(host, name)
        namespace['_BIND_MISSING'] = missing

    def bind_host(host_module: Any) -> None:
        namespace['_HOST'] = host_module
        inject_host()

    def with_host(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            inject_host()
            return fn(*args, **kwargs)

        wrapper.__name__ = getattr(fn, '__name__', 'wrapper')
        wrapper.__doc__ = getattr(fn, '__doc__', None)
        wrapper.__wrapped__ = fn
        return wrapper

    return bind_host, inject_host, with_host
