# paper_search_mcp/academic_platforms/scopus.py
from typing import List
from datetime import datetime
import os
from elsapy.elsclient import ElsClient
from elsapy.elsprofile import ElsAuthor, ElsAffil
from elsapy.elsdoc import AbsDoc, FullDoc
from elsapy.elssearch import ElsSearch
import json
import os # Import os module
from ..paper import Paper

# API_KEY will now be fetched from environment variable

class PaperSource:
    """Abstract base class for paper sources"""
    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

class ScopusSearcher(PaperSource):
    """Searcher for Scopus papers"""
    def __init__(self, api_key: str = None):
        env_api_key = os.environ.get("SCOPUS_API_KEY")
        final_api_key = api_key if api_key is not None else env_api_key

        if not final_api_key:
            raise ValueError("Scopus API key not provided. Set SCOPUS_API_KEY environment variable or pass it during instantiation.")
        self.client = ElsClient(final_api_key)

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        doc_srch = ElsSearch(query, 'scopus')
        doc_srch.execute(self.client, get_all=False, count=max_results)

        papers = []
        for result in doc_srch.results:
            try:
                # Extract basic metadata
                paper_id = result.get('dc:identifier', '').replace('SCOPUS_ID:', '')
                title = result.get('dc:title', '')
                authors = [author['authname'] for author in result.get('author', [])]
                abstract = result.get('dc:description', '') # Or 'prism:teaser'
                doi = result.get('prism:doi', '')
                url = result.get('prism:url', '') # Link to Scopus page
                pdf_url = '' # Scopus API does not typically provide direct PDF links

                # Publication date
                published_date_str = result.get('prism:coverDate', '')
                published_date = None
                if published_date_str:
                    try:
                        published_date = datetime.strptime(published_date_str, '%Y-%m-%d')
                    except ValueError:
                        pass # Handle other date formats if necessary

                papers.append(Paper(
                    paper_id=paper_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    url=url,
                    pdf_url=pdf_url,
                    published_date=published_date,
                    updated_date=None, # Scopus API might not provide this directly
                    source='scopus',
                    categories=[], # Scopus API might provide subject areas
                    keywords=[], # Scopus API might provide keywords
                    doi=doi
                ))
            except Exception as e:
                print(f"Error parsing Scopus entry: {e}")
        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Scopus API does not provide direct PDF download links.
        This method might need to guide users to the Scopus website or
        integrate with browser automation tools if PDF download is critical.
        """
        raise NotImplementedError("Direct PDF download from Scopus is not supported via this API.")

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Reading paper content directly is not supported as Scopus API
        does not provide full text or direct PDF links.
        """
        raise NotImplementedError("Reading paper content directly from Scopus is not supported.")

if __name__ == "__main__":
    # Test ScopusSearcher functionality
    searcher = ScopusSearcher()

    # Test search functionality
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id}, DOI: {paper.doi})")
            # print(f"   Abstract: {paper.abstract[:100]}...") # Uncomment to see abstracts
    except Exception as e:
        print(f"Error during search: {e}")

    # Test download_pdf (expected to raise NotImplementedError)
    if papers:
        print("\nTesting PDF download functionality (expecting NotImplementedError)...")
        paper_id = papers[0].paper_id
        try:
            searcher.download_pdf(paper_id, "./downloads")
        except NotImplementedError as e:
            print(f"Caught expected error: {e}")
        except Exception as e:
            print(f"Caught unexpected error: {e}")

    # Test read_paper (expected to raise NotImplementedError)
    if papers:
        print("\nTesting paper reading functionality (expecting NotImplementedError)...")
        paper_id = papers[0].paper_id
        try:
            searcher.read_paper(paper_id)
        except NotImplementedError as e:
            print(f"Caught expected error: {e}")
        except Exception as e:
            print(f"Caught unexpected error: {e}")
