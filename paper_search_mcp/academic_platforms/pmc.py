# paper_search_mcp/academic_platforms/pmc.py
from typing import List, Optional
import requests
from xml.etree import ElementTree as ET
from datetime import datetime
from ..paper import Paper
import os
from .pubmed import PaperSource # Reusing PaperSource
import PyPDF2
import io

class PMCSearcher(PaperSource):
    """Searcher for PubMed Central (PMC) papers."""
    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    PMC_PDF_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf"

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """Search PMC for papers."""
        search_params = {
            'db': 'pmc',
            'term': query,
            'retmax': max_results,
            'retmode': 'xml'
        }
        try:
            search_response = requests.get(self.ESEARCH_URL, params=search_params)
            search_response.raise_for_status()
            search_root = ET.fromstring(search_response.content)
        except requests.RequestException as e:
            print(f"Error during PMC esearch request: {e}")
            return []
        except ET.ParseError as e:
            print(f"Error parsing PMC esearch XML response: {e}")
            return []

        ids = [id_node.text for id_node in search_root.findall('.//Id') if id_node.text]
        if not ids:
            return []

        fetch_params = {
            'db': 'pmc',
            'id': ','.join(ids),
            'retmode': 'xml'
        }
        try:
            fetch_response = requests.get(self.EFETCH_URL, params=fetch_params)
            fetch_response.raise_for_status()
            fetch_root = ET.fromstring(fetch_response.content)
        except requests.RequestException as e:
            print(f"Error during PMC efetch request: {e}")
            return []
        except ET.ParseError as e:
            print(f"Error parsing PMC efetch XML response: {e}")
            return []

        papers = []
        for article_node in fetch_root.findall('.//article'): # PMC uses <article>
            try:
                # Extract PMCID
                pmcid_node = article_node.find(".//article-id[@pub-id-type='pmc']")
                pmcid = pmcid_node.text if pmcid_node is not None else None
                if not pmcid:
                    continue # Skip if no PMCID

                # Extract title
                title_node = article_node.find(".//article-title")
                title = title_node.text if title_node is not None else "N/A"

                # Extract authors
                authors = []
                for contrib_node in article_node.findall(".//contrib[@contrib-type='author']"):
                    surname_node = contrib_node.find(".//name/surname")
                    given_names_node = contrib_node.find(".//name/given-names")
                    surname = surname_node.text if surname_node is not None else ""
                    given_names = given_names_node.text if given_names_node is not None else ""
                    authors.append(f"{given_names} {surname}".strip())

                # Extract abstract
                abstract_text = "N/A"
                abstract_element = article_node.find("./front/article-meta/abstract")

                if abstract_element is not None:
                    # Check for structured abstract (sections)
                    sections = abstract_element.findall("./sec")
                    if sections: # If <sec> tags are present, parse them
                        abstract_parts = []
                        for sec_node in sections:
                            # Get all <p> text within this <sec>
                            sec_content_parts = [p_node.text.strip() for p_node in sec_node.findall(".//p") if p_node.text and p_node.text.strip()]
                            if sec_content_parts:
                                abstract_parts.append(" ".join(sec_content_parts))
                        if abstract_parts:
                            abstract_text = "\n".join(abstract_parts)
                    else:
                        # Try to find a single <p> directly under <abstract>
                        p_nodes = abstract_element.findall("./p")
                        if p_nodes:
                            abstract_text_parts = [p.text.strip() for p in p_nodes if p.text and p.text.strip()]
                            if abstract_text_parts:
                                abstract_text = "\n".join(abstract_text_parts)
                        # If no <p> directly under abstract, but abstract_element itself has text (less common)
                        elif abstract_element.text and abstract_element.text.strip():
                            abstract_text = abstract_element.text.strip()

                abstract = abstract_text # Assign to the variable used later


                # Extract publication date
                pub_date_node = article_node.find(".//pub-date[@pub-type='epub']") # Prefer electronic pub date
                if pub_date_node is None: # Fallback to print or other pub-types if epub not found
                    pub_date_node = article_node.find(".//pub-date[@pub-type='ppub']")
                if pub_date_node is None:
                     pub_date_node = article_node.find(".//pub-date") # Generic fallback

                year, month, day = "N/A", "N/A", "N/A"
                if pub_date_node is not None:
                    year_node = pub_date_node.find("./year") # Use relative path
                    month_node = pub_date_node.find("./month")
                    day_node = pub_date_node.find("./day")
                    year = year_node.text if year_node is not None and year_node.text else "N/A"
                    month = month_node.text if month_node is not None and month_node.text else "01" # Default month/day
                    day = day_node.text if day_node is not None and day_node.text else "01"

                try:
                    # Handle cases where year might be invalid
                    if year == "N/A" or not year.isdigit():
                        year_int = 1900 # Default year
                    else:
                        year_int = int(year)

                    if month == "N/A" or not month.isdigit() or not (1 <= int(month) <= 12):
                        month_int = 1
                    else:
                        month_int = int(month)

                    if day == "N/A" or not day.isdigit() or not (1 <= int(day) <= 31):
                        day_int = 1
                    else:
                        day_int = int(day)

                    published = datetime(year_int, month_int, day_int)
                except ValueError:
                    published = datetime(1900, 1, 1) # Default for parsing errors

                # Extract DOI
                doi_node = article_node.find(".//article-id[@pub-id-type='doi']")
                doi = doi_node.text if doi_node is not None and doi_node.text else ""

                papers.append(Paper(
                    paper_id=pmcid, # Use PMCID as the primary ID
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/", # PMC uses PMCID in URL
                    pdf_url=self.PMC_PDF_URL.format(pmcid=f"PMC{pmcid}"),
                    published_date=published,
                    updated_date=published, # Assuming same as published for now
                    source='pmc',
                    categories=[], # PMC API doesn't easily provide categories
                    keywords=[],   # PMC API doesn't easily provide keywords
                    doi=doi
                ))
            except Exception as e:
                print(f"Error parsing PMC article XML (PMCID: {pmcid if 'pmcid' in locals() else 'unknown'}): {e}")
        return papers

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download the PDF for a PMC paper."""
        if not paper_id.startswith("PMC"):
            paper_id = f"PMC{paper_id}"

        pdf_url = self.PMC_PDF_URL.format(pmcid=paper_id)

        os.makedirs(save_path, exist_ok=True)
        file_path = os.path.join(save_path, f"{paper_id}.pdf")

        try:
            response = requests.get(pdf_url, stream=True)
            response.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return file_path
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to download PDF from {pdf_url}: {e}")

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download and read text from a PMC paper's PDF."""
        pdf_path = self.download_pdf(paper_id, save_path)

        try:
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page_num in range(len(reader.pages)):
                    text += reader.pages[page_num].extract_text() or ""
            return text
        except Exception as e:
            print(f"Error reading PDF {pdf_path}: {e}")
            return "" # Return empty string on error

