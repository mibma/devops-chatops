import asyncio
from typing import List, Optional

import docker

from ..models.results import ContainerInfo, ImageInfo


class DockerAdapter:
    def __init__(self, host: Optional[str] = None, tls_config=None):
        self.client = docker.DockerClient(
            base_url=host or "unix:///var/run/docker.sock",
            tls=tls_config,
        )

    async def list_containers(self, filter_label: Optional[str] = None) -> List[ContainerInfo]:
        loop = asyncio.get_event_loop()
        filters = {"label": filter_label} if filter_label else {}

        containers = await loop.run_in_executor(
            None,
            lambda: self.client.containers.list(filters=filters),
        )

        return [
            ContainerInfo(
                id=c.short_id,
                name=c.name,
                image=c.image.tags[0] if c.image.tags else "untagged",
                status=c.status,
                ports=c.ports,
                created=c.attrs["Created"],
                labels=c.labels,
            )
            for c in containers
        ]

    async def get_container_logs(self, container_name: str, tail: int = 100) -> str:
        loop = asyncio.get_event_loop()
        container = await loop.run_in_executor(
            None,
            lambda: self.client.containers.get(container_name),
        )
        logs = await loop.run_in_executor(
            None,
            lambda: container.logs(tail=tail, timestamps=True).decode("utf-8"),
        )
        return logs

    async def pull_image(self, image: str, tag: str = "latest") -> ImageInfo:
        loop = asyncio.get_event_loop()
        image_obj = await loop.run_in_executor(
            None,
            lambda: self.client.images.pull(image, tag=tag),
        )
        return ImageInfo(
            id=image_obj.short_id,
            tags=image_obj.tags,
            size_mb=round(image_obj.attrs["Size"] / (1024 * 1024), 2),
            created=image_obj.attrs["Created"],
        )
