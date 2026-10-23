"""Runtime assertions that preserve the validated type in test expressions."""

from typing import TypeVar, Mapping
from email.message import Message

T = TypeVar("T")


def require_present(value: T | None) -> T:
    """Fail at the missing fixture/result instead of its next dereference."""
    assert value is not None, "Expected a non-null test result"
    return value


def require_type(value: object, expected: type[T]) -> T:
    """Check a discriminated result and expose its concrete type to callers."""
    assert isinstance(value, expected), f"Expected {expected.__name__}, got {type(value).__name__}"
    return value


def http_headers(values: Mapping[str, str]) -> Message:
    """Construct the header object required by urllib HTTPError fixtures."""
    result = Message()
    for name, value in values.items():
        result[name] = value
    return result
