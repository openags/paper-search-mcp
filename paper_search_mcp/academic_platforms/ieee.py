"""IEEE Xplore connector — optional, requires API key env.

This module is a **skeleton only**.  No real IEEE Xplore API requests are made
unless the ``PAPER_SEARCH_MCP_IEEE_API_KEY`` (or legacy ``IEEE_API_KEY``)
environment variable is configured.  All methods
raise :class:`NotImplementedError` with a descriptive message when accessed
without a valid key so that the rest of the platform continues to work without
any paid credentials.

Enable usage::

    export PAPER_SEARCH_MCP_IEEE_API_KEY=<your_ieee_api_key>
"""

from __future__ import annotations

import logging
from typing import List

from .base import PaperSource
from ..paper import Paper
from ..config import get_env

logger = logging.getLogger(__name__)

_NOT_CONFIGURED_MSG = (
    "IEEE Xplore is not configured.  Set PAPER_SEARCH_MCP_IEEE_API_KEY "
    "(or legacy IEEE_API_KEY) environment variable "
    "to enable IEEE Xplore search and download.  "
    "Obtain a free API key at https://developer.ieee.org/."
)


class IEEESearcher(PaperSource):
    """Skeleton connector for IEEE Xplore.

    Instantiating this class without ``PAPER_SEARCH_MCP_IEEE_API_KEY``
    (or ``IEEE_API_KEY``) set will log a warning
    but will NOT raise an error.  All actual operations will raise
    :class:`NotImplementedError` with a clear message directing the user to
    configure their API key.
    """

    # Base URL for IEEE Xplore REST API (v1)
    BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(self) -> None:
        self.api_key: str = get_env("IEEE_API_KEY", "")
        if not self.api_key:
            logger.warning(
                "IEEESearcher initialised without PAPER_SEARCH_MCP_IEEE_API_KEY/IEEE_API_KEY.  "
                "All calls will raise NotImplementedError until the key is set."
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True only when a non-empty IEEE API key is available."""
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:  # type: ignore[override]
        """Search IEEE Xplore — requires PAPER_SEARCH_MCP_IEEE_API_KEY or IEEE_API_KEY.

        Raises:
            NotImplementedError: Always, when IEEE API key env is not set.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        # TODO: implement real IEEE Xplore REST call here once key is available
        raise NotImplementedError(
            "IEEE Xplore search is not yet implemented.  "
            "Contribute at https://github.com/your-repo/paper-search-mcp."
        )

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from IEEE Xplore — requires IEEE API key env and institutional access.

        Raises:
            NotImplementedError: Always, until key + download logic are implemented.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore PDF download is not yet implemented.  "
            "Note: full-text download also requires institutional IEEE access."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper content from IEEE Xplore — requires IEEE API key env.

        Raises:
            NotImplementedError: Always, until download + extraction are implemented.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore paper reading is not yet implemented."
        )
