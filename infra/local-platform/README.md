# Local platform: krunkit + Terraform

此目录连接已由 Minikube CLI 启动的单节点 `krunkit/containerd` 集群。
Terraform 唯一 root module 为 `terraform/platform`，仅管理自定义 namespace 和
generic-device-plugin；没有 Minikube provider、`local-exec` 或集群启动操作。
Argo CD、监控的 `.tf` 和 values 文件暂为占位，不安装组件。

## 文件与状态

- `scripts/start-minikube.sh`：启动新集群或恢复已有集群；运行中的 profile 不重新配置。
- `scripts/verify-gpu.sh`：只读检查节点、DaemonSet 和 `devic.es/dri` 注册。
- `terraform/platform/terraform.tfvars.example`：可提交的参数示例。
- `terraform/platform/terraform.tfvars`：本机参数，已有 `.gitignore` 规则排除。
- `terraform/platform/.terraform.lock.hcl`：Terraform 生成的 provider 锁文件，应提交。
- `kubernetes/tests/gpu-smoke-test.yaml`：手动管理的测试 Pod，不属于 Terraform state。

Terraform 使用本地 state；`.terraform/`、`*.tfstate*`、`*.tfplan` 已被 Git 忽略。
保留 state，后续才能准确更新或删除这些平台资源。不要把相同资源交给其他 root module 管理。

## 初始化与计划

从项目根目录执行。当前集群已经启动，可以跳过启动脚本。

```bash
# 仅在需要启动/恢复集群时执行；不安装驱动或设备插件。
infra/local-platform/scripts/start-minikube.sh --profile minikube

terraform -chdir=infra/local-platform/terraform/platform init
terraform -chdir=infra/local-platform/terraform/platform validate
terraform -chdir=infra/local-platform/terraform/platform plan
```

本机 `terraform.tfvars` 已按 `minikube` context 配置。其他机器首次使用时从
`terraform.tfvars.example` 复制后修改。两个 provider 均显式使用同一 kubeconfig 和
context，不依赖 `kubectl config current-context`，也不自动采用 `KUBECONFIG`。
脚本使用 `KUBE_CONFIG_PATH` 指定单个 kubeconfig 文件，默认 `$HOME/.kube/config`；
自定义时同步设置 Terraform 的 `kubeconfig_path`。

默认计划应创建 4 个 namespace（`gpu-system`、`gpu-tests`、`argocd`、`monitoring`）
和 1 个 DaemonSet。若同名资源已经存在，先检查归属，再决定导入或更换名称。
不要同时运行其他 namespace 中注册相同 `devic.es/dri` 的设备插件。

检查计划后手动部署：

```bash
terraform -chdir=infra/local-platform/terraform/platform apply
infra/local-platform/scripts/verify-gpu.sh --profile minikube
```

## GPU 资源与测试

插件显式设置 `--domain=devic.es`，只发布 `/dev/dri/renderD128` 为 `devic.es/dri`。
镜像固定为 generic-device-plugin 0.2.0 的多架构 digest，包含 ARM64。
插件本身需要 privileged 与 kubelet socket；测试 Pod 无 privileged、无 hostPath，
依靠扩展资源申请取得设备，使用 UID 1000 检查并打开 render 节点。

`gpu_slots = 1` 默认只允许一个 Pod 同时申请该设备。调大该值代表共享同一 GPU，
不代表增加物理 GPU，也不提供显存隔离。修改后用 `--expected-slots N` 验证。
Node 的 `allocatable` 表示总可调度容量，不会随 Pod 使用而递减。

```bash
kubectl --context minikube apply -f infra/local-platform/kubernetes/tests/gpu-smoke-test.yaml
kubectl --context minikube -n gpu-tests wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-smoke-test --timeout=150s
kubectl --context minikube -n gpu-tests logs gpu-smoke-test
kubectl --context minikube -n gpu-tests delete pod gpu-smoke-test
```

重复测试需先删除上一次的 Pod，再创建。若修改默认 namespace，同步修改测试 YAML；
若 kubeconfig 不在默认路径，上述 kubectl 命令也需传入 `--kubeconfig`。
测试依赖节点上 render 设备的读写权限；当前节点的 `renderD128` 为 `0666`。

该测试只证明调度、设备注入与设备打开成功；不验证 Vulkan、模型推理或 GPU 利用率。
后续推理工作负载还需要支持 krunkit virtio GPU 的用户态驱动与推理镜像。
当前仓库原有 `scripts/minikube_demo.sh` 仍有 Docker driver 前提，不用于此 krunkit 平台。

失败时查看：

```bash
kubectl --context minikube -n gpu-system logs daemonset/generic-device-plugin
kubectl --context minikube -n gpu-tests describe pod gpu-smoke-test
kubectl --context minikube describe node minikube
```

Terraform destroy 会删除这里管理的 namespace 及其中资源，包括以后手动放入的资源；
它不会删除 Minikube VM。测试 Pod 虽不在 state 中，也会随 `gpu-tests` namespace 删除。

## 上游依据

- [Minikube krunkit driver](https://minikube.sigs.k8s.io/docs/drivers/krunkit/)
- [Minikube Apple silicon AI playground](https://minikube.sigs.k8s.io/docs/tutorials/ai-playground/)
- [generic-device-plugin 0.2.0](https://github.com/squat/generic-device-plugin/tree/0.2.0)
- [Helm provider 配置](https://registry.terraform.io/providers/hashicorp/helm/latest/docs)
