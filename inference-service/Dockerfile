# Validated on ARM64 krunkit; the downstream Mesa build fixes 16 KiB alignment.
FROM registry.fedoraproject.org/fedora@sha256:dbb22055c0c19f4eba2afbbb717667b0c76aa956c4283700041980ad3710bb73
COPY deploy/images/ollama-vulkan/mesa-krunkit.repo /etc/yum.repos.d/mesa-krunkit.repo
RUN dnf install -y --disablerepo=fedora-cisco-openh264 --setopt=install_weak_deps=False \
      ollama-0.24.0-4.fc45 ollama-vulkan-0.24.0-4.fc45 \
      mesa-vulkan-drivers-25.3.6-102.fc44 mesa-filesystem-25.3.6-102.fc44 \
      vulkan-tools python3 python3-pip tar \
    && dnf clean all \
    && rpm -qa | sort > /etc/ollama-image-packages.txt \
    && test -f /usr/share/vulkan/icd.d/virtio_icd.aarch64.json
COPY inference-service/requirements.lock /opt/llm/requirements.lock
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /opt/llm/requirements.lock
COPY contracts /opt/contracts
COPY inference-service/pyproject.toml /opt/llm/pyproject.toml
COPY inference-service/src /opt/llm/src
RUN /opt/venv/bin/pip install --no-deps /opt/contracts /opt/llm
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp/ollama-home \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_RUNTIME_DIR=/tmp/runtime \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/models \
    OLLAMA_VULKAN=1 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_KEEP_ALIVE=5m \
    VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.aarch64.json
USER 10001:10001
EXPOSE 9091 11435
ENTRYPOINT ["llm-service"]
