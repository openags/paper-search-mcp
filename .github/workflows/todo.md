TODO - Paper Search MCP HTTP/SSE Integration

📋 Übersicht

Dieses Dokument beschreibt die schrittweise Integration von HTTP/SSE Transport in das paper-search-mcp Repository, basierend auf der Architektur des mcp-paperstream Projekts.
Ziel Erweitern des stdio-basierten MCP Servers um HTTP REST API und Server-Sent Events (SSE) für Integration mit n8n, Webhooks und IoT-Clients.

🏗️ Phase 1: Infrastruktur Setup (Priorität: HOCH)

1.1 Neue Server-Datei erstellen
[ ] paper\search\mcp/server\http.py erstellen (analog zu server\integrated.py aus paperstream)
[ ] FastMCP HTTP Transport konfigurieren mit mcp.http\_app()
[ ] Basis-Struktur mit uvicorn Integration
[ ] Environment Variables für Konfiguration:
  - PAPER\SEARCH\HOST (default: "0.0.0.0")
  - PAPER\SEARCH\PORT (default: 8090)
  - PAPER\SEARCH\DEBUG (default: False)

1.2 Dependencies erweitern
[ ] pyproject.toml aktualisieren:
    dependencies = [
    # ... existing dependencies
    "uvicorn>=0.24.0",
    "starlette>=0.32.0",
    "fastapi>=0.104.0",  # Optional für erweiterte REST Features
  ]
  

1.3 CORS und Middleware
[ ] CORS Middleware für Web-Clients hinzufügen
[ ] Request Logging Middleware
[ ] Error Handling Middleware
[ ] Rate Limiting (optional)
Zeitschätzung: 4-5 Stunden

🔌 Phase 2: HTTP REST Endpoints (Priorität: HOCH)

2.1 Health & System Endpoints
[ ] GET /health - Health Check
    {
    "status": "healthy",
    "version": "0.1.3",
    "uptime": 3600,
    "platforms": ["arxiv", "pubmed", ...]
  }
  

[ ] GET /api/platforms - Verfügbare Suchplattformen
    {
    "platforms": [
      {"name": "arxiv", "description": "arXiv.org preprints", "supports\_download": true},
      {"name": "pubmed", "description": "PubMed database", "supports\_download": false}
    ]
  }
  

2.2 Search Endpoints
[ ] POST /api/search/{platform} - Paper Suche
    // Request
  {
    "query": "machine learning",
    "max\_results": 10,
    "filters": {
      "year\_from": 2020,
      "categories": ["cs.AI"]
    }
  }
  
  // Response
  {
    "results": [...],
    "total\_found": 150,
    "search\time\ms": 1200,
    "platform": "arxiv"
  }
  

[ ] GET /api/search/{platform}?q={query}&max\_results={n} - GET Alternative

2.3 Download Endpoints
[ ] POST /api/download/{platform} - PDF Download
    // Request
  {
    "paper\_id": "2301.12345",
    "format": "pdf",
    "save\_path": "/tmp/papers/"
  }
  
  // Response
  {
    "success": true,
    "file\_path": "/tmp/papers/2301.12345.pdf",
    "file\size\bytes": 1024000,
    "download\time\ms": 5000
  }
  

2.4 Paper Detail Endpoints
[ ] GET /api/paper/{paper\_id} - Paper Details
[ ] GET /api/paper/{paper\_id}/metadata - Nur Metadaten
[ ] GET /api/paper/{paper\_id}/abstract - Nur Abstract

2.5 Error Handling
[ ] Standardisierte Error Responses:
    {
    "error": {
      "code": "PLATFORM\_UNAVAILABLE",
      "message": "arXiv API is currently unavailable",
      "details": {...},
      "timestamp": "2025-01-23T10:30:00Z"
    }
  }
  
Zeitschätzung: 6-8 Stunden

📡 Phase 3: SSE Integration (Priorität: MITTEL)

3.1 SSE Endpoint
[ ] GET /sse - Server-Sent Events Endpoint
[ ] Client Registration System mit UUID
[ ] Async Queue System für SSE Messages
[ ] Connection Management (connect/disconnect events)

