# Connect to the existing CLI-managed cluster. Never rely on current-context.
provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}

# Reserved for the future Argo CD and monitoring releases (Helm provider v3 syntax).
provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}
