resource "helm_release" "argocd" {
  count      = var.argocd_enabled ? 1 : 0
  name       = "argocd"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.argocd_chart_version
  values     = [file("${path.module}/values/argocd.yaml")]
  wait       = true
  timeout    = 600

  depends_on = [kubernetes_namespace_v1.platform]

  lifecycle {
    precondition {
      condition     = contains(var.namespaces, "argocd")
      error_message = "argocd must be included in namespaces when Argo CD is enabled."
    }
  }
}
