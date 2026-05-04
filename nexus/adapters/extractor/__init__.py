"""Adapter for the Phase 1 PHP extractor.

The Python side never boots Laravel in-process — it shells out to
``php artisan nexus:extract`` via the subprocess wrapper defined here.
See ``internal_docs/PHASE-3-indexing-pipeline.md`` §D3.2 for the
rationale (isolation from Laravel's destructive boot, timeouts,
cancellability).
"""

from nexus.adapters.extractor.errors import (
    ExtractorError,
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
)
from nexus.adapters.extractor.php_subprocess import PhpExtractor

__all__ = [
    "ExtractorError",
    "ExtractorFailedError",
    "ExtractorMissingError",
    "ExtractorTimeoutError",
    "PhpExtractor",
]
