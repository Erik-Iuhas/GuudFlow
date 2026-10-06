"""Escape probe: runs INSIDE an agent sandbox (O10, plan §5).

The escape suite has a scripted agent write this file into its worktree and run
it with python3, the way a hostile or confused agent would. Every probe records
what happened; the test then asserts on the host that each attempt was
contained. Standard library only (the agent image has python3 and nothing else).

Usage: python3 escape_probe.py <host_port> <out_file>
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


def attempt_write(path: str) -> str:
    """Try to create `path`; return 'written' or the error name."""
    try:
        with open(path, "w") as f:
            f.write("pwned\n")
        return "written"
    except OSError as exc:
        return type(exc).__name__ + ":" + str(exc.errno)


def connect(host: str, port: int, timeout: float = 2.0) -> str:
    """'open' if a TCP connection succeeds, else the error name."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except OSError as exc:
        return type(exc).__name__


def http(method: str, url: str, headers: dict[str, str] | None = None,
         body: dict[str, Any] | None = None, timeout: float = 5.0) -> int | str:
    """Status code of an HTTP call, or the error name when it never got one."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, urllib.error.URLError) as exc:
        return type(exc).__name__


def mcp_call(url: str, token: str | None, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """One MCP tools/call over streamable HTTP; returns status and a body snippet."""
    headers = {"content-type": "application/json",
               "accept": "application/json, text/event-stream"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "body": resp.read(4000).decode(errors="replace")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read(500).decode(errors="replace")}
    except (OSError, urllib.error.URLError) as exc:
        return {"status": type(exc).__name__, "body": ""}


def own_ip() -> str:
    """This container's address on the run network."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


def scan_peers(me: str, port: int) -> list[str]:
    """Every other address in this /24 that accepts a connection on `port`."""
    prefix = me.rsplit(".", 1)[0]
    targets = [f"{prefix}.{i}" for i in range(2, 40) if f"{prefix}.{i}" != me]
    with concurrent.futures.ThreadPoolExecutor(32) as pool:
        results = list(pool.map(lambda h: (h, connect(h, port, 1.5)), targets))
    return [h for h, r in results if r == "open"]


def status_field(name: str) -> str:
    """A field from /proc/self/status."""
    for line in open("/proc/self/status"):
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    return ""


NEEDLE = ("UPSTREAM-" + "SECRET").encode()  # built at runtime: this file must not match


