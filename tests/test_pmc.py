import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from paper_search_mcp.academic_platforms.pmc import PMCSearcher

BUCKET = PMCSearcher.OA_BUCKET_URL


def _response(content=b"", status_code=200):
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.content = content
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    else:
        response.raise_for_status.return_value = None
    return response


def _listing(keys, next_token=""):
    ns = "http://s3.amazonaws.com/doc/2006-03-01/"
    contents = "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
    truncated = "true" if next_token else "false"
    token = f"<NextContinuationToken>{next_token}</NextContinuationToken>" if next_token else ""
    return _response(
        f'<ListBucketResult xmlns="{ns}">{contents}<IsTruncated>{truncated}</IsTruncated>{token}</ListBucketResult>'.encode()
    )


class _Bucket:
    """A session.get that answers listings by prefix and token, and files by URL."""

    def __init__(self, pages, files):
        self.pages = pages
        self.files = files
        self.urls = []

    def __call__(self, url, params=None, timeout=None):
        self.urls.append(url)
        if params and "list-type" in params:
            return self.pages[(params["prefix"], params.get("continuation-token", ""))]
        return self.files.get(url, _response(b"<Error><Code>NoSuchKey</Code></Error>", 404))


class TestPMCOpenAccess(unittest.TestCase):
    def setUp(self):
        self.searcher = PMCSearcher()
        self.save_path = tempfile.mkdtemp()

    def test_read_paper_returns_the_newest_versions_text(self):
        # The newest version sits in the middle, and sorts before .2 as a string
        keys = ["PMC1.1/PMC1.1.txt", "PMC1.10/PMC1.10.txt", "PMC1.2/PMC1.2.txt", "PMC1.2/fig1.jpg"]
        bucket = _Bucket(
            {("PMC1.", ""): _listing(keys)},
            {f"{BUCKET}/PMC1.10/PMC1.10.txt": _response(b"the whole article")},
        )
        self.searcher.session.get = Mock(side_effect=bucket)

        text = self.searcher.read_paper("PMC1", self.save_path)

        self.assertEqual(text, "the whole article")
        self.assertFalse(any("/pmc/articles/" in url for url in bucket.urls))

    def test_a_listing_is_read_past_its_first_page(self):
        bucket = _Bucket(
            {
                ("PMC2.", ""): _listing(["PMC2.1/fig.jpg"], next_token="t/1"),
                ("PMC2.", "t/1"): _listing(["PMC2.1/PMC2.1.txt"]),
            },
            {f"{BUCKET}/PMC2.1/PMC2.1.txt": _response(b"text")},
        )
        self.searcher.session.get = Mock(side_effect=bucket)

        self.assertEqual(self.searcher.read_paper("2", self.save_path), "text")

    def test_download_pdf_saves_the_newest_pdf(self):
        keys = ["PMC3.1/PMC3.1.pdf", "PMC3.2/PMC3.2.pdf", "PMC3.2/supplement.pdf"]
        bucket = _Bucket(
            {("PMC3.", ""): _listing(keys)},
            {f"{BUCKET}/PMC3.2/PMC3.2.pdf": _response(b"%PDF-1.7 version two")},
        )
        self.searcher.session.get = Mock(side_effect=bucket)

        path = self.searcher.download_pdf("PMC3", self.save_path)

        self.assertEqual(Path(path).read_bytes(), b"%PDF-1.7 version two")
        self.assertFalse(any("/pmc/articles/" in url for url in bucket.urls))

    def test_download_pdf_rejects_a_body_that_is_not_a_pdf(self):
        bucket = _Bucket(
            {("PMC4.", ""): _listing(["PMC4.1/PMC4.1.pdf"])},
            {f"{BUCKET}/PMC4.1/PMC4.1.pdf": _response(b"<html>a bot check</html>")},
        )
        self.searcher.session.get = Mock(side_effect=bucket)

        with self.assertRaises(Exception):
            self.searcher.download_pdf("PMC4", self.save_path)
        self.assertEqual(list(Path(self.save_path).iterdir()), [])

    def test_download_pdf_without_an_open_access_copy_raises(self):
        bucket = _Bucket({("PMC5.", ""): _listing([])}, {})
        self.searcher.session.get = Mock(side_effect=bucket)

        with self.assertRaisesRegex(Exception, "open access"):
            self.searcher.download_pdf("PMC5", self.save_path)

    def test_read_paper_falls_back_to_the_pdf_when_there_is_no_text(self):
        # The PDF and the XML are served, so only the extension decides what is text
        bucket = _Bucket(
            {("PMC6.", ""): _listing(["PMC6.1/PMC6.1.pdf", "PMC6.1/PMC6.1.xml"])},
            {
                f"{BUCKET}/PMC6.1/PMC6.1.pdf": _response(b"%PDF-1.7 bytes"),
                f"{BUCKET}/PMC6.1/PMC6.1.xml": _response(b"<article/>"),
            },
        )
        self._assert_pdf_fallback(bucket)

    def test_read_paper_falls_back_to_the_pdf_when_the_text_is_empty(self):
        bucket = _Bucket(
            {("PMC6.", ""): _listing(["PMC6.1/PMC6.1.txt", "PMC6.1/PMC6.1.pdf"])},
            {f"{BUCKET}/PMC6.1/PMC6.1.txt": _response(b" \n")},
        )
        self._assert_pdf_fallback(bucket)

    def _assert_pdf_fallback(self, bucket):
        self.searcher.session.get = Mock(side_effect=bucket)
        page = Mock()
        page.extract_text.return_value = "extracted " * 20
        pdf = Path(self.save_path) / "PMC6.pdf"
        pdf.write_bytes(b"%PDF-1.7")

        with (
            patch.object(self.searcher, "download_pdf", return_value=str(pdf)) as download,
            patch("paper_search_mcp.academic_platforms.pmc.PdfReader", return_value=Mock(pages=[page])),
        ):
            text = self.searcher.read_paper("PMC6", self.save_path)

        download.assert_called_once_with("PMC6", self.save_path)
        self.assertIn("extracted", text)


if __name__ == "__main__":
    unittest.main()
