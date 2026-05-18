# tests/test_sse.py
"""
Tests for Server-Sent Events (SSE) functionality in server_http.py
"""

import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from starlette.testclient import TestClient

from paper_search_mcp.server_http import (
    app,
    SSEClient,
    SSEManager,
    sse_manager
)


@pytest.fixture
def client():
    """Create a test client for the Starlette app"""
    return TestClient(app)


class TestSSEClient:
    """Tests for SSEClient dataclass"""
    
    def test_sse_client_creation(self):
        """Test SSEClient is created with correct defaults"""
        client = SSEClient(client_id="test-123")
        
        assert client.client_id == "test-123"
        assert client.queue is not None
        assert client.connected_at > 0
        assert client.last_heartbeat > 0
    
    def test_sse_client_queue_is_async(self):
        """Test that SSEClient queue is an asyncio.Queue"""
        client = SSEClient(client_id="test-123")
        assert isinstance(client.queue, asyncio.Queue)


class TestSSEManager:
    """Tests for SSEManager class"""
    
    @pytest.fixture
    def manager(self):
        """Create a fresh SSEManager for testing"""
        return SSEManager()
    
    def test_register_client(self, manager):
        """Test client registration"""
        client = manager.register_client()
        
        assert client is not None
        assert client.client_id in manager.clients
        assert len(client.client_id) == 36  # UUID format
    
    def test_unregister_client(self, manager):
        """Test client unregistration"""
        client = manager.register_client()
        client_id = client.client_id
        
        assert client_id in manager.clients
        
        manager.unregister_client(client_id)
        
        assert client_id not in manager.clients
    
    def test_unregister_nonexistent_client(self, manager):
        """Test unregistering a client that doesn't exist"""
        # Should not raise an error
        manager.unregister_client("nonexistent-id")
    
    @pytest.mark.asyncio
    async def test_send_event(self, manager):
        """Test sending event to specific client"""
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "test_event", {"key": "value"})
        
        # Check event was queued
        event = await client.queue.get()
        assert event["type"] == "test_event"
        assert event["data"] == {"key": "value"}
        assert "timestamp" in event
    
    @pytest.mark.asyncio
    async def test_send_event_nonexistent_client(self, manager):
        """Test sending event to nonexistent client"""
        # Should not raise an error
        await manager.send_event("nonexistent", "test", {})
    
    @pytest.mark.asyncio
    async def test_broadcast(self, manager):
        """Test broadcasting event to all clients"""
        client1 = manager.register_client()
        client2 = manager.register_client()
        
        await manager.broadcast("broadcast_test", {"message": "hello"})
        
        # Both clients should receive the event
        event1 = await client1.queue.get()
        event2 = await client2.queue.get()
        
        assert event1["type"] == "broadcast_test"
        assert event2["type"] == "broadcast_test"
        assert event1["data"]["message"] == "hello"
        assert event2["data"]["message"] == "hello"
    
    def test_multiple_clients(self, manager):
        """Test registering multiple clients"""
        clients = [manager.register_client() for _ in range(5)]
        
        assert len(manager.clients) == 5
        
        # All client IDs should be unique
        client_ids = [c.client_id for c in clients]
        assert len(set(client_ids)) == 5


class TestSSEEndpoint:
    """Tests for the /sse endpoint"""
    
    def test_sse_endpoint_exists(self, client):
        """Test that SSE endpoint is accessible"""
        # Note: TestClient doesn't properly support SSE streaming,
        # so we just verify the endpoint exists and returns correct content type
        with client.stream("GET", "/sse") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            # Read first event (connected)
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    assert data["type"] == "connected"
                    assert "client_id" in data["data"]
                    break
    
    def test_sse_headers(self, client):
        """Test SSE response headers"""
        with client.stream("GET", "/sse") as response:
            assert response.headers.get("cache-control") == "no-cache"
            assert response.headers.get("connection") == "keep-alive"


class TestSSEEventTypes:
    """Tests for different SSE event types"""
    
    @pytest.mark.asyncio
    async def test_event_type_connected(self):
        """Test 'connected' event structure"""
        manager = SSEManager()
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "connected", {
            "client_id": client.client_id,
            "message": "Connected"
        })
        
        event = await client.queue.get()
        assert event["type"] == "connected"
        assert "client_id" in event["data"]
    
    @pytest.mark.asyncio
    async def test_event_type_search_started(self):
        """Test 'search_started' event structure"""
        manager = SSEManager()
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "search_started", {
            "platform": "arxiv",
            "query": "machine learning",
            "max_results": 10
        })
        
        event = await client.queue.get()
        assert event["type"] == "search_started"
        assert event["data"]["platform"] == "arxiv"
        assert event["data"]["query"] == "machine learning"
    
    @pytest.mark.asyncio
    async def test_event_type_search_completed(self):
        """Test 'search_completed' event structure"""
        manager = SSEManager()
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "search_completed", {
            "platform": "arxiv",
            "query": "machine learning",
            "results_count": 10,
            "search_time_ms": 1500
        })
        
        event = await client.queue.get()
        assert event["type"] == "search_completed"
        assert event["data"]["results_count"] == 10
    
    @pytest.mark.asyncio
    async def test_event_type_download_progress(self):
        """Test 'download_progress' event structure"""
        manager = SSEManager()
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "download_progress", {
            "paper_id": "2301.12345",
            "progress": 0.75,
            "bytes_downloaded": 750000
        })
        
        event = await client.queue.get()
        assert event["type"] == "download_progress"
        assert event["data"]["progress"] == 0.75
    
    @pytest.mark.asyncio
    async def test_event_type_error(self):
        """Test 'error' event structure"""
        manager = SSEManager()
        client = manager.register_client()
        
        await manager.send_event(client.client_id, "error", {
            "platform": "arxiv",
            "error": "Connection timeout"
        })
        
        event = await client.queue.get()
        assert event["type"] == "error"
        assert "error" in event["data"]


class TestSSEIntegration:
    """Integration tests for SSE with HTTP endpoints"""
    
    @pytest.mark.asyncio
    async def test_search_triggers_sse_events(self):
        """Test that search operations trigger SSE events"""
        # This test would require a more complex setup with
        # actual search execution and SSE monitoring
        pass
    
    @pytest.mark.asyncio
    async def test_download_triggers_sse_events(self):
        """Test that download operations trigger SSE events"""
        # This test would require a more complex setup with
        # actual download execution and SSE monitoring
        pass
