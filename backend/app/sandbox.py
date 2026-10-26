"""Policy-constrained ephemeral Docker runner for reviewing untrusted code proposals."""

import difflib
import io
import os
import tarfile
import threading
import time
from collections.abc import Mapping
from typing import Literal

import docker
from docker.types import Ulimit
from pydantic import BaseModel, ConfigDict, Field, model_validator
from requests.exceptions import Timeout as RequestsTimeout

SANDBOX_IMAGE = "orqestra-sandbox:0.1.0"
MAX_FILES = 30
MAX_PATCH_FILES = 20
MAX_FILE_BYTES = 256 * 1024
MAX_INPUT_BYTES = 1024 * 1024
MAX_JSON_REQUEST_BYTES = 8 * 1024 * 1024
MAX_DIFF_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MAX_RUNTIME_SECONDS = 35
MAX_CHECKS = 3
MAX_CONCURRENT_RUNS = 2
_RUN_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)

CHECK_COMMANDS: dict[str, list[str]] = {
    "pytest": ["python", "-m", "pytest", "-q", "--disable-warnings"],
    "unittest": ["python", "-m", "unittest"],
    "compileall": ["python", "-m", "compileall", "-q", "."],
}


class SandboxError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_project_path(value: str) -> str:
    if (
        not value
        or len(value) > 240
        or value.startswith(("/", "~"))
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Project file paths must be relative and normalized")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Project file paths must be relative and normalized")
    return value


class SandboxRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(min_length=1, max_length=36)
    files: dict[str, str] = Field(min_length=1, max_length=MAX_FILES)
    patch: dict[str, str] = Field(min_length=1, max_length=MAX_PATCH_FILES)
    checks: list[Literal["pytest", "unittest", "compileall"]] = Field(
        min_length=1, max_length=MAX_CHECKS
    )

    @model_validator(mode="after")
    def validate_project_payload(self):
        paths = [*self.files, *self.patch]
        if len({path.casefold() for path in self.files}) != len(self.files):
            raise ValueError("Project file paths must be unique")
        if len({path.casefold() for path in self.patch}) != len(self.patch):
            raise ValueError("Patch paths must be unique")
        total = 0
        for path in paths:
            validate_project_path(path)
        for content in (*self.files.values(), *self.patch.values()):
            size = len(content.encode("utf-8"))
            if size > MAX_FILE_BYTES:
                raise ValueError("Each project file is limited to 256 KiB")
            total += size
        if total > MAX_INPUT_BYTES:
            raise ValueError("The combined project and patch are limited to 1 MiB")
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("Each check may be selected only once")
        return self


