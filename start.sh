#!/bin/bash
set -e

docker build -t paper-search-mcp .
docker run -p 8089:8089 --env-file .env paper-search-mcp