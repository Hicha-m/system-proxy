"""Minimal SOCKS4a and SOCKS5 NO-AUTH tunnels for a fixed HTTPS test target.

No external dependencies, credentials or arbitrary destination requests.
The SOCKS proxy resolves example.com; the TLS client verifies its certificate.
"""
from __future__ import annotations

import socket
import ssl
import struct
from .collect import normalize_endpoint

HOST = "example.com"
PORT = 443


def recv_exact(conn: socket.socket, size: int) -> bytes:
    if not 0 <= size <= 65535:
        raise ValueError("Invalid SOCKS read size")
    result = bytearray()
    while len(result) < size:
        chunk = conn.recv(size - len(result))
        if not chunk:
            raise ConnectionError("SOCKS connection ended early")
        result.extend(chunk)
    return bytes(result)


def establish(conn: socket.socket, protocol: str) -> None:
    target = HOST.encode("ascii")
    if protocol == "socks5":
        conn.sendall(b"\x05\x01\x00")  # version 5, one auth method, NO-AUTH
        if recv_exact(conn, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS5 no-auth rejected")
        conn.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) +
                     target + struct.pack("!H", PORT))
        header = recv_exact(conn, 4)
        if header[0] != 5 or header[1] != 0 or header[2] != 0:
            raise ConnectionError("SOCKS5 CONNECT refused")
        if header[3] == 1:
            recv_exact(conn, 4 + 2)
        elif header[3] == 4:
            recv_exact(conn, 16 + 2)
        elif header[3] == 3:
            count = recv_exact(conn, 1)[0]
            recv_exact(conn, count + 2)
        else:
            raise ConnectionError("Unexpected SOCKS5 address type")
    elif protocol == "socks4":
        # SOCKS4a name resolution through the proxy, avoids local DNS.
        conn.sendall(b"\x04\x01" + struct.pack("!H", PORT) +
                     b"\x00\x00\x00\x01\x00" + target + b"\x00")
        response = recv_exact(conn, 8)
        if response[0] != 0 or response[1] != 0x5A:
            raise ConnectionError("SOCKS4a CONNECT refused")
    else:
        raise ValueError("Unsupported SOCKS protocol")


def probe_socks(ip: str, port: int, timeout: float, protocol: str) -> tuple[bool, str]:
    normalize_endpoint(f"{ip}:{port}")
    if protocol not in {"socks4", "socks5"} or not (0 < timeout <= 30):
        raise ValueError("Invalid checker configuration")
    with socket.create_connection((ip, port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        establish(conn, protocol)
        with ssl.create_default_context().wrap_socket(conn, server_hostname=HOST) as tls:
            tls.settimeout(timeout)
            tls.sendall(b"GET / HTTP/1.1\r\nHost: example.com\r\n"
                        b"User-Agent: system-proxy-checker/0.1\r\n"
                        b"Connection: close\r\n\r\n")
            result = bytearray()
            while len(result) < 128_000:
                chunk = tls.recv(min(8192, 128_000 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if b"Example Domain" in result and b"\r\n\r\n" in result:
                    break
            headers, divider, body = bytes(result).partition(b"\r\n\r\n")
            if (not divider or not headers.startswith(b"HTTP/1.")
                    or b" 200 " not in headers.split(b"\r\n", 1)[0]
                    or b"Example Domain" not in body):
                return False, "unexpected_response"
            return True, "verified_https_content"
