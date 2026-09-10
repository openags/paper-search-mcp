from paper_search_mcp.server import ALL_SOURCES

def test_all_sources_are_medical():
    expected = ["pubmed", "pmc", "europepmc", "medrxiv", "biorxiv"]
    assert set(ALL_SOURCES) == set(expected)
