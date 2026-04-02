# paper_search_mcp/paper.py
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional

@dataclass
class Paper:
    """Standardized paper format with core fields for academic sources"""
    # 核心字段（必填，但允许空值或默认值）
    paper_id: str              # Unique identifier (e.g., arXiv ID, PMID, DOI)
    title: str                 # Paper title
    authors: List[str]         # List of author names
    abstract: str              # Abstract text
    doi: str                   # Digital Object Identifier
    published_date: Optional[datetime]   # Publication date
    pdf_url: str               # Direct PDF link
    url: str                   # URL to paper page
    source: str                # Source platform (e.g., 'arxiv', 'pubmed')

    # 可选字段
    updated_date: Optional[datetime] = None        # Last updated date
    categories: Optional[List[str]] = None         # Subject categories
    keywords: Optional[List[str]] = None           # Keywords
    citations: int = 0                             # Citation count
    references: Optional[List[str]] = None         # List of reference IDs/DOIs
    extra: Optional[Dict] = None                   # Source-specific extra metadata

    def __post_init__(self):
        """Post-initialization to handle default values"""
        if self.authors is None:
            self.authors = []
        if self.categories is None:
            self.categories = []
        if self.keywords is None:
            self.keywords = []
        if self.references is None:
            self.references = []
        if self.extra is None:
            self.extra = {}

    def to_dict(self) -> Dict:
        """Convert paper to dictionary format for serialization"""
        return {
            'paper_id': self.paper_id,
            'title': self.title,
            'authors': '; '.join(self.authors) if self.authors else '',
            'abstract': self.abstract,
            'doi': self.doi,
            'published_date': self.published_date.isoformat() if self.published_date else '',
            'pdf_url': self.pdf_url,
            'url': self.url,
            'source': self.source,
            'updated_date': self.updated_date.isoformat() if self.updated_date else '',
            'categories': '; '.join(self.categories) if self.categories else '',
            'keywords': '; '.join(self.keywords) if self.keywords else '',
            'citations': self.citations,
            'references': '; '.join(self.references) if self.references else '',
            'extra': str(self.extra) if self.extra else ''
        }