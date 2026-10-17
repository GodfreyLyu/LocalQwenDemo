# Monitoring schemas

These JSON schemas are extracted from the `v1` OpenAPI definitions in Prometheus
Operator **v0.94.1**, matching kube-prometheus-stack **92.10.1**. The upstream URL is
recorded in each schema's `$comment`.
The upstream schemas are distributed under the Apache License 2.0; see [LICENSE](LICENSE).

Descriptions are omitted to keep the files small. Kubernetes integer-or-string
fields are represented as JSON Schema `anyOf`. Declared object properties reject
unknown fields unless the upstream schema permits them; free-form metadata/maps
remain open. Other constraints are preserved.
Kubeconform validates these CRDs explicitly instead of ignoring unknown kinds.
Kubernetes CEL rules and PromQL semantics require their own validation; promtool
checks the actual rendered rules and dashboards in the configuration CI job.

Update both schemas when upgrading the platform Operator. They are validator-owned
source files and do not need to be included in application release snapshots.
