"""Small real Thrift generation probe used by Helm tests and Argo PostSync."""

import argparse
import json
import time
import uuid

from llm_contract.generated import LlmService
from llm_contract.generated.ttypes import (
    GenerationOptions,
    GenerationRequest,
    GenerationState,
    Message,
)
from thrift.transport import TSocket

from llm_service.server import wire


def verify(host, port=9091):
    def call(method, *args):
        sock = TSocket.TSocket(host, port)
        sock.setTimeout(2000)
        transport, protocol = wire(sock)
        try:
            transport.open()
            return getattr(LlmService.Client(protocol), method)(*args)
        finally:
            transport.close()

    info = call("getModelInfo")
    if info.device != "gpu" or not call("getHealth").ready:
        raise RuntimeError("GPU service is not ready")
    request = GenerationRequest(
        str(uuid.uuid4()),
        info.instance_id,
        [Message("user", "Reply with the word Ready.")],
        GenerationOptions(max_output_tokens=8, temperature=0),
        min(180000, info.limits.max_deadline_ms),
    )
    snapshot = call("startGeneration", request)
    deadline = time.monotonic() + 200
    while snapshot.state in {
        GenerationState.QUEUED,
        GenerationState.RUNNING,
        GenerationState.CANCELLING,
    }:
        if time.monotonic() >= deadline:
            call("cancelGeneration", info.instance_id, request.request_id)
            raise RuntimeError("Verification deadline exceeded")
        time.sleep(0.1)
        snapshot = call("getGeneration", info.instance_id, request.request_id)
    if (
        snapshot.state != GenerationState.SUCCEEDED
        or not snapshot.result.content.strip()
        or snapshot.result.usage.output_tokens <= 0
    ):
        raise RuntimeError("Verification generation failed")
    print(
        json.dumps(
            {
                "event": "gpu_verified",
                "model": info.model_id,
                "revision": info.model_revision,
                "output_tokens": snapshot.result.usage.output_tokens,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9091)
    args = parser.parse_args()
    try:
        verify(args.host, args.port)
    except Exception:
        parser.exit(1, "Inference verification failed.\n")


if __name__ == "__main__":
    main()
