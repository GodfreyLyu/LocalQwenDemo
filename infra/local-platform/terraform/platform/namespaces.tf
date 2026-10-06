resource "kubernetes_namespace_v1" "platform" {
  for_each = var.namespaces

  metadata {
    name = each.value
    labels = merge(
      {
        "app.kubernetes.io/part-of"    = "local-platform"
        "app.kubernetes.io/managed-by" = "terraform"
      },
      each.value == var.gpu_plugin_namespace ? {
        "pod-security.kubernetes.io/enforce" = "privileged"
      } : {}
    )
  }
}
