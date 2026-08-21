"""Shared scan-stop coordination.

A single module-level flag is enough because the app runs at most one scan at
a time (router guards `running`). Scrapers and the scanner pipeline poll
`stop_requested()` between pages / platforms so a click on 暫停 breaks the
loop promptly; already-scraped drafts are still persisted by the pipeline.
"""
from __future__ import annotations

_STOP = False


def request_stop() -> None:
    global _STOP
    _STOP = True


def clear_stop() -> None:
    global _STOP
    _STOP = False


def stop_requested() -> bool:
    return _STOP
