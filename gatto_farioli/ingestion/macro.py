"""Macro data ingestion — reserved for Session 2 (FRED / custom indicators).

Not wired into ``run.py`` yet. Kept as a package anchor so Session 2 can add
``ingest_macro()`` without reshuffling the ingestion layout.
"""


def ingest_macro(*args, **kwargs) -> None:
    """No-op until Session 2 implements FRED ingestion."""
    return None