def sandbox_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get("SANDBOX_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def sandbox_runtime_configured(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    if not sandbox_enabled(values):
        return False
    docker_host = values.get("DOCKER_HOST", "").strip()
    tls_verify = values.get("DOCKER_TLS_VERIFY", "").strip().lower() in ("1", "true", "yes")
    certificate_dir = values.get("DOCKER_CERT_PATH", "").strip()
    return (
        docker_host.startswith("tcp://")
        and tls_verify
        and bool(certificate_dir)
        and all(
            os.path.isfile(os.path.join(certificate_dir, name))
            for name in ("ca.pem", "cert.pem", "key.pem")
        )
    )


def _tar_project(files: dict[str, str], patch: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        root = "project"
        mapping = files | patch
        directories: set[str] = set()
        for path in mapping:
            validate_project_path(path)
            parent = os.path.dirname(path)
            while parent:
                directories.add(parent)
                parent = os.path.dirname(parent)
        for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(f"{root}/{directory}/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = info.gid = 10001
            info.uname = info.gname = "sandbox"
            archive.addfile(info)
        for path, content in sorted(mapping.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{root}/{path}")
            info.type = tarfile.REGTYPE
            info.mode = 0o644
            info.uid = info.gid = 10001
            info.uname = info.gname = "sandbox"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _proposal_diff(files: dict[str, str], patch: dict[str, str]) -> str:
    chunks: list[str] = []
    for path in sorted(patch):
        original = files.get(path, "")
        replacement = patch[path]
        chunks.extend(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                replacement.splitlines(keepends=True),
                fromfile=f"a/{path}" if path in files else "/dev/null",
                tofile=f"b/{path}",
            )
        )
    diff = "".join(chunks)
    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        raise SandboxError("sandbox_diff_too_large", "The proposed diff exceeds 128 KiB")
    return diff


def _text_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace")
    else:
        decoded = value
    if len(decoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        decoded = decoded.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        decoded += "\n[output truncated]"
    return decoded


class SandboxController:
    def __init__(self, image: str = SANDBOX_IMAGE, client_factory=docker.from_env):
        if image != SANDBOX_IMAGE:
            raise ValueError("Only the pinned ORQESTRA sandbox image is permitted")
        self.image = image
        self.client_factory = client_factory

    def run(self, request: SandboxRunRequest) -> dict:
        if not _RUN_SLOTS.acquire(blocking=False):
            raise SandboxError("sandbox_busy", "The sandbox is at its concurrent run limit")
        try:
            return self._run_once(request)
        finally:
            _RUN_SLOTS.release()

    def _run_once(self, request: SandboxRunRequest) -> dict:
        started = time.monotonic()
        proposal = _proposal_diff(request.files, request.patch)
        try:
            client = self.client_factory(timeout=MAX_RUNTIME_SECONDS + 5)
        except docker.errors.DockerException:
            raise SandboxError(
                "sandbox_unavailable", "The isolated sandbox runtime is unavailable"
            ) from None

        container = None
        try:
            info = client.info()
            if str(info.get("CgroupVersion", "")) != "2":
                raise SandboxError(
                    "sandbox_limits_unavailable",
                    "The sandbox runtime must support cgroup v2 resource limits",
                )
            try:
                image = client.images.get(self.image)
            except docker.errors.ImageNotFound:
                raise SandboxError(
                    "sandbox_image_missing",
                    "Build the pinned sandbox runtime image before enabling code runs",
                ) from None
            labels = (image.attrs.get("Config") or {}).get("Labels") or {}
            if labels.get("org.orqestra.sandbox") != "checkpoint-10":
                raise SandboxError(
                    "sandbox_image_invalid", "The local image is not the verified sandbox runtime"
                )

            container = client.containers.create(
                image=self.image,
                command=["python", "-c", "import time; time.sleep(60)"],
                name=f"orqestra-sandbox-{os.urandom(8).hex()}",
                user="10001:10001",
                working_dir="/workspace/project",
                environment={
                    "HOME": "/tmp",
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "PIP_NO_INDEX": "1",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                },
                network_mode="none",
                read_only=True,
                privileged=False,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="256m",
                memswap_limit="256m",
                nano_cpus=500_000_000,
                pids_limit=32,
                ulimits=[
                    Ulimit(name="cpu", soft=20, hard=20),
                    Ulimit(name="fsize", soft=8 * 1024 * 1024, hard=8 * 1024 * 1024),
                    Ulimit(name="nofile", soft=64, hard=64),
                ],
                tmpfs={
                    "/workspace": "rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700",
                    "/tmp": "rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
                },
                detach=True,
                auto_remove=False,
                init=True,
            )
            container.start()
            if not container.put_archive("/workspace", _tar_project(request.files, request.patch)):
                raise SandboxError(
                    "sandbox_input_failed", "The sandbox could not receive the project"
                )

            outputs = []
            status = "completed"
            deadline = started + MAX_RUNTIME_SECONDS
            for check in request.checks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timed_out"
                    outputs.append(
                        {
                            "check": check,
                            "status": "timed_out",
                            "exit_code": None,
                            "stdout": "",
                            "stderr": "Sandbox time limit exceeded",
                        }
                    )
                    break
                client.api.timeout = max(1, int(remaining))
                try:
                    result = container.exec_run(
                        cmd=CHECK_COMMANDS[check],
                        workdir="/workspace/project",
                        user="10001:10001",
                        environment={
                            "HOME": "/tmp",
                            "PATH": "/usr/local/bin:/usr/bin:/bin",
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                            "PIP_NO_INDEX": "1",
                            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                        },
                        demux=True,
                        stdout=True,
                        stderr=True,
                    )
                except RequestsTimeout:
                    status = "timed_out"
                    outputs.append(
                        {
                            "check": check,
                            "status": status,
                            "exit_code": None,
                            "stdout": "",
                            "stderr": "Sandbox check exceeded its execution limit",
                        }
                    )
                    break
                except docker.errors.DockerException:
                    status = "failed"
                    outputs.append(
                        {
                            "check": check,
                            "status": status,
                            "exit_code": None,
                            "stdout": "",
                            "stderr": "Sandbox check exceeded its execution limit",
                        }
                    )
                    break
                stdout, stderr = result.output or (None, None)
                exit_code = result.exit_code
                outputs.append(
                    {
                        "check": check,
                        "status": "passed" if exit_code == 0 else "failed",
                        "exit_code": exit_code,
                        "stdout": _text_output(stdout),
                        "stderr": _text_output(stderr),
                    }
                )
                if exit_code != 0:
                    status = "failed"
            return {
                "status": status,
                "image": self.image,
                "image_id": image.id,
                "runtime_seconds": round(min(time.monotonic() - started, MAX_RUNTIME_SECONDS), 3),
                "diff": proposal,
                "checks": outputs,
                "isolation": {
                    "network": "none",
                    "host_mounts": 0,
                    "read_only_root": True,
                    "memory_bytes": 256 * 1024 * 1024,
                    "cpu_cores": 0.5,
                    "pids": 32,
                    "wall_time_seconds": MAX_RUNTIME_SECONDS,
                },
            }
        except SandboxError:
            raise
        except RequestsTimeout:
            raise SandboxError(
                "sandbox_timeout", "The isolated code run exceeded its time limit"
            ) from None
        except docker.errors.DockerException:
            raise SandboxError(
                "sandbox_execution_failed", "The isolated code run failed safely"
            ) from None
        finally:
            if container is not None:
                try:
                    container.remove(force=True, v=True)
                except (docker.errors.DockerException, RequestsTimeout):
                    pass
            client.close()
