"""Schema guardrails shared by Alembic migration modes."""

from sqlalchemy.sql.schema import SchemaItem

TARGET_SCHEMA = "assistant_core"


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
