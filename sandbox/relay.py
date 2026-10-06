"""Sandbox relay: lets one role's sandbox and GuudFlow reach each other on Docker Desktop.

On Docker Desktop (macOS, Windows) the host cannot bind a Docker network's gateway IP or
reach container IPs, so the direct setup (gateway and MCP server bound on the run network's
gateway IP) does not work. One relay container per role sits on that role's internal network
(alias `guudflow-host`) and on Docker's default bridge, and forwards fixed ports only:

- RELAY_PORTS (the run's LLM gateway and MCP ports): sandbox -> relay -> the host, via
  RELAY_HOST (host.docker.internal);
- RELAY_LISTEN (published on the host's 127.0.0.1): GuudFlow -> relay -> the sandbox's
  opencode server, RELAY_SANDBOX:RELAY_SANDBOX_PORT.

It runs from the agent image as `python3 -c <this file>` with no mounts, a read-only root,
no capabilities and an unprivileged user. Standard library only.
"""

from __future__ import annotations

import asyncio
import os

_CHUNK = 65536


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction until EOF, then half-close the other side."""
    try:
        while True:
            data = await reader.read(_CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


def forward(host: str, port: int):  # -> callback for asyncio.start_server
    """A connection handler that splices each client to host:port."""
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.close()
            return
        await asyncio.wait([asyncio.ensure_future(_pipe(reader, up_writer)),
                            asyncio.ensure_future(_pipe(up_reader, writer))])
        for w in (writer, up_writer):
            w.close()
    return handle


async def main() -> None:
    host = os.environ["RELAY_HOST"]
    ports = [int(p) for p in os.environ.get("RELAY_PORTS", "").split(",") if p]
    servers = [await asyncio.start_server(forward(host, p), "0.0.0.0", p) for p in ports]
    sandbox = os.environ.get("RELAY_SANDBOX")
    if sandbox:
        servers.append(await asyncio.start_server(
            forward(sandbox, int(os.environ["RELAY_SANDBOX_PORT"])),
            "0.0.0.0", int(os.environ["RELAY_LISTEN"])))
    print("relay ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