3.2 Streaming Features
[ ] Search Result Streaming für große Ergebnismengen:
    // SSE Event
  data: {"type": "search\_result", "paper": {...}, "index": 5, "total": 100}
  

[ ] Download Progress Updates:
    data: {"type": "download\progress", "paper\id": "123", "progress": 0.75, "bytes\_downloaded": 750000}
  

3.3 Heartbeat & Reconnection
[ ] Heartbeat Mechanismus (alle 30s)
[ ] Client Timeout Detection
[ ] Automatic Cleanup von disconnected clients
[ ] Reconnection Logic mit Session Recovery

3.4 Event Types
[ ] connected - Client verbunden
[ ] heartbeat - Keep-alive
[ ] search\_started - Suche gestartet
[ ] search\_result - Einzelnes Suchergebnis
[ ] search\_completed - Suche abgeschlossen
[ ] download\_started - Download gestartet
[ ] download\_progress - Download Fortschritt
[ ] download\_completed - Download abgeschlossen
[ ] error - Fehler aufgetreten
Zeitschätzung: 5-6 Stunden

🔧 Phase 4: MCP HTTP Transport (Priorität: MITTEL)

4.1 MCP über HTTP
[ ] /mcp Endpoint für MCP JSON-RPC über HTTP
[ ] POST /messages für MCP Messages
[ ] Session Management für MCP Clients
[ ] JSON-RPC Request/Response Handling

4.2 Tool Call Routing
[ ] HTTP-basierte Tool Calls für alle existierenden Tools:
  - search\_arxiv
  - search\_pubmed
  - search\_biorxiv
  - search\_medrxiv
  - search\google\scholar
  - search\_iacr
  - search\_semantic
  - search\_crossref
  - download\_arxiv
  - download\_biorxiv
  - etc.

4.3 WebSocket Alternative (Optional)
[ ] WebSocket Endpoint als Alternative zu SSE
[ ] Bidirektionale Kommunikation
[ ] Real-time Tool Call Execution
Zeitschätzung: 4-5 Stunden

📝 Phase 5: Konfiguration & Deployment (Priorität: MITTEL)

5.1 Smithery Integration
[ ] smithery.yaml erweitern um HTTP Transport Option:
    startCommand:
    type: http  # Zusätzlich zu stdio
    configSchema:
      type: object
      properties:
        host:
          type: string
          default: "0.0.0.0"
        port:
          type: integer
          default: 8090
    commandFunction: |
      (config) => ({ 
        command: 'uvicorn', 
        args: ['paper\search\mcp.server\_http:app', '--host', config.host, '--port', config.port.toString()] 
      })
  

5.2 Startup Scripts
[ ] start\_server.sh Script erstellen (analog zu paperstream):
    #!/bin/bash
  # Paper Search MCP HTTP Server
  MODE="${1:-http}"
  PORT="${PAPER\SEARCH\PORT:-8090}"
  HOST="${PAPER\SEARCH\HOST:-0.0.0.0}"
  
  case "$MODE" in
    http)
      exec uvicorn "paper\search\mcp.server\_http:app" --host "$HOST" --port "$PORT"
      ;;
    stdio)
      exec python -m paper\search\mcp.server
      ;;
  esac
  

5.3 Docker Integration
[ ] Dockerfile erweitern um HTTP Port Exposition:
    # Expose HTTP port
  EXPOSE 8090
  
  # Support both modes
  CMD ["python", "-m", "paper\search\mcp.server\_http"]
  

5.4 Claude Desktop HTTP Config
[ ] Beispiel-Konfiguration für HTTP Transport:
    {
    "mcpServers": {
      "paper\search\http": {
        "command": "curl",
        "args": ["-X", "POST", "http://localhost:8090/mcp"],
        "transport": "http"
      }
    }
  }
  
Zeitschätzung: 3-4 Stunden

🧪 Phase 6: Testing & Validation (Priorität: NIEDRIG)

6.1 Unit Tests
[ ] tests/test\http\endpoints.py - REST API Tests
[ ] tests/test\_sse.py - SSE Connection Tests
[ ] tests/test\mcp\http.py - MCP über HTTP Tests
[ ] tests/test\_integration.py - End-to-End Tests

