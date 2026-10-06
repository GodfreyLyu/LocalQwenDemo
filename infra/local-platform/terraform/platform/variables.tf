variable "kubeconfig_path" {
  description = "Single kubeconfig file shared by both providers."
  type        = string
  default     = "~/.kube/config"

  validation {
    condition     = length(trimspace(var.kubeconfig_path)) > 0
    error_message = "kubeconfig_path must not be empty."
  }
}

variable "kube_context" {
  description = "Explicit kubeconfig context for the existing krunkit cluster."
  type        = string
  default     = "minikube"

  validation {
    condition     = length(trimspace(var.kube_context)) > 0
    error_message = "kube_context must not be empty."
  }
}

variable "namespaces" {
  description = "Custom namespaces managed here; exclude Kubernetes system namespaces."
  type        = set(string)
  default     = ["gpu-system", "gpu-tests", "argocd", "monitoring"]

  validation {
    condition = alltrue([
      for name in var.namespaces :
      can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", name)) &&
      length(name) <= 63 && name != "default" && !startswith(name, "kube-")
    ])
    error_message = "Use custom DNS-label namespace names, not default or kube-* namespaces."
  }
}

variable "gpu_plugin_namespace" {
  description = "Namespace for the device plugin; must be included in namespaces."
  type        = string
  default     = "gpu-system"
}

variable "gpu_device_plugin_image" {
  description = "generic-device-plugin 0.2.0 multi-platform image, including linux/arm64."
  type        = string
  default     = "docker.io/squat/generic-device-plugin:0.2.0@sha256:66c8d5c270eb2b721f1064c549b9b7898152a6d2f0163380a5d37dc7636c20ff"

  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.gpu_device_plugin_image))
    error_message = "Pin the plugin image to a verified sha256 digest."
  }
}

variable "gpu_slots" {
  description = "Allocation slots for the same render device per node; values above 1 share the device without GPU memory isolation."
  type        = number
  default     = 1

  validation {
    condition     = var.gpu_slots >= 1 && floor(var.gpu_slots) == var.gpu_slots
    error_message = "gpu_slots must be a positive integer."
  }
}

variable "argocd_enabled" {
  description = "Install Argo CD; application bootstrap remains an explicit step after release approval."
  type        = bool
  default     = false
}

variable "argocd_chart_version" {
  description = "Pinned upstream argo-cd Helm Chart version."
  type        = string
  default     = "10.9.6"
}
