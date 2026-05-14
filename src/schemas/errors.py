class SchemaValidationError(ValueError):
    """Raised when input data fails Pydantic schema validation."""


class SchemaSerializationError(TypeError):
    """Raised when a schema model fails to serialize to JSON."""