def mcp_list(url: str, token: str) -> list[str]:
    """Tool names tools/list returns for `token`."""
    req = urllib.request.Request(url, method="POST", data=json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode(), headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return sorted(t["name"] for t in json.load(resp)["result"]["tools"])
    except (OSError, ValueError, KeyError) as exc:
        return [type(exc).__name__]


def main() -> None:
    host_port = int(sys.argv[1])
    out_file = sys.argv[2]
    config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
    provider = next(iter(config.get("provider", {}).values()), {})
    options = provider.get("options", {})
    gateway_url = options.get("baseURL", "").rstrip("/")
    virtual_key = options.get("apiKey", "")
    mcp = config.get("mcp", {}).get("guudflow", {})
    mcp_url = mcp.get("url", "")
    mcp_token = mcp.get("headers", {}).get("Authorization", "").removeprefix("Bearer ")
    host_ip = urlparse(gateway_url).hostname or ""
    gw_root = gateway_url.rsplit("/", 1)[0]
    password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    me = own_ip()
    r: dict[str, Any] = {"own_ip": me, "host_ip": host_ip}

    # Identity and kernel hardening.
    r["uid"] = os.getuid()
    r["cap_eff"] = status_field("CapEff")
    r["no_new_privs"] = status_field("NoNewPrivs")
    r["seccomp"] = status_field("Seccomp")
    r["docker_sock"] = os.path.exists("/var/run/docker.sock")

    # Filesystem: read-only root, read-only inputs and tool dir.
    r["write_root"] = attempt_write("/pwned")
    r["write_etc"] = attempt_write("/etc/pwned")
    r["write_usr_bin"] = attempt_write("/usr/local/bin/opencode-pwned")
    r["write_inputs"] = attempt_write("/inputs/pwned.md")
    r["write_tool_dir"] = attempt_write("/work/.guudflow/pwned")
    r["redirect_root"] = os.system("echo x > /pwned-redirect 2>/dev/null") != 0
    r["inputs_visible"] = os.path.exists("/inputs/spec.md")
    mounts = open("/proc/mounts").read()
    r["mount_points"] = sorted({line.split()[1] for line in mounts.splitlines()
                                if line.split()[1].startswith(("/work", "/inputs", "/home", "/tmp"))})

    # The upstream provider key must not be anywhere the agent can read.
    leak = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if NEEDLE in open(f"/proc/{pid}/environ", "rb").read():
                    leak.append(f"/proc/{pid}/environ")
            except OSError:
                pass
    for top in ("/work", "/tmp", "/home", "/etc", "/inputs", "/root"):
        for dirpath, _dirs, files in os.walk(top):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    if NEEDLE in open(path, "rb").read(1_000_000):
                        leak.append(path)
                except OSError:
                    pass
    r["secret_leaks"] = leak

    # Network: no internet, no DNS, no metadata service.
    r["internet_ip"] = connect("1.1.1.1", 443)
    r["internet_ip_80"] = connect("8.8.8.8", 53)
    r["metadata"] = connect("169.254.169.254", 80)
    try:
        socket.getaddrinfo("example.com", 443)
        r["dns"] = "resolved"
    except OSError as exc:
        r["dns"] = type(exc).__name__

    # Host: only the gateway and MCP ports; never anything else that listens.
    gw_port = urlparse(gateway_url).port
    mcp_port = urlparse(mcp_url).port
    r["host_gateway_port"] = connect(host_ip, gw_port or 0)
    r["host_mcp_port"] = connect(host_ip, mcp_port or 0)
    r["host_other_port"] = connect(host_ip, host_port)
    r["host_ssh"] = connect(host_ip, 22, 1.5)

    # Other sandboxes: no peer's opencode server is reachable.
    r["peers_4096"] = scan_peers(me, 4096)

    # Gateway: only inference, only the granted model, only with the virtual key.
    auth = {"authorization": f"Bearer {virtual_key}"}
    chat = {"model": "some-other-model", "messages": [{"role": "user", "content": "hi"}]}
    r["gw_other_model"] = http("POST", f"{gateway_url}/chat/completions", auth, chat)
    r["gw_models_list"] = http("GET", f"{gateway_url}/models", auth)
    r["gw_other_endpoint"] = http("POST", f"{gateway_url}/embeddings", auth,
                                  {"model": "fake-model", "input": "x"})
    r["gw_no_key"] = http("POST", f"{gateway_url}/chat/completions", None,
                          {**chat, "model": "fake-model"})
    r["gw_other_provider"] = http("POST", f"{gw_root}/anthropic/messages", auth,
                                  {"model": "claude-opus-5-5", "max_tokens": 5,
                                   "messages": [{"role": "user", "content": "hi"}]})

    # MCP: no token, forged token, and a tool this role was never granted.
    r["mcp_no_token"] = mcp_call(mcp_url, None, "read_board", {})
    r["mcp_forged_token"] = mcp_call(mcp_url, "forged-" + mcp_token[:8], "read_board", {})
    r["mcp_web_search"] = mcp_call(mcp_url, mcp_token, "web_search", {"query": "x"})
    r["mcp_screenshot"] = mcp_call(mcp_url, mcp_token, "screenshot", {"url": "http://x"})
    r["mcp_tools_list"] = mcp_list(mcp_url, mcp_token)
    r["mcp_read_board"] = mcp_call(mcp_url, mcp_token, "read_board", {})

    # Its own opencode server (same container, so reachable): record what the API
    # gives away. The boundaries asserted above must hold regardless.
    basic = base64.b64encode(f"opencode:{password}".encode()).decode()
    r["own_api_config"] = http("GET", "http://127.0.0.1:4096/config",
                               {"authorization": f"Basic {basic}"})

    with open(out_file, "w") as f:
        json.dump(r, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
