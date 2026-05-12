"""Helper to access config settings without circular imports."""


def should_auto_close_tabs() -> bool:
    """Check if auto close tabs is enabled."""
    from ..config import settings
    return settings.auto_close_tabs