6.2 Performance Tests
[ ] Load Testing für concurrent requests
[ ] Memory Usage bei vielen SSE Connections
[ ] Response Time Benchmarks
[ ] Performance Vergleich stdio vs HTTP

6.3 Integration Tests
[ ] n8n Webhook Integration
[ ] Unity SSE Client Test
[ ] Android REST API Test
[ ] Claude Desktop HTTP Transport Test
Zeitschätzung: 6-8 Stunden

📚 Phase 7: Dokumentation (Priorität: NIEDRIG)

7.1 API Dokumentation
[ ] docs/API.md erstellen (analog zu paperstream):
  - Endpoint Übersicht
  - Request/Response Beispiele
  - Error Codes
  - Rate Limits
  - Authentication (falls implementiert)

7.2 Setup Guides
[ ] HTTP Transport Setup Guide
[ ] Migration Guide von stdio zu HTTP
[ ] Docker Deployment Guide
[ ] n8n Integration Tutorial

7.3 Client Beispiele
[ ] JavaScript SSE Client Beispiel
[ ] Python HTTP Client Beispiel
[ ] Unity C# SSE Client Beispiel
[ ] n8n Workflow Beispiele

7.4 README Updates
[ ] README.md HTTP Setup Dokumentation
[ ] Installation Instructions für HTTP Mode
[ ] Configuration Examples
[ ] Troubleshooting Section
Zeitschätzung: 4-6 Stunden

🚀 Deployment & Rollout

Rollout-Strategie
Alpha: Lokale Entwicklung und Testing
Beta: Docker Container mit HTTP Support
Release: Smithery Update mit HTTP Transport Option
Migration: Bestehende stdio Nutzer können optional auf HTTP wechseln

Backwards Compatibility
[ ] Bestehende stdio Funktionalität bleibt unverändert
[ ] Beide Transport-Modi parallel unterstützen
[ ] Keine Breaking Changes in bestehenden APIs

⏱️ Zeitschätzung Gesamt

| Phase | Priorität | Geschätzte Zeit |
|-------|-----------|----------------|
| 1. Infrastruktur Setup | HOCH | 4-5h |
| 2. HTTP REST Endpoints | HOCH | 6-8h |
| 3. SSE Integration | MITTEL | 5-6h |
| 4. MCP HTTP Transport | MITTEL | 4-5h |
| 5. Konfiguration & Deployment | MITTEL | 3-4h |
| 6. Testing & Validation | NIEDRIG | 6-8h |
| 7. Dokumentation | NIEDRIG | 4-6h |
Gesamtzeit: 32-42 Stunden

🎯 Meilensteine

Milestone 1: Basic HTTP (Woche 1)
✅ server\_http.py erstellt
✅ /health und /api/platforms Endpoints
✅ Basis Search Endpoints
✅ Docker HTTP Support

Milestone 2: SSE & Streaming (Woche 2)
✅ SSE Endpoint implementiert
✅ Search Result Streaming
✅ Download Progress Updates
✅ Client Management

Milestone 3: MCP HTTP (Woche 3)
✅ MCP JSON-RPC über HTTP
✅ Tool Call Routing
✅ Session Management
✅ Integration Tests

Milestone 4: Production Ready (Woche 4)
✅ Vollständige Dokumentation
✅ Performance Tests
✅ Smithery Integration
✅ Release Vorbereitung

🔍 Referenzen

Basis Repository: openags/paper-search-mcp
Vorbild Architektur: nileneb/mcp-paperstream (server\_integrated.py)
FastMCP Dokumentation: FastMCP HTTP Transport
SSE Standard: Server-Sent Events Specification

📝 Notizen

Kompatibilität: Beide Transport-Modi (stdio + HTTP) parallel unterstützen
Performance: HTTP Transport sollte ähnliche Performance wie stdio haben
Security: CORS richtig konfigurieren, optional Authentication
Monitoring: Health Checks und Metrics für Production Deployment
Scaling: Vorbereitung für Load Balancer und horizontale Skalierung