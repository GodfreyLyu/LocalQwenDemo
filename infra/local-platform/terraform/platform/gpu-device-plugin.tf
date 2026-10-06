locals {
  gpu_resource_name = "devic.es/dri"
  gpu_plugin_labels = {
    "app.kubernetes.io/name"    = "generic-device-plugin"
    "app.kubernetes.io/part-of" = "local-platform"
  }
}

resource "kubernetes_daemon_set_v1" "gpu_device_plugin" {
  metadata {
    name      = "generic-device-plugin"
    namespace = var.gpu_plugin_namespace
    labels    = local.gpu_plugin_labels
  }

  spec {
    selector {
      match_labels = local.gpu_plugin_labels
    }

    template {
      metadata {
        labels = local.gpu_plugin_labels
      }

      spec {
        automount_service_account_token = false
        node_selector = {
          "kubernetes.io/os"   = "linux"
          "kubernetes.io/arch" = "arm64"
        }

        toleration {
          operator = "Exists"
          effect   = "NoSchedule"
        }
        toleration {
          operator = "Exists"
          effect   = "NoExecute"
        }

        container {
          name              = "generic-device-plugin"
          image             = var.gpu_device_plugin_image
          image_pull_policy = "IfNotPresent"
          args = [
            "--domain=devic.es",
            "--device",
            jsonencode({
              name = "dri"
              groups = [{
                count = var.gpu_slots
                paths = [{ path = "/dev/dri/renderD128" }]
              }]
            })
          ]

          # Device discovery and the kubelet plugin socket require host access.
          # Workload Pods requesting devic.es/dri do not need privileged mode.
          security_context {
            privileged = true
          }

          resources {
            requests = { cpu = "50m", memory = "32Mi" }
            limits   = { cpu = "200m", memory = "128Mi" }
          }

          port {
            name           = "health"
            container_port = 8080
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = "health"
            }
          }

          volume_mount {
            name       = "device-plugins"
            mount_path = "/var/lib/kubelet/device-plugins"
          }
          volume_mount {
            name       = "dri"
            mount_path = "/dev/dri"
            read_only  = true
          }
        }

        volume {
          name = "device-plugins"
          host_path {
            path = "/var/lib/kubelet/device-plugins"
            type = "Directory"
          }
        }
        volume {
          name = "dri"
          host_path {
            path = "/dev/dri"
            type = "Directory"
          }
        }
      }
    }
  }

  wait_for_rollout = true
  timeouts {
    create = "5m"
    update = "5m"
    delete = "5m"
  }

  depends_on = [kubernetes_namespace_v1.platform]

  lifecycle {
    precondition {
      condition     = contains(var.namespaces, var.gpu_plugin_namespace)
      error_message = "gpu_plugin_namespace must be included in namespaces."
    }
  }
}
