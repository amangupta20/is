"""Schema guardrails shared by Alembic migration modes."""

from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.sql.schema import SchemaItem

TARGET_SCHEMA = "assistant_core"
REFLECTION_DEFAULT_SCHEMA = "__alembic_non_target_default__"


def include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    """Allow reflected schema and table discovery only inside assistant_core."""
    if type_ == "schema":
        return name == TARGET_SCHEMA
    if type_ == "table":
        return parent_names.get("schema_name") == TARGET_SCHEMA
    return True


def validate_default_schema(default_schema_name: str | None) -> None:
    """Require the dedicated role before treating Alembic's None schema as the target."""
    if default_schema_name != TARGET_SCHEMA:
        raise RuntimeError(f"migration connection default schema must be {TARGET_SCHEMA}")


def qualify_target_schema(dialect: Dialect) -> None:
    """Keep target reflection schema-qualified after validating the dedicated role."""
    validate_default_schema(dialect.default_schema_name)
    dialect.default_schema_name = REFLECTION_DEFAULT_SCHEMA


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """Allow target-metadata table comparison only inside assistant_core."""
    if type_ == "table":
        return getattr(object_, "schema", None) == TARGET_SCHEMA
    return True
