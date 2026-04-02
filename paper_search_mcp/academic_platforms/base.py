"""Base class for all academic paper source searchers."""
from abc import ABC, abstractmethod
from typing import List
from ..paper import Paper


class PaperSource(ABC):
    """Abstract base class for academic paper sources."""

    @abstractmethod
    def search(self, query: str, **kwargs) -> List[Paper]:
        """Search papers matching the query.

        Args:
            query: Search query string.
            **kwargs: Source-specific parameters (e.g., max_results, year).

        Returns:
            List of Paper objects.
        """

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download the PDF for a given paper.

        Args:
            paper_id: Platform-specific paper identifier.
            save_path: Directory to save the downloaded PDF.

        Returns:
            Path to the saved PDF file.

        Raises:
            NotImplementedError: If the source does not support PDF downloads.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support PDF downloads."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download and extract text from a paper PDF.

        Args:
            paper_id: Platform-specific paper identifier.
            save_path: Directory where the PDF is/will be saved.

        Returns:
            Extracted text content of the paper.

        Raises:
            NotImplementedError: If the source does not support paper reading.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support reading paper content."
        )
