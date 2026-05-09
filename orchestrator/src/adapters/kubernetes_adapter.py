import asyncio
from datetime import datetime
from typing import List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from ..models.results import DeploymentStatus, PodInfo, RolloutStatus


class KubernetesAdapter:
    def __init__(self, in_cluster: bool = True, kubeconfig_path: Optional[str] = None):
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=kubeconfig_path)

        self.apps_v1 = client.AppsV1Api()
        self.core_v1 = client.CoreV1Api()
        self.autoscaling_v1 = client.AutoscalingV1Api()

    async def get_deployment_status(self, name: str, namespace: str) -> DeploymentStatus:
        loop = asyncio.get_event_loop()
        try:
            deployment = await loop.run_in_executor(
                None,
                lambda: self.apps_v1.read_namespaced_deployment(name, namespace),
            )
        except ApiException as e:
            if e.status == 404:
                raise ValueError(f"Deployment '{name}' not found in namespace '{namespace}'")
            raise

        spec = deployment.spec
        status = deployment.status
        containers = spec.template.spec.containers

        return DeploymentStatus(
            name=name,
            namespace=namespace,
            desired_replicas=spec.replicas,
            ready_replicas=status.ready_replicas or 0,
            available_replicas=status.available_replicas or 0,
            current_image=containers[0].image if containers else "unknown",
            conditions=[
                {"type": c.type, "status": c.status, "reason": c.reason}
                for c in (status.conditions or [])
            ],
        )

    async def trigger_rolling_update(
        self,
        deployment_name: str,
        namespace: str,
        new_image: str,
        requester: str,
    ) -> RolloutStatus:
        loop = asyncio.get_event_loop()

        patch_body = {
            "metadata": {
                "annotations": {
                    "chatops.io/last-deploy-by": requester,
                    "chatops.io/last-deploy-at": datetime.utcnow().isoformat(),
                }
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "chatops.io/last-deploy-by": requester,
                            "chatops.io/last-deploy-at": datetime.utcnow().isoformat(),
                        }
                    },
                    "spec": {
                        "containers": [{"name": deployment_name, "image": new_image}]
                    },
                }
            },
        }

        await loop.run_in_executor(
            None,
            lambda: self.apps_v1.patch_namespaced_deployment(
                deployment_name, namespace, patch_body
            ),
        )

        return RolloutStatus(
            deployment=deployment_name,
            namespace=namespace,
            new_image=new_image,
            triggered_by=requester,
            status="ROLLING_UPDATE_INITIATED",
        )

    async def rollback_deployment(
        self,
        deployment_name: str,
        namespace: str,
        requester: str,
    ) -> str:
        """
        Kubernetes rollback pattern: read the prior ReplicaSet's pod-template
        and patch the Deployment back to it. (The deprecated rollback subresource
        was removed — we emulate `kubectl rollout undo`.)
        """
        loop = asyncio.get_event_loop()

        rs_list = await loop.run_in_executor(
            None,
            lambda: self.apps_v1.list_namespaced_replica_set(
                namespace,
                label_selector=f"app={deployment_name}",
            ),
        )

        revisions = sorted(
            [
                rs for rs in rs_list.items
                if rs.metadata.annotations
                and rs.metadata.annotations.get("deployment.kubernetes.io/revision")
            ],
            key=lambda rs: int(rs.metadata.annotations["deployment.kubernetes.io/revision"]),
        )
        if len(revisions) < 2:
            raise ValueError("No previous revision available for rollback")

        previous_rs = revisions[-2]
        previous_template = previous_rs.spec.template

        patch_body = {
            "metadata": {
                "annotations": {
                    "chatops.io/rollback-by": requester,
                    "chatops.io/rollback-at": datetime.utcnow().isoformat(),
                }
            },
            "spec": {"template": previous_template.to_dict()},
        }

        await loop.run_in_executor(
            None,
            lambda: self.apps_v1.patch_namespaced_deployment(
                deployment_name, namespace, patch_body
            ),
        )

        return f"Rollback initiated for {deployment_name} in {namespace}"

    async def scale_deployment(
        self,
        deployment_name: str,
        namespace: str,
        replicas: int,
        requester: str,
    ) -> str:
        loop = asyncio.get_event_loop()
        patch_body = {
            "metadata": {
                "annotations": {
                    "chatops.io/last-scale-by": requester,
                    "chatops.io/last-scale-at": datetime.utcnow().isoformat(),
                }
            },
            "spec": {"replicas": replicas},
        }
        await loop.run_in_executor(
            None,
            lambda: self.apps_v1.patch_namespaced_deployment(
                deployment_name, namespace, patch_body
            ),
        )
        return f"Scaled `{deployment_name}` in `{namespace}` to *{replicas}* replica(s) by <@{requester}>"

    async def get_pod_list(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
    ) -> List[PodInfo]:
        loop = asyncio.get_event_loop()

        pods = await loop.run_in_executor(
            None,
            lambda: self.core_v1.list_namespaced_pod(namespace, label_selector=label_selector),
        )

        return [
            PodInfo(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                phase=pod.status.phase,
                ready=all(cs.ready for cs in (pod.status.container_statuses or [])),
                restarts=sum(cs.restart_count for cs in (pod.status.container_statuses or [])),
                node=pod.spec.node_name,
                age=pod.metadata.creation_timestamp.isoformat()
                if pod.metadata.creation_timestamp else "",
            )
            for pod in pods.items
        ]

    async def get_pod_logs(
        self,
        pod_name: str,
        namespace: str,
        container: Optional[str] = None,
        tail_lines: int = 100,
    ) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.core_v1.read_namespaced_pod_log(
                pod_name,
                namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
            ),
        )
