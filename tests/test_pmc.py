import unittest
from unittest.mock import patch, MagicMock
import os
import requests # Added import
from datetime import datetime

from paper_search_mcp.academic_platforms.pmc import PMCSearcher
from paper_search_mcp.paper import Paper

class TestPMCSearcher(unittest.TestCase):

    def setUp(self):
        self.searcher = PMCSearcher()
        self.test_downloads_dir = "test_downloads_pmc"
        os.makedirs(self.test_downloads_dir, exist_ok=True)

    def tearDown(self):
        # Clean up created files and directory
        if os.path.exists(self.test_downloads_dir):
            for f in os.listdir(self.test_downloads_dir):
                os.remove(os.path.join(self.test_downloads_dir, f))
            os.rmdir(self.test_downloads_dir)

    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_search_success(self, mock_get):
        # Mock ESearch response
        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.content = b"""
        <eSearchResult>
            <IdList>
                <Id>PMC123</Id>
                <Id>PMC456</Id>
            </IdList>
        </eSearchResult>
        """

        # Mock EFetch response
        mock_efetch_response = MagicMock()
        mock_efetch_response.status_code = 200
        mock_efetch_response.content = b"""
        <pmc-articleset>
            <article>
                <front>
                    <article-meta>
                        <article-id pub-id-type="pmc">PMC123</article-id>
                        <article-id pub-id-type="doi">10.1000/xyz123</article-id>
                        <title-group>
                            <article-title>Test Paper Title 1</article-title>
                        </title-group>
                        <contrib-group>
                            <contrib contrib-type="author">
                                <name>
                                    <surname>Author</surname>
                                    <given-names>First</given-names>
                                </name>
                            </contrib>
                        </contrib-group>
                        <pub-date pub-type="epub">
                            <year>2023</year>
                            <month>01</month>
                            <day>15</day>
                        </pub-date>
                        <abstract><p>This is abstract 1.</p></abstract>
                    </article-meta>
                </front>
            </article>
            <article>
                <front>
                    <article-meta>
                        <article-id pub-id-type="pmc">PMC456</article-id>
                        <title-group>
                            <article-title>Test Paper Title 2</article-title>
                        </title-group>
                        <contrib-group>
                             <contrib contrib-type="author">
                                <name>
                                    <surname>Tester</surname>
                                    <given-names>Another</given-names>
                                </name>
                            </contrib>
                        </contrib-group>
                        <pub-date pub-type="ppub">
                            <year>2022</year>
                            <month>12</month>
                        </pub-date>
                        <abstract><sec><title>BACKGROUND</title><p>Background info.</p></sec><sec><title>RESULTS</title><p>Results here.</p></sec></abstract>
                    </article-meta>
                </front>
            </article>
        </pmc-articleset>
        """
        mock_get.side_effect = [mock_esearch_response, mock_efetch_response]

        papers = self.searcher.search("test query", max_results=2)

        self.assertEqual(len(papers), 2)

        paper1 = papers[0]
        self.assertEqual(paper1.paper_id, "PMC123")
        self.assertEqual(paper1.title, "Test Paper Title 1")
        self.assertEqual(paper1.authors, ["First Author"])
        self.assertEqual(paper1.abstract, "This is abstract 1.")
        self.assertEqual(paper1.doi, "10.1000/xyz123")
        self.assertEqual(paper1.url, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMCPMC123/")
        self.assertEqual(paper1.pdf_url, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMCPMC123/pdf")
        self.assertEqual(paper1.published_date, datetime(2023, 1, 15))

        paper2 = papers[1]
        self.assertEqual(paper2.paper_id, "PMC456")
        self.assertEqual(paper2.title, "Test Paper Title 2")
        self.assertEqual(paper2.abstract, "Background info.\nResults here.") # Check structured abstract
        self.assertEqual(paper2.published_date, datetime(2022, 12, 1)) # Month only, day defaults to 1

    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_search_empty_results(self, mock_get):
        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.content = b"<eSearchResult><IdList></IdList></eSearchResult>"
        mock_get.return_value = mock_esearch_response

        papers = self.searcher.search("nonexistent query")
        self.assertEqual(len(papers), 0)

    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_search_request_exception_esearch(self, mock_get):
        mock_get.side_effect = requests.RequestException("ESearch failed")
        papers = self.searcher.search("test query")
        self.assertEqual(len(papers), 0)
        # You could also check logs or print statements if your actual code logs errors

    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_search_request_exception_efetch(self, mock_get):
        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.content = b"<eSearchResult><IdList><Id>PMC123</Id></IdList></eSearchResult>"

        mock_get.side_effect = [mock_esearch_response, requests.RequestException("EFetch failed")]
        papers = self.searcher.search("test query")
        self.assertEqual(len(papers), 0)

    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_download_pdf_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda chunk_size: [b"fake ", b"pdf ", b"content"]
        mock_get.return_value = mock_response

        pmcid = "PMC789"
        expected_path = os.path.join(self.test_downloads_dir, "PMC789.pdf")

        actual_path = self.searcher.download_pdf(pmcid, save_path=self.test_downloads_dir)
        self.assertEqual(actual_path, expected_path)
        self.assertTrue(os.path.exists(expected_path))
        with open(expected_path, 'rb') as f:
            content = f.read()
            self.assertEqual(content, b"fake pdf content")

        # Test with pmcid not starting with PMC
        actual_path_no_prefix = self.searcher.download_pdf("12345", save_path=self.test_downloads_dir)
        expected_path_no_prefix = os.path.join(self.test_downloads_dir, "PMC12345.pdf")
        self.assertEqual(actual_path_no_prefix, expected_path_no_prefix)


    @patch('paper_search_mcp.academic_platforms.pmc.requests.get')
    def test_download_pdf_connection_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("Download failed")
        with self.assertRaises(ConnectionError):
            self.searcher.download_pdf("PMC123", save_path=self.test_downloads_dir)

    @patch('paper_search_mcp.academic_platforms.pmc.PMCSearcher.download_pdf')
    @patch('paper_search_mcp.academic_platforms.pmc.PyPDF2.PdfReader')
    def test_read_paper_success(self, mock_pdf_reader, mock_download_pdf):
        pmcid = "PMC123"
        pdf_path = os.path.join(self.test_downloads_dir, f"{pmcid}.pdf")
        mock_download_pdf.return_value = pdf_path

        mock_reader_instance = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 text. "
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 text."
        mock_reader_instance.pages = [mock_page1, mock_page2]
        mock_pdf_reader.return_value = mock_reader_instance

        # Create a dummy PDF file for the test, as PyPDF2 needs a real file path
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "w") as f: # Create empty file, content doesn't matter due to mocking
            f.write("dummy")

        text = self.searcher.read_paper(pmcid, save_path=self.test_downloads_dir)
        self.assertEqual(text, "Page 1 text. Page 2 text.")
        mock_download_pdf.assert_called_once_with(pmcid, self.test_downloads_dir)

    @patch('paper_search_mcp.academic_platforms.pmc.PMCSearcher.download_pdf')
    def test_read_paper_download_fails(self, mock_download_pdf):
        mock_download_pdf.side_effect = ConnectionError("Failed to download")

        with self.assertRaises(ConnectionError): # Exception should propagate
             self.searcher.read_paper("PMC123", save_path=self.test_downloads_dir)


    @patch('paper_search_mcp.academic_platforms.pmc.PMCSearcher.download_pdf')
    @patch('paper_search_mcp.academic_platforms.pmc.PyPDF2.PdfReader')
    def test_read_paper_pdf_read_fails(self, mock_pdf_reader, mock_download_pdf):
        pmcid = "PMC123"
        pdf_path = os.path.join(self.test_downloads_dir, f"{pmcid}.pdf")
        mock_download_pdf.return_value = pdf_path

        mock_pdf_reader.side_effect = Exception("PDF parsing error")

        # Create a dummy PDF file
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "w") as f:
            f.write("dummy")

        text = self.searcher.read_paper(pmcid, save_path=self.test_downloads_dir)
        self.assertEqual(text, "") # Should return empty string on PDF read error

if __name__ == '__main__':
    unittest.main()
