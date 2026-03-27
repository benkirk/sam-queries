# ☸️ Kubernetes & OIDC Cheat Sheet (`nwc1`)

This guide is optimized for navigating the `nwc1` cluster using OIDC authentication.

## 1. Context & Navigation
| Task | Command |
| :--- | :--- |
| **Check Current Context** | `kubectl config current-context` |
| **Switch to OIDC Context** | `kubectl config use-context nwc1` |
| **List All Namespaces** | `kubectl get ns` |
| **Set Default Namespace** | `kubectl config set-context --current --namespace=<name>` |

---

## 2. Pods, Logs & Status
| Task | Command |
| :--- | :--- |
| **List Pods (Brief)** | `kubectl get pods` |
| **List Pods (Detailed/IPs)** | `kubectl get pods -o wide` |
| **Watch Pods (Live)** | `kubectl get pods -w` |
| **Describe Pod (Troubleshooting)** | `kubectl describe pod <pod-name>` |
| **Get Pod Logs** | `kubectl logs <pod-name>` |
| **Stream Logs (Live Tail)** | `kubectl logs -f <pod-name>` |
| **Logs from a crashed pod** | `kubectl logs <pod-name> --previous` |
| **Execute Shell into Pod** | `kubectl exec -it <pod-name> -- /bin/bash` |

---

## 3. Helm Charts (Package Management)
| Task | Command |
| :--- | :--- |
| **List Installed Releases** | `helm list -A` |
| **Search for Charts** | `helm search repo <keyword>` |
| **Show Values for Release** | `helm get values <release-name>` |
| **Upgrade/Install Release** | `helm upgrade --install <name> <chart> -f values.yaml` |
| **Rollback a Release** | `helm rollback <release-name> <revision-number>` |
| **Uninstall a Release** | `helm uninstall <release-name>` |

---

## 4. Secrets & Configuration
| Task | Command |
| :--- | :--- |
| **List Secrets** | `kubectl get secrets` |
| **View Secret YAML** | `kubectl get secret <name> -o yaml` |
| **Decode Secret Value** | `kubectl get secret <name> -o jsonpath='{.data.key}' | base64 --decode` |
| **List ConfigMaps** | `kubectl get cm` |
| **View ConfigMap YAML** | `kubectl get cm <name> -o yaml` |

---

## 5. Storage (PVCs & PVs)
| Task | Command |
| :--- | :--- |
| **List PersistentVolumeClaims** | `kubectl get pvc` |
| **List PersistentVolumes** | `kubectl get pv` |
| **Check Storage Capacity** | `kubectl describe pvc <name>` |
| **Check Storage Classes** | `kubectl get sc` |

---

## 6. Pro-Tips for Troubleshooting
*   **The "Describe" Rule:** If a pod is stuck in `Pending` or `CrashLoopBackOff`, run `kubectl describe pod <name>` and look at the **Events** section at the bottom.
*   **Resource Usage:** Use `kubectl top pod` or `kubectl top node` to see CPU/Memory usage (if metrics-server is installed).
*   **Tab Completion:** Enable it in your `~/.zshrc`:
    ```bash
    source <(kubectl completion zsh)
    alias k='kubectl'
    complete -F __start_kubectl k
    ```

## 7. OIDC & Authentication
If your token expires or you get `Unauthorized` errors:
*   **Refresh Token:** Run any `kubectl` command; `kubelogin` should trigger a browser login.
*   **Force Re-login:**
    ```bash
    kubelogin remove-tokens
    kubectl get pods
    ```
