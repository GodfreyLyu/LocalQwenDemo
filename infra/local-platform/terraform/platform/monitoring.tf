resource "helm_release" "monitoring" {
  count      = var.monitoring_enabled ? 1 : 0
  name       = "monitoring"
  namespace  = "monitoring"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.monitoring_chart_version
  values     = [file("${path.module}/values/monitoring.yaml")]
  wait       = true
  timeout    = 900

  depends_on = [kubernetes_namespace_v1.platform]

  lifecycle {
    precondition {
      condition     = contains(var.namespaces, "monitoring")
      error_message = "monitoring must be included in namespaces when monitoring is enabled."
    }
  }
}
