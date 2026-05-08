import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _HAS_BOTO3 = True
except Exception:  # noqa: BLE001
    _HAS_BOTO3 = False


@dataclass
class EC2HealthReport:
    target: str
    instance_id: Optional[str] = None
    instance_state: Optional[str] = None
    instance_status: Optional[str] = None
    system_status: Optional[str] = None
    public_ip: Optional[str] = None
    public_dns: Optional[str] = None
    http_url: Optional[str] = None
    http_status: Optional[int] = None
    http_latency_ms: Optional[int] = None
    http_error: Optional[str] = None
    ssh_checks: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        if self.http_status is not None and not (200 <= self.http_status < 400):
            return False
        if self.instance_state and self.instance_state != "running":
            return False
        if self.errors:
            return False
        return True


class EC2Adapter:
    """
    Health checks for an application running on an EC2 instance.

    Three independent probes (each is best-effort):
      1. HTTP GET the deployed URL  -> reachability + latency
      2. boto3 describe instance     -> AWS-side instance state and status checks
      3. SSH (paramiko via thread)   -> uptime, disk, memory, service active

    Probes that aren't configured are skipped silently.
    """

    def __init__(
        self,
        target_name: str = "hello-cicd",
        http_url: Optional[str] = None,
        instance_id: Optional[str] = None,
        aws_region: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        ssh_host: Optional[str] = None,
        ssh_user: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        service_name: Optional[str] = None,
        app_dir: str = "~/app",
    ):
        self.target_name = target_name
        self.http_url = http_url
        self.instance_id = instance_id
        self.aws_region = aws_region
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.service_name = service_name or "nginx"
        self.app_dir = app_dir

    async def get_health(self) -> EC2HealthReport:
        report = EC2HealthReport(target=self.target_name)

        await asyncio.gather(
            self._probe_http(report),
            self._probe_aws(report),
            self._probe_ssh(report),
            return_exceptions=True,
        )
        return report

    async def _probe_http(self, report: EC2HealthReport) -> None:
        if not self.http_url:
            return
        report.http_url = self.http_url
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(self.http_url)
                report.http_status = resp.status_code
        except Exception as exc:  # noqa: BLE001
            report.http_error = f"{type(exc).__name__}: {exc}"
        finally:
            report.http_latency_ms = int((time.perf_counter() - start) * 1000)

    async def _probe_aws(self, report: EC2HealthReport) -> None:
        if not (_HAS_BOTO3 and self.instance_id and self.aws_region):
            return
        try:
            data = await asyncio.to_thread(self._describe_instance_sync)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"aws: {exc}")
            return
        report.instance_id = data.get("instance_id")
        report.instance_state = data.get("state")
        report.instance_status = data.get("instance_status")
        report.system_status = data.get("system_status")
        report.public_ip = data.get("public_ip")
        report.public_dns = data.get("public_dns")

    def _describe_instance_sync(self) -> dict:
        kwargs = {"region_name": self.aws_region}
        if self.aws_access_key_id and self.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        ec2 = boto3.client("ec2", **kwargs)

        out: dict = {}
        try:
            r = ec2.describe_instances(InstanceIds=[self.instance_id])
            reservations = r.get("Reservations", [])
            if reservations and reservations[0].get("Instances"):
                inst = reservations[0]["Instances"][0]
                out["instance_id"] = inst.get("InstanceId")
                out["state"] = inst.get("State", {}).get("Name")
                out["public_ip"] = inst.get("PublicIpAddress")
                out["public_dns"] = inst.get("PublicDnsName")
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"describe_instances failed: {exc}") from exc

        try:
            s = ec2.describe_instance_status(
                InstanceIds=[self.instance_id], IncludeAllInstances=True,
            )
            statuses = s.get("InstanceStatuses", [])
            if statuses:
                out["instance_status"] = statuses[0].get("InstanceStatus", {}).get("Status")
                out["system_status"] = statuses[0].get("SystemStatus", {}).get("Status")
        except (BotoCoreError, ClientError) as exc:
            log.warning("describe_instance_status failed: %s", exc)

        return out

    async def _probe_ssh(self, report: EC2HealthReport) -> None:
        if not (self.ssh_host and self.ssh_user and self.ssh_key_path):
            return
        try:
            checks = await asyncio.to_thread(self._ssh_health_sync)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"ssh: {exc}")
            return
        report.ssh_checks = checks

    def _ssh_health_sync(self) -> dict:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("paramiko not installed") from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ssh_host,
            username=self.ssh_user,
            key_filename=self.ssh_key_path,
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
        )
        try:
            checks = {
                "uptime": self._run(client, "uptime -p"),
                "load": self._run(client, "cat /proc/loadavg"),
                "disk": self._run(client, "df -h / | tail -1"),
                "memory": self._run(client, "free -h | awk 'NR==2{print $3\"/\"$2}'"),
                f"{self.service_name}": self._run(
                    client, f"systemctl is-active {self.service_name} || true",
                ),
            }
        finally:
            client.close()
        return checks

    def _connect_ssh(self, timeout: int = 10):
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("paramiko not installed") from exc
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ssh_host,
            username=self.ssh_user,
            key_filename=self.ssh_key_path,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        return client

    @staticmethod
    def _run(client, cmd: str, timeout: int = 5) -> str:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        return out or err or ""

    # ------------------------------------------------------------------ #
    #  Mutating operations via SSH                                         #
    # ------------------------------------------------------------------ #

    async def deploy(self, version: str, requester: str) -> str:
        if not (self.ssh_host and self.ssh_user and self.ssh_key_path):
            raise RuntimeError("SSH not configured — set EC2_SSH_HOST, EC2_SSH_USER, EC2_SSH_KEY_PATH")
        return await asyncio.to_thread(self._deploy_sync, version, requester)

    def _deploy_sync(self, version: str, requester: str) -> str:
        client = self._connect_ssh(timeout=15)
        try:
            # Verify git repo exists at app_dir
            is_git = self._run(
                client,
                f"git -C {self.app_dir} rev-parse --is-inside-work-tree 2>&1",
                timeout=10,
            )
            if "true" not in is_git:
                raise RuntimeError(
                    f"No git repo at `{self.app_dir}` on remote server. "
                    f"Clone your repo there first, then set EC2_APP_DIR in .env."
                )

            # Always fetch latest commits and tags from GitHub
            self._run(client, f"git -C {self.app_dir} fetch --all --tags 2>&1", timeout=30)

            if version in ("latest", "main", "master", ""):
                git_out = self._run(
                    client, f"git -C {self.app_dir} pull origin main 2>&1", timeout=30,
                )
            else:
                git_out = self._run(
                    client, f"git -C {self.app_dir} checkout -f {version} 2>&1", timeout=15,
                )

            current = self._run(
                client,
                f"git -C {self.app_dir} describe --tags --always 2>/dev/null",
                timeout=5,
            )
            self._run(client, f"sudo systemctl restart {self.service_name} 2>&1", timeout=15)

            lines = [f":rocket: Deployed `{self.target_name}` → `{current or version}` by <@{requester}>"]
            if git_out:
                lines.append(f"```{git_out[:300]}```")
            return "\n".join(lines)
        finally:
            client.close()

    async def get_logs(self, service: str, lines: int = 50) -> str:
        if not (self.ssh_host and self.ssh_user and self.ssh_key_path):
            raise RuntimeError("SSH not configured — set EC2_SSH_HOST, EC2_SSH_USER, EC2_SSH_KEY_PATH")
        return await asyncio.to_thread(self._logs_sync, service, lines)

    def _logs_sync(self, service: str, lines: int) -> str:
        client = self._connect_ssh(timeout=10)
        try:
            return self._run(
                client,
                f"sudo journalctl -u {self.service_name} --no-pager -n {lines} 2>&1",
                timeout=15,
            )
        finally:
            client.close()

    async def rollback(self, requester: str, version: str = "") -> str:
        if not (self.ssh_host and self.ssh_user and self.ssh_key_path):
            raise RuntimeError("SSH not configured — set EC2_SSH_HOST, EC2_SSH_USER, EC2_SSH_KEY_PATH")
        return await asyncio.to_thread(self._rollback_sync, requester, version)

    def _rollback_sync(self, requester: str, version: str) -> str:
        client = self._connect_ssh(timeout=15)
        try:
            if version:
                self._run(client, f"git -C {self.app_dir} fetch --all --tags 2>&1", timeout=20)
                out = self._run(client, f"git -C {self.app_dir} checkout -f {version} 2>&1", timeout=15)
                self._run(client, f"sudo systemctl restart {self.service_name} 2>&1", timeout=15)
                return f":rewind: Rolled back `{self.target_name}` → `{version}` by <@{requester}>\n```{out[:200]}```"

            prev = self._run(
                client,
                f"git -C {self.app_dir} log --format='%H' 2>/dev/null | sed -n '2p'",
                timeout=10,
            )
            if not prev or len(prev) < 7:
                return (
                    ":warning: No previous git commit found.\n"
                    "Specify a version explicitly: `/rollback service=hello-cicd version=v1.0.0`\n"
                    "Or check `EC2_APP_DIR` in your `.env` — it must point to the git repo on the remote server."
                )
            self._run(client, f"git -C {self.app_dir} checkout {prev} 2>&1", timeout=20)
            self._run(client, f"sudo systemctl restart {self.service_name} 2>&1", timeout=15)
            return f":rewind: Rolled back `{self.target_name}` to `{prev[:8]}` by <@{requester}>"
        finally:
            client.close()
