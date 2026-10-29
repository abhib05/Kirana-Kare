from app.config import get_settings
from app.db.session import get_db

__all__ = ["get_db", "shop_id"]


def shop_id() -> int:
    """Single demo merchant for the MVP; swap for an auth-derived shop later."""
    return get_settings().demo_shop_id
