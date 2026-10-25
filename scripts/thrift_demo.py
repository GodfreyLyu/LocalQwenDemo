"""One generic generation using only the shared Python bindings (no backend imports)."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contracts/python"))

from llm_contract.generated import LlmService  # noqa: E402
from llm_contract.generated.ttypes import (  # noqa: E402
    GenerationOptions,
    GenerationRequest,
    GenerationState,
    Message,
)
from thrift.protocol import TBinaryProtocol  # noqa: E402
from thrift.transport import TSocket, TTransport  # noqa: E402


def generate(host, port, request_id, prompt):
    def call(method, *args):
        sock = TSocket.TSocket(host, port)
        sock.setTimeout(2000)
        transport = TTransport.TFramedTransport(sock, max_frame_size=2 * 1024 * 1024)
        try:
            transport.open()
            client = LlmService.Client(
                TBinaryProtocol.TBinaryProtocol(transport, strictRead=True, strictWrite=True)
            )
            return getattr(client, method)(*args)
        finally:
            transport.close()

    info = call("getModelInfo")
    request = GenerationRequest(
        request_id,
        info.instance_id,
        [Message("user", prompt)],
        GenerationOptions(max_output_tokens=min(32, info.limits.max_output_tokens), temperature=0),
        min(180000, info.limits.max_deadline_ms),
    )
    print(json.dumps({"request_id": request_id, "instance_id": info.instance_id}), flush=True)
    # Never retry a lost start response. Reconcile with getGeneration using these IDs.
    result = call("startGeneration", request)
    while result.state in {
        GenerationState.QUEUED,
        GenerationState.RUNNING,
        GenerationState.CANCELLING,
    }:
        time.sleep(0.1)
        result = call("getGeneration", info.instance_id, request_id)
    if result.state != GenerationState.SUCCEEDED:
        raise RuntimeError("Generation failed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--prompt", default="Translate Hello to French.")
    args = parser.parse_args()
    result = generate(args.host, args.port, args.request_id or str(uuid.uuid4()), args.prompt)
    print(result.result.content)


if __name__ == "__main__":
    main()
