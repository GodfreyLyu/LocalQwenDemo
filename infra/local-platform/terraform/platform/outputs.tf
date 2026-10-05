output "kube_context" {
  description = "Context used by both providers."
  value       = var.kube_context
}

output "namespaces" {
  description = "Custom namespaces managed by this root module."
  value       = sort([for ns in kubernetes_namespace_v1.platform : ns.metadata[0].name])
}

output "gpu_plugin_namespace" {
  value = var.gpu_plugin_namespace
}

output "gpu_resource_name" {
  description = "Use this key in workload resources.requests and resources.limits."
  value       = local.gpu_resource_name
}

output "gpu_slots_per_node" {
  description = "Configured allocation slots, not a measurement of physical GPUs or availability."
  value       = var.gpu_slots
}
