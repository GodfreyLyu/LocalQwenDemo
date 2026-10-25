"""Bounded TCP connections carrying generated Thrift processor calls."""

import socketserver
import threading

from llm_contract.generated import LlmService
from thrift.protocol import TBinaryProtocol
from thrift.Thrift import TException
from thrift.transport import TSocket, TTransport

MAX_FRAME_BYTES = 2 * 1024 * 1024


def wire(sock):
    transport = TTransport.TFramedTransport(sock, max_frame_size=MAX_FRAME_BYTES)
    protocol = TBinaryProtocol.TBinaryProtocol(
        transport,
        strictRead=True,
        strictWrite=True,
        string_length_limit=MAX_FRAME_BYTES,
        container_length_limit=64,
    )
    return transport, protocol


class RpcServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, handler, connections=32):
        self.processor = LlmService.Processor(handler)
        self.slots = threading.BoundedSemaphore(connections)
        super().__init__(address, RpcConnection)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        # socketserver's default traceback can expose decoded requests.
        pass


class RpcConnection(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        sock = TSocket.TSocket()
        sock.setHandle(self.request)
        transport, protocol = wire(sock)
        try:
            while True:
                self.server.processor.process(protocol, protocol)
        except (TException, OSError, ValueError, EOFError, TypeError, AttributeError):
            pass
        finally:
            transport.close()
