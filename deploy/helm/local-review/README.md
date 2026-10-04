# Local Review Helm Chart

One release per namespace. Backend replicas are deliberately fixed at one.
Values configure images, model backend, resources, persistent storage and local
network access. The backend init container creates or validates the DynamoDB users
table before API startup. The Chart references an external signing Secret.

Use `scripts/helm_deploy.py` for an explicitly selected Minikube profile; it supplies
the host Ollama egress address and preserves signing material. See
`docs/guides/helm-release.md` for publishing, migration and deployment instructions.

Chart-created PVCs are retained on uninstall. To reuse Kustomize storage, set the
three `persistence.*.existingClaim` values and retain the original signing Secret.
