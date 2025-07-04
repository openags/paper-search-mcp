# paper_search_mcp/academic_platforms/__init__.py

"""
This package provides modules for searching various academic platforms.
Each module should contain a searcher class that implements a common interface
(e.g., inheriting from a base PaperSource class and having a 'search' method).
"""

from .arxiv import ArxivSearcher
from .biorxiv import BioRxivSearcher # Corrected capitalization
from .google_scholar import GoogleScholarSearcher
# hub.py is not a searcher, so it's not imported here for direct use as a platform
from .iacr import IACRSearcher
from .medrxiv import MedRxivSearcher # Corrected capitalization
from .pubmed import PubMedSearcher
from .scopus import ScopusSearcher
from .semantic import SemanticSearcher
from .shodhganga import ShodhgangaSearcher

__all__ = [
    "ArxivSearcher",
    "BioRxivSearcher", # Corrected capitalization
    "GoogleScholarSearcher",
    "IACRSearcher",
    "MedRxivSearcher", # Corrected capitalization
    "PubMedSearcher",
    "ScopusSearcher",
    "SemanticSearcher",
    "ShodhgangaSearcher",
]
