"""Shared base for WERA's Pydantic contracts (SPEC §6).

Pydantic is canonical: these models export JSON Schema to packages/schemas/json, and the
web app's TypeScript types are generated from that (SCH-03). On the wire, fields are
camelCase. `Opt[X]` is an optional field that is omitted when unset (TypeScript `x?: X`);
`X | None` without a default is a required field that may be null (`x: X | null`).
"""

from collections.abc import Callable
from typing import Annotated, Any, TypeVar, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

T = TypeVar("T")


def _is_none(v: Any) -> bool:
    return v is None


Opt = Annotated[Union[T, SkipJsonSchema[None]], Field(exclude_if=_is_none)]  # noqa: UP007 — generic alias


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        json_schema_serialization_defaults_required=True,
    )


def fail(kind: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(kind, message)


def required(message: str) -> AfterValidator:
    """A non-empty string, with a message that names the field (as the Zod schemas did)."""

    def check(v: str) -> str:
        if not v:
            raise fail("required", message)
        return v

    return AfterValidator(check)


def count(lo: int, hi: int | None, too_few: str, too_many: str | None = None) -> AfterValidator:
    def check(v: list) -> list:
        if len(v) < lo:
            raise fail("too_few", too_few)
        if hi is not None and len(v) > hi:
            raise fail("too_many", too_many or too_few)
        return v

    return AfterValidator(check)


def check(fn: Callable[[Any], bool], kind: str, message: str) -> AfterValidator:
    def run(v: Any) -> Any:
        if not fn(v):
            raise fail(kind, message)
        return v

    return AfterValidator(run)
