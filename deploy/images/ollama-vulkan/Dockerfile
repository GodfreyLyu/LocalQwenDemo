# Validated on ARM64 krunkit; the downstream Mesa build fixes 16 KiB alignment.
FROM registry.fedoraproject.org/fedora@sha256:dbb22055c0c19f4eba2afbbb717667b0c76aa956c4283700041980ad3710bb73
COPY mesa-krunkit.repo /etc/yum.repos.d/mesa-krunkit.repo
RUN dnf install -y --disablerepo=fedora-cisco-openh264 --setopt=install_weak_deps=False \
      ollama-0.24.0-4.fc45 ollama-vulkan-0.24.0-4.fc45 \
      mesa-vulkan-drivers-25.3.6-102.fc44 mesa-filesystem-25.3.6-102.fc44 \
      vulkan-tools python3 tar \
    && dnf clean all \
    && rpm -qa | sort > /etc/ollama-image-packages.txt \
    && test -f /usr/share/vulkan/icd.d/virtio_icd.aarch64.json
COPY runtime.py /opt/ollama/runtime.py
ENV HOME=/tmp/ollama-home \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_RUNTIME_DIR=/tmp/runtime \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/models \
    OLLAMA_VULKAN=1 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_KEEP_ALIVE=-1 \
    VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.aarch64.json
USER 10001:10001
EXPOSE 11434 11435
ENTRYPOINT ["python3", "/opt/ollama/runtime.py"]
CMD ["serve"]