if __name__ == "__main__":
    searcher = PMCSearcher()

    print("Testing PMC search functionality...")
    query = "crispr gene editing"
    max_results = 3
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. ID: {paper.paper_id} - {paper.title}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   DOI: {paper.doi}")
            print(f"   URL: {paper.url}")
            print(f"   PDF URL: {paper.pdf_url}\n")

        if papers:
            # Test PDF download and read
            test_paper = papers[0]
            print(f"\nTesting PDF download and read for PMCID: {test_paper.paper_id}")
            try:
                pdf_file_path = searcher.download_pdf(test_paper.paper_id)
                print(f"PDF downloaded to: {pdf_file_path}")

                # Check if file exists and is not empty
                if os.path.exists(pdf_file_path) and os.path.getsize(pdf_file_path) > 0:
                    print("PDF file seems valid.")
                    paper_text = searcher.read_paper(test_paper.paper_id)
                    if paper_text:
                         print(f"Successfully read paper. First 500 chars:\n{paper_text[:500]}...")
                    else:
                        print("Could not extract text from PDF, or PDF was empty.")
                else:
                    print(f"PDF file at {pdf_file_path} is missing or empty.")

            except ConnectionError as e:
                print(f"Connection error during PDF download/read test: {e}")
            except Exception as e:
                print(f"Error during PDF download/read test: {e}")
        else:
            print("No papers found to test download/read functionality.")

    except Exception as e:
        print(f"Error during PMC search test: {e}")
