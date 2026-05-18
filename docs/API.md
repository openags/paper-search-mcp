# Paper Search MCP - HTTP/SSE API Documentation

This document describes the HTTP REST API and Server-Sent Events (SSE) interface for the Paper Search MCP server.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Endpoints](#endpoints)
  - [Health & System](#health--system)
  - [Search](#search)
  - [Download](#download)
  - [Read Paper Content](#read-paper-content)
  - [Paper Details](#paper-details)
  - [Server-Sent Events (SSE)](#server-sent-events-sse)
  - [MCP HTTP Transport](#mcp-http-transport)
- [Error Handling](#error-handling)
- [Rate Limits](#rate-limits)
- [Client Examples](#client-examples)

---

## Overview

The Paper Search MCP HTTP server provides:

- **REST API**: Standard HTTP endpoints for searching and downloading papers
- **SSE**: Real-time event streaming for progress updates
- **MCP over HTTP**: JSON-RPC 2.0 interface for MCP protocol compatibility

### Base URL

```
http://localhost:8090
```

### Supported Platforms

| Platform | Search | Download | Read |
|----------|--------|----------|------|
| arxiv | ✅ | ✅ | ✅ |
| pubmed | ✅ | ❌ | ❌ |
| biorxiv | ✅ | ✅ | ✅ |
| medrxiv | ✅ | ✅ | ✅ |
| google_scholar | ✅ | ❌ | ❌ |
| iacr | ✅ | ✅ | ✅ |
| semantic | ✅ | ✅ | ✅ |
| crossref | ✅ | ❌ | ❌ |

---

## Getting Started

### Starting the Server

```bash
# Using start script
./start_server.sh http

# Using uvicorn directly
uvicorn paper_search_mcp.server_http:app --host 0.0.0.0 --port 8090

# Using Docker
docker run -p 8090:8090 paper-search-mcp
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_SEARCH_HOST` | `0.0.0.0` | Host to bind to |
| `PAPER_SEARCH_PORT` | `8090` | Port to bind to |
| `PAPER_SEARCH_DEBUG` | `false` | Enable debug mode |
| `SEMANTIC_SCHOLAR_API_KEY` | - | Optional API key for Semantic Scholar |

---

## Endpoints

### Health & System

#### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "version": "0.1.3",
  "uptime": 3600,
  "uptime_human": "1h 0m 0s",
  "platforms": ["arxiv", "pubmed", "biorxiv", "medrxiv", "google_scholar", "iacr", "semantic", "crossref"],
  "sse_clients": 2,
  "mcp_sessions": 5
}
```

#### GET /api/platforms

List available search platforms.

**Response:**
```json
{
  "platforms": [
    {
      "name": "arxiv",
      "description": "arXiv.org preprints - Open access archive for scholarly articles",
      "supports_download": true,
      "supports_read": true
    },
    {
      "name": "pubmed",
      "description": "PubMed - Biomedical literature database from NCBI",
      "supports_download": false,
      "supports_read": false
    }
  ]
}
```

---

### Search

#### POST /api/search/{platform}

Search papers on a specific platform.

**Path Parameters:**
- `platform` - Platform name (e.g., `arxiv`, `pubmed`, `semantic`)

**Request Body:**
```json
{
  "query": "machine learning",
  "max_results": 10,
  "filters": {
    "year": "2020-2024",
    "fetch_details": true
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "paper_id": "2301.12345",
      "title": "Deep Learning for Natural Language Processing",
      "authors": "John Doe; Jane Smith",
      "abstract": "We present a novel approach...",
      "doi": "10.1234/example.12345",
      "published_date": "2023-01-15T00:00:00",
      "pdf_url": "https://arxiv.org/pdf/2301.12345.pdf",
      "url": "https://arxiv.org/abs/2301.12345",
      "source": "arxiv"
    }
  ],
  "total_found": 150,
  "search_time_ms": 1200,
  "platform": "arxiv",
  "query": "machine learning"
}
```

#### GET /api/search/{platform}

Alternative GET method for search.

**Query Parameters:**
- `q` - Search query (required)
- `max_results` - Maximum results (default: 10)
- `year` - Year filter
- `fetch_details` - Fetch details for IACR (default: true)

**Example:**
```
GET /api/search/arxiv?q=machine+learning&max_results=5
```

---

### Download

#### POST /api/download/{platform}

Download a paper PDF.

**Request Body:**
```json
{
  "paper_id": "2301.12345",
  "save_path": "/tmp/papers/"
}
```

**Response:**
```json
{
  "success": true,
  "file_path": "/tmp/papers/2301.12345.pdf",
  "file_size_bytes": 1024000,
  "download_time_ms": 5000,
  "platform": "arxiv",
  "paper_id": "2301.12345"
}
```

---

### Read Paper Content

#### POST /api/read/{platform}

Extract text content from a paper PDF.

**Request Body:**
```json
{
  "paper_id": "2301.12345",
  "save_path": "/tmp/papers/"
}
```

**Response:**
```json
{
  "success": true,
  "paper_id": "2301.12345",
  "platform": "arxiv",
  "content": "Full text content of the paper...",
  "content_length": 50000
}
```

---

### Paper Details

#### GET /api/paper/{platform}/{paper_id}

Get details for a specific paper.

**Example:**
```
GET /api/paper/crossref/10.1038/nature12373
```

**Response:**
```json
{
  "paper": {
    "paper_id": "10.1038/nature12373",
    "title": "Example Paper Title",
    "authors": "Author One; Author Two",
    "abstract": "Paper abstract...",
    "doi": "10.1038/nature12373",
    "published_date": "2023-01-15T00:00:00",
    "pdf_url": "",
    "url": "https://doi.org/10.1038/nature12373",
    "source": "crossref"
  }
}
```

---

### Server-Sent Events (SSE)

#### GET /sse

Establish an SSE connection for real-time updates.

**Headers:**
```
Accept: text/event-stream
```

**Event Types:**

| Event | Description |
|-------|-------------|
| `connected` | Client connected successfully |
| `heartbeat` | Keep-alive signal (every 30s) |
| `search_started` | Search operation started |
| `search_result` | Individual search result (streaming) |
| `search_completed` | Search operation completed |
| `download_started` | Download started |
| `download_progress` | Download progress update |
| `download_completed` | Download completed |
| `error` | Error occurred |

**Event Format:**
```
data: {"type": "search_completed", "data": {"platform": "arxiv", "query": "ML", "results_count": 10, "search_time_ms": 1500}, "timestamp": "2025-01-23T10:30:00Z"}
```

**JavaScript Example:**
```javascript
const eventSource = new EventSource('http://localhost:8090/sse');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`Event: ${data.type}`, data.data);
};

eventSource.onerror = (error) => {
  console.error('SSE Error:', error);
};
```

---

### MCP HTTP Transport

#### POST /mcp

MCP JSON-RPC 2.0 endpoint.

##### Initialize

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {}
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { "tools": {} },
    "serverInfo": {
      "name": "paper_search_server",
      "version": "0.1.3"
    },
    "sessionId": "uuid-session-id"
  }
}
```

##### List Tools

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

##### Call Tool

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search_arxiv",
    "arguments": {
      "query": "machine learning",
      "max_results": 10
    }
  }
}
```

#### POST /messages

Alternative endpoint for MCP messages (same as `/mcp`).

---

## Error Handling

All errors follow a consistent format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {
      "additional": "context"
    },
    "timestamp": "2025-01-23T10:30:00Z"
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_PLATFORM` | 404 | Platform not found |
| `MISSING_QUERY` | 400 | Search query required |
| `MISSING_PAPER_ID` | 400 | Paper ID required |
| `INVALID_JSON` | 400 | Invalid JSON body |
| `DOWNLOAD_NOT_SUPPORTED` | 400 | Platform doesn't support downloads |
| `READ_NOT_SUPPORTED` | 400 | Platform doesn't support reading |
| `SEARCH_FAILED` | 500 | Search operation failed |
| `DOWNLOAD_FAILED` | 500 | Download operation failed |
| `READ_FAILED` | 500 | Read operation failed |
| `FETCH_FAILED` | 500 | Failed to fetch paper details |
| `PAPER_NOT_FOUND` | 404 | Paper not found |

### MCP Error Codes (JSON-RPC)

| Code | Description |
|------|-------------|
| `-32700` | Parse error |
| `-32600` | Invalid Request |
| `-32601` | Method not found |
| `-32000` | Server error |

---

## Rate Limits

The HTTP server does not implement rate limiting by default. Consider adding a reverse proxy (nginx, Caddy) for production deployments with rate limiting requirements.

Platform-specific rate limits apply:
- **Semantic Scholar**: Enhanced limits with `SEMANTIC_SCHOLAR_API_KEY`
- **Google Scholar**: May implement anti-scraping measures

---

## Client Examples

### Python

```python
import requests

# Search papers
response = requests.post(
    "http://localhost:8090/api/search/arxiv",
    json={"query": "deep learning", "max_results": 5}
)
papers = response.json()["results"]

# Download paper
download = requests.post(
    "http://localhost:8090/api/download/arxiv",
    json={"paper_id": "2301.12345", "save_path": "./papers"}
)
print(download.json()["file_path"])
```

### JavaScript (Node.js)

```javascript
const fetch = require('node-fetch');

// Search papers
const searchResponse = await fetch('http://localhost:8090/api/search/arxiv', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query: 'machine learning', max_results: 5 })
});
const papers = await searchResponse.json();

// SSE connection
const EventSource = require('eventsource');
const es = new EventSource('http://localhost:8090/sse');
es.onmessage = (e) => console.log(JSON.parse(e.data));
```

### cURL

```bash
# Health check
curl http://localhost:8090/health

# Search
curl -X POST http://localhost:8090/api/search/arxiv \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "max_results": 5}'

# MCP initialize
curl -X POST http://localhost:8090/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}'
```

### Unity C# (SSE)

```csharp
using UnityEngine;
using System.Collections;
using UnityEngine.Networking;

public class PaperSearchSSE : MonoBehaviour
{
    IEnumerator ConnectSSE()
    {
        using (UnityWebRequest www = UnityWebRequest.Get("http://localhost:8090/sse"))
        {
            www.SetRequestHeader("Accept", "text/event-stream");
            yield return www.SendWebRequest();
            
            // Handle streaming response
            Debug.Log(www.downloadHandler.text);
        }
    }
}
```

### n8n Webhook Integration

1. Create an HTTP Request node with:
   - Method: `POST`
   - URL: `http://localhost:8090/api/search/arxiv`
   - Body: `{"query": "{{$json.searchTerm}}", "max_results": 10}`

2. Process results in subsequent nodes

---

## CORS Configuration

The server allows all origins by default. For production, configure allowed origins appropriately.

Current configuration:
- `allow_origins`: `["*"]`
- `allow_credentials`: `true`
- `allow_methods`: `["*"]`
- `allow_headers`: `["*"]`
