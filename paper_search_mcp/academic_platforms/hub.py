# paper_search_mcp/academic_platforms/hub.py

"""
Central hub for accessing different academic platform searchers.
This allows for dynamic instantiation of searchers based on a key.
"""

from .arxiv import ArxivSearcher
from .biorxiv import BiorxivSearcher
from .google_scholar import GoogleScholarSearcher
from .iacr import IACRSearcher
from .medrxiv import MedrxivSearcher
from .pubmed import PubMedSearcher
from .scopus import ScopusSearcher
from .semantic import SemanticSearcher
from .shodhganga import ShodhgangaSearcher

# A dictionary mapping platform names (keys) to their searcher classes.
# This allows for easy lookup and instantiation of searchers.
AVAILABLE_SEARCHERS = {
    "arxiv": ArxivSearcher,
    "biorxiv": BiorxivSearcher,
    "google_scholar": GoogleScholarSearcher,
    "iacr": IACRSearcher,
    "medrxiv": MedrxivSearcher,
    "pubmed": PubMedSearcher,
    "scopus": ScopusSearcher,
    "semantic_scholar": SemanticSearcher, # Assuming 'semantic_scholar' as key for SemanticSearcher
    "shodhganga": ShodhgangaSearcher,
}

def get_searcher(platform_name: str):
    """
    Returns an instance of the searcher for the given platform name.

    Args:
        platform_name (str): The key for the desired platform
                             (e.g., "arxiv", "pubmed", "shodhganga").

    Returns:
        An instance of the searcher class if found, otherwise None.

    Raises:
        ValueError: If the platform_name is not recognized.
    """
    platform_name = platform_name.lower()
    searcher_class = AVAILABLE_SEARCHERS.get(platform_name)
    if searcher_class:
        return searcher_class() # Instantiate the class
    else:
        raise ValueError(f"Unknown platform: {platform_name}. Available platforms are: {list(AVAILABLE_SEARCHERS.keys())}")

if __name__ == '__main__':
    # Example usage:
    print(f"Available searcher platforms: {list(AVAILABLE_SEARCHERS.keys())}")

    try:
        arxiv_searcher = get_searcher("arxiv")
        print(f"Successfully got searcher for 'arxiv': {type(arxiv_searcher)}")

        shodhganga_searcher = get_searcher("shodhganga")
        print(f"Successfully got searcher for 'shodhganga': {type(shodhganga_searcher)}")

        # Test a non-existent platform
        # get_searcher("nonexistent_platform")

    except ValueError as e:
        print(f"Error: {e}")
    except ImportError as e:
        print(f"ImportError: {e}. This might indicate an issue with the class names in __init__.py or the files themselves.")
        print("Please ensure all Searcher classes (e.g., ArxivSearcher, PubMedSearcher) are correctly defined and imported.")

# TODO: Consider adding a more robust plugin system if the number of platforms grows significantly.
# TODO: Potentially load API keys or configurations here if needed by searchers in the future.
