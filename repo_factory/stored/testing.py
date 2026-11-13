"""Hypothesis strategies for the contract types: built from each dataclass's fields, so a field
added is generated on the next run. For tests only: it imports hypothesis (the ``dev`` group)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar, Union, get_args, get_origin

from hypothesis import strategies as st

from .base import ISO, Stored, _strip_optional

#: Any JSON value (no NaN or infinity: JSON has none).
json_values: st.SearchStrategy[Any] = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(2**53), 2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)

#: Any text a column could hold: JSON of any shape, broken JSON, nothing.
column_text: st.SearchStrategy[Any] = (
    st.none() | st.text(max_size=40) | st.binary(max_size=20) | json_values.map(json.dumps)
)

_isos = st.datetimes(
    min_value=datetime(1970, 1, 1), max_value=datetime(2200, 1, 1), timezones=st.just(UTC)
).map(lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ"))


def for_type(tp: object) -> st.SearchStrategy[Any]:
    """A strategy for a field's declared type."""
    inner, optional = _strip_optional(tp)
    s = _for(inner)
    return st.none() | s if optional else s


def _for(tp: object) -> st.SearchStrategy[Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        base, *marks = get_args(tp)
        if any(m is ISO for m in marks):
            return _isos
        return for_type(base)
    if tp is Any or tp is object:
        return json_values
    if isinstance(tp, type) and issubclass(tp, Stored):
        return strategy(tp)
    if tp is bool:
        return st.booleans()
    if tp is int:
        return st.integers(-(2**53), 2**53)
    if tp is float:
        return st.floats(allow_nan=False, allow_infinity=False)
    if tp is str:
        return st.text(max_size=30)
    if origin is Literal:
        return st.sampled_from(get_args(tp))
    if origin is list:
        (arg,) = get_args(tp) or (Any,)
        return st.lists(for_type(arg), max_size=5)
    if origin is dict:
        args = get_args(tp) or (str, Any)
        return st.dictionaries(st.text(max_size=8), for_type(args[1]), max_size=5)
    if origin in (Union, types.UnionType):
        return st.one_of([for_type(m) for m in get_args(tp)])
    raise TypeError(f"no strategy for {tp!r}")


T = TypeVar("T", bound=Stored)


def strategy(cls: type[T]) -> st.SearchStrategy[T]:
    """Instances of a contract type, every field drawn from its type and ``extra`` from keys no
    field names."""
    fields = cls._fields()
    named = {f.key for f in fields}
    kwargs: dict[str, st.SearchStrategy[Any]] = {f.name: for_type(f.tp) for f in fields}
    rest = cls._rest()
    if rest:
        kwargs[rest] = st.dictionaries(
            st.text(max_size=8).filter(lambda k: k not in named), json_values, max_size=3
        )
    return st.builds(cls, **kwargs)


__all__ = ["column_text", "for_type", "json_values", "strategy"]
