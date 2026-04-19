import asyncio
from datetime import datetime

import jenkins

from ..models.results import BuildResult, BuildStatus


class JenkinsAdapter:
    def __init__(self, url: str, username: str, api_token: str):
        self.server = jenkins.Jenkins(url, username=username, password=api_token)

    async def trigger_build(self, job_name: str, parameters: dict, requester: str) -> BuildResult:
        loop = asyncio.get_event_loop()

        parameters["TRIGGERED_BY"] = requester
        parameters["TRIGGERED_AT"] = datetime.utcnow().isoformat()

        queue_item = await loop.run_in_executor(
            None,
            lambda: self.server.build_job(job_name, parameters=parameters),
        )

        return BuildResult(
            job_name=job_name,
            queue_item=queue_item,
            status=BuildStatus.QUEUED,
            triggered_by=requester,
        )

    async def poll_build_status(self, job_name: str, build_number: int) -> BuildStatus:
        loop = asyncio.get_event_loop()
        build_info = await loop.run_in_executor(
            None,
            lambda: self.server.get_build_info(job_name, build_number),
        )

        result = build_info.get("result")
        building = build_info.get("building", False)

        if building:
            return BuildStatus.RUNNING
        if result == "SUCCESS":
            return BuildStatus.SUCCESS
        if result == "FAILURE":
            return BuildStatus.FAILED
        return BuildStatus.ABORTED

    async def get_build_log(self, job_name: str, build_number: int, last_n_lines: int = 50) -> str:
        loop = asyncio.get_event_loop()
        full_log = await loop.run_in_executor(
            None,
            lambda: self.server.get_build_console_output(job_name, build_number),
        )
        lines = full_log.splitlines()
        return "\n".join(lines[-last_n_lines:])
