"""The ``full`` worker sandbox's network: the sandbox has its own network namespace (its own
loopback, so the worker's servers neither collide with nor reach the host's), and its only
way out is an HTTP proxy on the host that refuses every destination that isn't public.

    host:    Proxy  - CONNECT (and plain-http) proxy on a unix socket; resolves the target
                      itself and connects only to global unicast addresses: never loopback,
                      private LAN, link-local, CGNAT/tailnet or reserved ranges, so the
                      host's own services stay out of reach.
    sandbox: bridge - ``python netproxy.py --bridge SOCK PORT -- cmd ...``: listens on the
                      sandbox's 127.0.0.1:PORT, pipes each connection to SOCK (bind-mounted
                      in), then execs ``cmd`` with ``HTTPS_PROXY`` pointing at it.

Standard library only: the bridge runs as a plain script inside the sandbox."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

HEAD_LIMIT = 65536
CONNECT_TIMEOUT_S = 20


def public(ip: str) -> bool:
    """Whether the proxy may connect to ``ip``: global unicast only."""
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def allowlist(hosts) -> callable:
    """A host check for ``Proxy(hosts=...)``: a name is allowed when it is one of ``hosts``
    or a subdomain of one (``api.github.com`` under ``github.com`` only when ``github.com``
    is listed). An IP literal is never on a list of names. Case and a trailing dot don't
    matter. Used by maintenance's fix sessions (MAINTAIN.md §6.2: an injected "fetch this"
    fails here)."""
    names = {str(h).strip().lower().rstrip(".") for h in hosts or () if str(h).strip()}

    def ok(host: str) -> bool:
        h = str(host or "").strip("[]").lower().rstrip(".")
        if not h:
            return False
        try:
            ipaddress.ip_address(h.split("%", 1)[0])
            return False
        except ValueError:
            pass
        return any(h == n or h.endswith("." + n) for n in names)

    return ok


class Proxy:
    """A host-side proxy on ``<dir>/proxy.sock`` for one worker run (a context manager).
    ``allow(ip)`` decides each destination (default ``public``); ``hosts``, when given, is a
    list of host names (``allowlist``): any other name is refused before it is resolved, and
    an allowed one must still resolve to public addresses. ``log`` records
    ``(verdict, target)`` for every request. ``host_ok``, instead of ``hosts``: a callable
    deciding each host name, asked on every request (an Open in Claude Code session's list,
    which grows when the owner allows a host); ``on_refuse(host, target)`` is told of each
    name it refuses, and ``refusal`` is the message the client reads then."""

    def __init__(
        self,
        allow=public,
        hosts=None,
        forwards=(),
        host_ok=None,
        on_refuse=None,
        refusal: str = "host not on the allowlist",
    ):
        self.allow = allow
        self.host_ok = (
            host_ok if host_ok is not None else (allowlist(hosts) if hosts is not None else None)
        )
        self.on_refuse = on_refuse
        self.refusal = refusal
        # (host, port) on the host's loopback, each on a socket of its own (``forward_sock``):
        # an MCP server the owner added (repo_factory/mcp_servers); nothing else of the loopback
        self.forwards = [
            (str(h), int(p)) for h, p in forwards or () if str(h) in ("127.0.0.1", "::1")
        ]
        self.log: list[tuple[str, str]] = []
        self.dir: Path | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def sock(self) -> Path:
        return self.dir / "proxy.sock"

    def __enter__(self) -> Proxy:
        self.dir = Path(tempfile.mkdtemp(prefix="repo_factory-net-"))
        ready = threading.Event()
        err: list[BaseException] = []

        def main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            fwds = []
            try:
                srv = self._loop.run_until_complete(
                    asyncio.start_unix_server(self._handle, path=str(self.sock), limit=HEAD_LIMIT)
                )
                for host, port in self.forwards:
                    fwds.append(
                        self._loop.run_until_complete(
                            asyncio.start_unix_server(
                                self._forwarder(host, port), path=str(self.forward_sock(port))
                            )
                        )
                    )
            except BaseException as e:  # noqa: BLE001
                err.append(e)
                ready.set()
                return
            ready.set()
            try:
                self._loop.run_forever()
            finally:
                srv.close()  # open tunnels are cancelled, not waited for
                for f in fwds:
                    f.close()
                tasks = asyncio.all_tasks(self._loop)
                for t in tasks:
                    t.cancel()
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                self._loop.close()

        self._thread = threading.Thread(target=main, name="repo_factory-netproxy", daemon=True)
        self._thread.start()
        ready.wait()
        if err:
            shutil.rmtree(self.dir, ignore_errors=True)
            raise err[0]
        return self

    def __exit__(self, *exc) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        shutil.rmtree(self.dir, ignore_errors=True)

    def forward_sock(self, port: int) -> Path:
        return self.dir / f"fwd-{int(port)}.sock"

    def _forwarder(self, host: str, port: int):
        """A connection on ``fwd-<port>.sock`` goes to the host's ``host:port`` and nowhere
        else (the sandbox's bridge listens on its own 127.0.0.1:port)."""

        async def handle(reader, writer) -> None:
            target = f"loopback {host}:{port}"
            try:
                ur, uw = await asyncio.wait_for(
                    asyncio.open_connection(host, port), CONNECT_TIMEOUT_S
                )
            except (TimeoutError, OSError):
                self.log.append(("error 502", target))
                writer.close()
                return
            self.log.append(("allowed", target))
            await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))

        return handle

    async def _resolve(self, host: str, port: int) -> str | None:
        """An address of ``host`` the proxy may connect to; None if any address isn't."""
        host = host.strip("[]")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError:
            return None
        ips = [i[4][0] for i in infos]
        if not ips or not all(self.allow(ip) for ip in ips):
            return None
        return ips[0]

    async def _handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        line, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = line.decode("latin-1").split(" ", 2)
        except ValueError:
            return await self._refuse(writer, 400, "bad request", "?")
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            fwd = b""
        else:
            # plain http: "GET http://host[:port]/path HTTP/1.1" -> origin form
            u = urlsplit(target)
            if u.scheme.lower() != "http" or not u.hostname:
                return await self._refuse(writer, 400, "only CONNECT and http:// URLs", target)
            try:
                host, port = u.hostname, str(u.port or 80)
            except ValueError:
                return await self._refuse(writer, 400, "bad port", target)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            fwd = f"{method} {path} {version}\r\n".encode("latin-1") + rest
        try:
            port_n = int(port)
        except ValueError:
            return await self._refuse(writer, 400, "bad port", target)
        if self.host_ok is not None and not self.host_ok(host):
            if self.on_refuse is not None:
                try:
                    self.on_refuse(host, target)
                except Exception:  # noqa: BLE001 - the refusal stands either way
                    pass
            return await self._refuse(writer, 403, self.refusal, target)
        ip = await self._resolve(host, port_n)
        if ip is None:
            return await self._refuse(writer, 403, "destination not allowed", target)
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, port_n), CONNECT_TIMEOUT_S)
        except (TimeoutError, OSError):
            return await self._refuse(writer, 502, "could not connect", target)
        self.log.append(("allowed", target))
        if fwd:
            uw.write(fwd)
        else:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))

    async def _refuse(self, writer, code: int, why: str, target: str) -> None:
        self.log.append(("denied" if code == 403 else f"error {code}", target))
        body = f"repo_factory sandbox proxy: {why}: {target}\n".encode()
        writer.write(
            f"HTTP/1.1 {code} {why}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        finally:
            writer.close()


async def _pipe(r, w) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            if w.can_write_eof():
                w.write_eof()
            else:
                w.close()
        except (OSError, RuntimeError):
            pass


# -- inside the sandbox ---------------------------------------------------------------------
def _splice(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _forward(conn: socket.socket, sock_path: str) -> None:
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(sock_path)
    except OSError:
        conn.close()
        return
    t = threading.Thread(target=_splice, args=(up, conn), daemon=True)
    t.start()
    _splice(conn, up)
    t.join()
    conn.close()
    up.close()


def bridge(sock_path: str, port: int, argv: list[str], forwards=()) -> None:
    """Listen on 127.0.0.1:``port``, fork a forwarder to ``sock_path``, exec ``argv``.
    ``forwards``: more ``(sock, port)`` pairs, each listened on and piped the same way (an MCP
    server on the host's loopback, ``Proxy(forwards=...)``)."""
    srvs = []
    for sp, pt in [(sock_path, port), *forwards]:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", int(pt)))
        srv.listen(128)
        srvs.append((srv, sp))
    parent = os.getpid()
    if os.fork() == 0:
        try:
            # Die with the exec'd command (inside bwrap the pid namespace does this anyway),
            # and hold none of its stdio open.
            import ctypes

            ctypes.CDLL(None, use_errno=True).prctl(1, 9)  # PR_SET_PDEATHSIG, SIGKILL
            if os.getppid() != parent:
                os._exit(0)
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)

            def serve(srv, sp):
                while True:
                    conn, _ = srv.accept()
                    threading.Thread(target=_forward, args=(conn, sp), daemon=True).start()

            for srv, sp in srvs[1:]:
                threading.Thread(target=serve, args=(srv, sp), daemon=True).start()
            serve(*srvs[0])
        finally:
            os._exit(0)
    for srv, _sp in srvs:
        srv.close()
    os.execv(argv[0], argv)


def parse_args(a: list[str]) -> tuple[str, int, list, list[str]]:
    """``--bridge SOCK PORT [--forward SOCK PORT]... -- cmd [args...]``."""
    if len(a) < 5 or a[0] != "--bridge":
        raise ValueError
    sock, port, i, fwd = a[1], int(a[2]), 3, []
    while i < len(a) and a[i] == "--forward":
        fwd.append((a[i + 1], int(a[i + 2])))
        i += 3
    if i >= len(a) - 1 or a[i] != "--":
        raise ValueError
    return sock, port, fwd, a[i + 1 :]


if __name__ == "__main__":
    try:
        s, p, f, cmd = parse_args(sys.argv[1:])
    except (ValueError, IndexError):
        sys.exit("usage: netproxy.py --bridge SOCK PORT [--forward SOCK PORT]... -- cmd [args...]")
    bridge(s, p, cmd, f)
