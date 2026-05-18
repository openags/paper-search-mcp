# tests/test_mcp_http.py
"""
Tests for MCP JSON-RPC over HTTP transport in server_http.py
"""

import pytest
import json
from starlette.testclient import TestClient

from paper_search_mcp.server_http import (
    app,
    MCPSession,
    MCPSessionManager,
    MCP_TOOLS
)


@pytest.fixture
def client():
    """Create a test client for the Starlette app"""
    return TestClient(app)


class TestMCPSession:
    """Tests for MCPSession dataclass"""
    
    def test_session_creation(self):
        """Test MCPSession is created with correct defaults"""
        session = MCPSession(session_id="test-123")
        
        assert session.session_id == "test-123"
        assert session.created_at > 0
        assert session.last_activity > 0
        assert session.request_counter == 0


class TestMCPSessionManager:
    """Tests for MCPSessionManager class"""
    
    @pytest.fixture
    def manager(self):
        """Create a fresh MCPSessionManager for testing"""
        return MCPSessionManager()
    
    def test_create_session(self, manager):
        """Test session creation"""
        session = manager.create_session()
        
        assert session is not None
        assert session.session_id in manager.sessions
        assert len(session.session_id) == 36  # UUID format
    
    def test_get_session(self, manager):
        """Test getting an existing session"""
        created = manager.create_session()
        
        retrieved = manager.get_session(created.session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == created.session_id
    
    def test_get_nonexistent_session(self, manager):
        """Test getting a session that doesn't exist"""
        session = manager.get_session("nonexistent")
        assert session is None
    
    def test_remove_session(self, manager):
        """Test removing a session"""
        session = manager.create_session()
        session_id = session.session_id
        
        manager.remove_session(session_id)
        
        assert session_id not in manager.sessions
    
    def test_cleanup_stale_sessions(self, manager):
        """Test cleaning up stale sessions"""
        session = manager.create_session()
        # Artificially age the session
        session.last_activity = 0
        
        manager.cleanup_stale_sessions(timeout=1)
        
        assert session.session_id not in manager.sessions


class TestMCPEndpoint:
    """Tests for the /mcp endpoint"""
    
    def test_mcp_endpoint_exists(self, client):
        """Test that MCP endpoint is accessible"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {}
            }
        )
        assert response.status_code == 200
    
    def test_mcp_invalid_json(self, client):
        """Test MCP with invalid JSON"""
        response = client.post(
            "/mcp",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        
        data = response.json()
        assert data["error"]["code"] == -32700
        assert "Parse error" in data["error"]["message"]
    
    def test_mcp_missing_jsonrpc_version(self, client):
        """Test MCP without jsonrpc field"""
        response = client.post(
            "/mcp",
            json={
                "id": 1,
                "method": "test"
            }
        )
        assert response.status_code == 400
        
        data = response.json()
        assert data["error"]["code"] == -32600
    
    def test_mcp_wrong_jsonrpc_version(self, client):
        """Test MCP with wrong jsonrpc version"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "1.0",
                "id": 1,
                "method": "test"
            }
        )
        assert response.status_code == 400


class TestMCPInitialize:
    """Tests for MCP initialize method"""
    
    def test_initialize(self, client):
        """Test MCP initialization"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {}
            }
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert "result" in data
        
        result = data["result"]
        assert "protocolVersion" in result
        assert "capabilities" in result
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "paper_search_server"
        assert "sessionId" in result


class TestMCPToolsList:
    """Tests for MCP tools/list method"""
    
    def test_tools_list(self, client):
        """Test listing available tools"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "result" in data
        assert "tools" in data["result"]
        
        tools = data["result"]["tools"]
        assert isinstance(tools, list)
        assert len(tools) > 0
        
        # Check tool structure
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
    
    def test_tools_list_contains_expected_tools(self, client):
        """Test that tools list contains expected tools"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
        )
        
        data = response.json()
        tool_names = [t["name"] for t in data["result"]["tools"]]
        
        expected_tools = [
            "search_arxiv",
            "search_pubmed",
            "search_semantic",
            "download_arxiv",
            "read_arxiv_paper"
        ]
        
        for expected in expected_tools:
            assert expected in tool_names


class TestMCPToolsCall:
    """Tests for MCP tools/call method"""
    
    def test_tools_call_unknown_tool(self, client):
        """Test calling an unknown tool"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "unknown_tool",
                    "arguments": {}
                }
            }
        )
        assert response.status_code == 404
        
        data = response.json()
        assert data["error"]["code"] == -32601
    
    def test_tools_call_search_structure(self, client):
        """Test calling a search tool returns correct structure"""
        # This test may fail if actual API call fails
        # We're mainly testing the structure
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search_arxiv",
                    "arguments": {
                        "query": "test",
                        "max_results": 1
                    }
                }
            }
        )
        
        # Response should be either success or error with proper structure
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        
        if "result" in data:
            assert "content" in data["result"]
        elif "error" in data:
            assert "code" in data["error"]
            assert "message" in data["error"]


class TestMCPMessagesEndpoint:
    """Tests for the /messages endpoint (alias for /mcp)"""
    
    def test_messages_endpoint_works(self, client):
        """Test that /messages endpoint works same as /mcp"""
        response = client.post(
            "/messages",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {}
            }
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "result" in data
        assert "serverInfo" in data["result"]


class TestMCPUnknownMethod:
    """Tests for unknown MCP methods"""
    
    def test_unknown_method(self, client):
        """Test calling an unknown method"""
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "unknown/method",
                "params": {}
            }
        )
        assert response.status_code == 404
        
        data = response.json()
        assert data["error"]["code"] == -32601


class TestMCPToolsMapping:
    """Tests for MCP_TOOLS mapping"""
    
    def test_all_tools_have_required_fields(self):
        """Test that all tools in MCP_TOOLS have required fields"""
        for tool_name, tool_info in MCP_TOOLS.items():
            assert "searcher" in tool_info, f"{tool_name} missing 'searcher'"
            assert "type" in tool_info, f"{tool_name} missing 'type'"
    
    def test_tool_types_are_valid(self):
        """Test that all tool types are valid"""
        valid_types = {"search", "download", "read", "get_by_doi"}
        
        for tool_name, tool_info in MCP_TOOLS.items():
            assert tool_info["type"] in valid_types, f"{tool_name} has invalid type"
    
    def test_searcher_names_are_valid(self):
        """Test that all searcher names reference valid searchers"""
        valid_searchers = {
            "arxiv", "pubmed", "biorxiv", "medrxiv",
            "google_scholar", "iacr", "semantic", "crossref"
        }
        
        for tool_name, tool_info in MCP_TOOLS.items():
            assert tool_info["searcher"] in valid_searchers, \
                f"{tool_name} references invalid searcher"
