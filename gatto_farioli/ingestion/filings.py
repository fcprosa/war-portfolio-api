"""SEC EDGAR filing ingestion — optional later work.

Not wired into ``run.py``. Filings are lower priority than RSS + prices for
the current build; kept as a named hook for a future session.
"""


def ingest_filings(*args, **kwargs) -> None:
    """No-op until optional filing ingestion is implemented."""
    return None
