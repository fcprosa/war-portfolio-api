"""Telegram delivery — reserved for Session 5.

Not wired into ``run.py``. Briefs currently go to stdout + SQLite only.
"""


def send_telegram(*args, **kwargs) -> None:
    """No-op until Session 5 implements outbound Telegram."""
    return None
