FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

WORKDIR /app

COPY remote_mcp_server/pyproject.toml remote_mcp_server/uv.lock ./
RUN uv sync --frozen --no-dev

COPY remote_mcp_server /app/remote_mcp_server
COPY simdorei_mcp_common /app/simdorei_mcp_common

EXPOSE 8030

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "remote_mcp_server.simdorei_mcp.main:app", "--host", "0.0.0.0", "--port", "8030", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
