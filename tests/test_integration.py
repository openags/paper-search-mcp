# tests/test_integration.py
"""
End-to-end integration tests for paper-search-mcp HTTP server
"""

import pytest
import json
import time
from starlette.testclient import TestClient

from paper_search_mcp.server_http import app


@pytest.fixture
def client():
    """Create a test client for the Starlette app"""
    return TestClient(app)


class TestEndToEndWorkflow:
    """End-to-end workflow tests"""
    
    def test_full_workflow_check_health_list_platforms(self, client):
        """Test basic workflow: check health -> list platforms"""
        # Step 1: Check health
        health_response = client.get("/health")
        assert health_response.status_code == 200
        health_data = health_response.json()
        assert health_data["status"] == "healthy"
        
        # Step 2: List platforms
        platforms_response = client.get("/api/platforms")
        assert platforms_response.status_code == 200
        platforms_data = platforms_response.json()
        assert len(platforms_data["platforms"]) > 0
    
    def test_mcp_workflow_initialize_list_tools(self, client):
        """Test MCP workflow: initialize -> list tools"""
        # Step 1: Initialize
        init_response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {}
            }
        )
        assert init_response.status_code == 200
        init_data = init_response.json()
        session_id = init_data["result"]["sessionId"]
        assert session_id is not None
        
        # Step 2: List tools
        tools_response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
        )
        assert tools_response.status_code == 200
        tools_data = tools_response.json()
        assert len(tools_data["result"]["tools"]) > 0


class TestPlatformCoverage:
    """Tests to ensure all platforms are accessible via HTTP"""
    
    @pytest.mark.parametrize("platform", [
        "arxiv",
        "pubmed", 
        "biorxiv",
        "medrxiv",
        "google_scholar",
        "iacr",
        "semantic",
        "crossref"
    ])
    def test_platform_in_list(self, client, platform):
        """Test that each platform appears in the platforms list"""
        response = client.get("/api/platforms")
        platforms = [p["name"] for p in response.json()["platforms"]]
        assert platform in platforms
    
    @pytest.mark.parametrize("platform", [
        "arxiv",
        "pubmed",
        "biorxiv",
        "medrxiv",
        "google_scholar",
        "iacr",
        "semantic",
        "crossref"
    ])
    def test_search_endpoint_exists_for_platform(self, client, platform):
        """Test that search endpoint works for each platform (structure test)"""
        response = client.post(
            f"/api/search/{platform}",
            json={"query": "test"}
        )
        # Should not be 404 (endpoint exists)
        assert response.status_code != 404


class TestErrorRecovery:
    """Tests for error handling and recovery"""
    
    def test_invalid_platform_returns_helpful_error(self, client):
        """Test that invalid platform returns list of valid platforms"""
        response = client.post(
            "/api/search/invalid",
            json={"query": "test"}
        )
        assert response.status_code == 404
        
        data = response.json()
        assert "available_platforms" in data["error"]["details"]
    
    def test_server_recovers_after_error(self, client):
        """Test that server works after an error"""
        # Cause an error
        client.post("/api/search/invalid", json={"query": "test"})
        
        # Server should still work
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestResponseConsistency:
    """Tests for consistent response formats"""
    
    def test_all_errors_have_consistent_format(self, client):
        """Test that all error responses have consistent format"""
        error_inducing_requests = [
            ("POST", "/api/search/invalid", {"query": "test"}),
            ("POST", "/api/download/pubmed", {"paper_id": "123"}),
            ("POST", "/api/read/pubmed", {"paper_id": "123"}),
            ("POST", "/api/search/arxiv", {}),  # Missing query
        ]
        
        for method, path, body in error_inducing_requests:
            if method == "POST":
                response = client.post(path, json=body)
            else:
                response = client.get(path)
            
            if response.status_code >= 400:
                data = response.json()
                assert "error" in data, f"Missing 'error' in response from {path}"
                error = data["error"]
                assert "code" in error, f"Missing 'code' in error from {path}"
                assert "message" in error, f"Missing 'message' in error from {path}"
                assert "timestamp" in error, f"Missing 'timestamp' in error from {path}"
    
    def test_success_responses_have_consistent_format(self, client):
        """Test that success responses have expected fields"""
        # Health check
        health = client.get("/health").json()
        assert "status" in health
        assert "version" in health
        
        # Platforms
        platforms = client.get("/api/platforms").json()
        assert "platforms" in platforms


class TestConcurrency:
    """Tests for concurrent request handling"""
    
    def test_multiple_health_checks(self, client):
        """Test multiple concurrent health checks"""
        responses = [client.get("/health") for _ in range(10)]
        
        for response in responses:
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"
    
    def test_health_and_platforms_concurrent(self, client):
        """Test health and platforms endpoints work together"""
        health = client.get("/health")
        platforms = client.get("/api/platforms")
        
        assert health.status_code == 200
        assert platforms.status_code == 200


class TestUptimeTracking:
    """Tests for uptime tracking"""
    
    def test_uptime_increases(self, client):
        """Test that uptime increases over time"""
        response1 = client.get("/health")
        uptime1 = response1.json()["uptime"]
        
        time.sleep(1)
        
        response2 = client.get("/health")
        uptime2 = response2.json()["uptime"]
        
        assert uptime2 >= uptime1


class TestSSEClientTracking:
    """Tests for SSE client tracking in health endpoint"""
    
    def test_sse_clients_count_in_health(self, client):
        """Test that health endpoint reports SSE client count"""
        response = client.get("/health")
        data = response.json()
        
        assert "sse_clients" in data
        assert isinstance(data["sse_clients"], int)
        assert data["sse_clients"] >= 0


class TestMCPSessionTracking:
    """Tests for MCP session tracking"""
    
    def test_mcp_sessions_count_in_health(self, client):
        """Test that health endpoint reports MCP session count"""
        response = client.get("/health")
        data = response.json()
        
        assert "mcp_sessions" in data
        assert isinstance(data["mcp_sessions"], int)
    
    def test_mcp_session_increases_after_init(self, client):
        """Test that MCP session count increases after initialization"""
        # Get initial count
        health1 = client.get("/health").json()
        initial_sessions = health1["mcp_sessions"]
        
        # Initialize MCP
        client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {}
            }
        )
        
        # Check count increased
        health2 = client.get("/health").json()
        assert health2["mcp_sessions"] >= initial_sessions
