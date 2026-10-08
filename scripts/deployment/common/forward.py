"""Own only the supplied loopback-forward process; target selection belongs to callers."""

import contextlib
import errno
import os
import select
import socket
import subprocess
import threading
import time

from deployment.common.errors import DemoError, require


def safe_errno(exc):
    value = exc.errno
    return f"errno={value} ({errno.errorcode.get(value, 'UNKNOWN')})"


def port_available(port):
    """Probe reusable TCP listener semantics; success does not reserve the port."""
    try:
        with socket.socket() as sock:
            # Permit rebinding after our earlier listener's accepted connections close.
            # listen() also rejects an active listener on platforms allowing a shared bind.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
        return True
    except OSError as exc:
        reason = {
            errno.EADDRINUSE: "address_in_use",
            errno.EACCES: "permission_denied",
            errno.EPERM: "permission_denied",
        }.get(exc.errno, "bind_failed")
        raise DemoError(
            f"Loopback port {port}: {reason}; {safe_errno(exc)}. "
            "Check local listener state and execution permissions; "
            "no existing listener is reused or terminated."
        ) from None


@contextlib.contextmanager
def forward(service, remote_port, local_port=None, *, wait=False, command, env, start_timeout=10):
    """Own one loopback-forward child for this context and clean up only that process.

    A free-port probe is not a reservation. Require its exact announcement, live
    process and reachable socket within the deadline; never adopt a listener."""
    if local_port is None:
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                local_port = sock.getsockname()[1]
        except OSError as exc:
            raise DemoError(f"Loopback port allocation_failed; {safe_errno(exc)}.") from None
    port_available(local_port)
    try:
        proc = subprocess.Popen(
            command(
                "port-forward",
                "--address=127.0.0.1",
                "service/" + service,
                f"{local_port}:{remote_port}",
            ),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise DemoError(f"Port-forward process_start_failed; {safe_errno(exc)}.") from None
    drainer = None
    stop_drainer = threading.Event()
    try:
        deadline = time.monotonic() + start_timeout
        pending = b""
        reason = "listener_not_confirmed"
        announced = False
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.1)[0]:
                chunk = os.read(proc.stdout.fileno(), 4096)
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line == f"Forwarding from 127.0.0.1:{local_port} -> {remote_port}".encode():
                        announced = True
                    # Only fixed classifications escape this reader, never raw process output.
                    if b"address already in use" in line.lower():
                        reason = "address_in_use"
                    elif any(
                        p in line.lower()
                        for p in (b"permission denied", b"operation not permitted")
                    ):
                        reason = "permission_denied"
                pending = pending[-4096:]
            require(
                proc.poll() is None,
                f"Port-forward startup_failed on loopback port {local_port}: {reason}; "
                "the port may have changed after probing. No unrelated listener is reused.",
            )
            if announced:
                break
        require(announced, f"Port-forward startup_timeout on loopback port {local_port}: {reason}.")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                pass
        except OSError as exc:
            raise DemoError(f"Port-forward listener_unreachable; {safe_errno(exc)}.") from None
        require(proc.poll() is None, "Port-forward exited before listener readiness was confirmed.")

        def discard_output():
            while not stop_drainer.is_set():
                if select.select([proc.stdout], [], [], 0.1)[0]:
                    if not os.read(proc.stdout.fileno(), 4096):
                        break

        drainer = threading.Thread(target=discard_output, daemon=True)
        drainer.start()
        yield local_port
        if wait:
            while proc.poll() is None:
                time.sleep(0.2)
        require(
            proc.poll() is None, "Port-forward exited unexpectedly; forwarding is not verified."
        )
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise DemoError("Port-forward cleanup_timeout for the owned process.") from None
        finally:
            stop_drainer.set()
            if drainer:
                drainer.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()
