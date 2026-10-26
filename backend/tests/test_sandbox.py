from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from requests.exceptions import ReadTimeout

from app.sandbox import (
    _RUN_SLOTS,
    CHECK_COMMANDS,
    MAX_RUNTIME_SECONDS,
    SandboxController,
    SandboxError,
    SandboxRunRequest,
    sandbox_enabled,
    sandbox_runtime_configured,
    validate_project_path,
)


def request(checks=None):
    return SandboxRunRequest(
        workspace_id="workspace-id",
        files={"src/add.py": "def add(a, b):\n    return a + b\n"},
        patch={"src/add.py": "def add(a, b):\n    return a + b + 0\n"},
        checks=checks or ["pytest", "compileall"],
    )


class FakeContainer:
    def __init__(self, exit_codes=None):
        self.exit_codes = exit_codes or [0, 0]
        self.commands = []
        self.removed = False

    def start(self):
        self.started = True

    def put_archive(self, path, data):
        self.archive_path = path
        self.archive = data
        return True

    def exec_run(self, **kwargs):
        self.commands.append(kwargs)
        return SimpleNamespace(
            exit_code=self.exit_codes[len(self.commands) - 1],
            output=(b"check output", b""),
        )

    def remove(self, **kwargs):
        self.removed = True


class FakeClient:
    def __init__(self, container):
        self.container = container
        self.api = SimpleNamespace(timeout=None)
        self.config = None
        self.closed = False
        self.images = SimpleNamespace(
            get=lambda image: SimpleNamespace(
                id="sha256:verified-image",
                attrs={"Config": {"Labels": {"org.orqestra.sandbox": "checkpoint-10"}}},
            )
        )
        self.containers = SimpleNamespace(create=self.create)

    def info(self):
        return {"CgroupVersion": "2"}

    def create(self, **kwargs):
        self.config = kwargs
        return self.container

    def close(self):
        self.closed = True


def test_sandbox_runtime_is_disabled_until_an_operator_configures_a_dedicated_daemon(tmp_path):
    assert not sandbox_enabled({})
    assert not sandbox_runtime_configured(
        {"SANDBOX_ENABLED": "false", "DOCKER_HOST": "tcp://sandbox:2376"}
    )
    certificate_dir = tmp_path / "certs"
    certificate_dir.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (certificate_dir / name).write_text("fixture", encoding="utf-8")
    assert sandbox_runtime_configured(
        {
            "SANDBOX_ENABLED": "true",
            "DOCKER_HOST": "tcp://sandbox:2376",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": str(certificate_dir),
        }
    )
    assert not sandbox_runtime_configured(
        {"SANDBOX_ENABLED": "true", "DOCKER_HOST": "tcp://sandbox:2376"}
    )


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../secret.py", "src/../escape.py", "C:/temp.py", "src\\x.py", "./main.py"],
)
def test_project_paths_cannot_escape_the_in_memory_workspace(path):
    with pytest.raises(ValueError):
        validate_project_path(path)


def test_request_rejects_shell_checks_and_oversized_patch_files():
    with pytest.raises(ValidationError):
        SandboxRunRequest(
            workspace_id="workspace-id",
            files={"main.py": "pass"},
            patch={"main.py": "pass"},
            checks=["pytest; cat /etc/passwd"],
        )
    with pytest.raises(ValidationError):
        SandboxRunRequest(
            workspace_id="workspace-id",
            files={"main.py": "pass"},
            patch={"main.py": "x" * (256 * 1024 + 1)},
            checks=["pytest"],
        )


def test_controller_uses_fixed_commands_ephemeral_isolation_caps_and_cleans_up():
    container = FakeContainer()
    client = FakeClient(container)
    controller = SandboxController(client_factory=lambda **kwargs: client)
    result = controller.run(request())

    assert result["status"] == "completed"
    assert result["image_id"] == "sha256:verified-image"
    assert "-" in result["diff"] and "+" in result["diff"]
    assert [command["cmd"] for command in container.commands] == [
        CHECK_COMMANDS["pytest"],
        CHECK_COMMANDS["compileall"],
    ]
    config = client.config
    assert config["network_mode"] == "none"
    assert config["read_only"] is True
    assert config["privileged"] is False
    assert config["cap_drop"] == ["ALL"]
    assert config["security_opt"] == ["no-new-privileges:true"]
    assert config["mem_limit"] == "256m"
    assert config["nano_cpus"] == 500_000_000
    assert config["pids_limit"] == 32
    assert config["tmpfs"]["/workspace"].startswith("rw,noexec,nosuid,nodev")
    assert "volumes" not in config
    assert "DOCKER_HOST" not in config["environment"]
    assert container.archive_path == "/workspace"
    assert container.removed and client.closed
    assert result["isolation"]["host_mounts"] == 0
    assert result["isolation"]["wall_time_seconds"] == MAX_RUNTIME_SECONDS


def test_controller_reports_failed_checks_but_always_removes_the_container():
    container = FakeContainer(exit_codes=[1])
    client = FakeClient(container)
    result = SandboxController(client_factory=lambda **kwargs: client).run(request(["pytest"]))
    assert result["status"] == "failed"
    assert result["checks"][0]["status"] == "failed"
    assert result["checks"][0]["exit_code"] == 1
    assert container.removed


def test_controller_kills_container_after_runtime_timeout():
    class TimeoutContainer(FakeContainer):
        def exec_run(self, **kwargs):
            raise ReadTimeout("mock timeout")

    container = TimeoutContainer()
    client = FakeClient(container)
    result = SandboxController(client_factory=lambda **kwargs: client).run(request(["pytest"]))
    assert result["status"] == "timed_out"
    assert result["checks"][0]["status"] == "timed_out"
    assert container.removed and client.closed


def test_controller_rejects_excess_concurrency_before_creating_a_container():
    client_created = False

    def factory(**kwargs):
        nonlocal client_created
        client_created = True
        return FakeClient(FakeContainer())

    assert _RUN_SLOTS.acquire(blocking=False)
    assert _RUN_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(SandboxError) as error:
            SandboxController(client_factory=factory).run(request(["pytest"]))
    finally:
        _RUN_SLOTS.release()
        _RUN_SLOTS.release()
    assert error.value.code == "sandbox_busy"
    assert not client_created


def test_controller_rejects_unlabelled_or_missing_runtime_images():
    class BadImageClient(FakeClient):
        def __init__(self):
            super().__init__(FakeContainer())
            self.images.get = lambda image: SimpleNamespace(attrs={"Config": {"Labels": {}}})

    client = BadImageClient()
    with pytest.raises(SandboxError) as error:
        SandboxController(client_factory=lambda **kwargs: client).run(request(["pytest"]))
    assert error.value.code == "sandbox_image_invalid"
    assert client.closed


def test_sandbox_api_persists_diff_and_checks_workspace_membership(
    api_client, accounts, monkeypatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "sandbox_runtime_configured", lambda: True)

    class FakeController:
        def run(self, payload):
            return {
                "status": "completed",
                "image": "orqestra-sandbox:0.1.0",
                "runtime_seconds": 0.1,
                "diff": "--- a/main.py\n+++ b/main.py\n",
                "checks": [{"check": "pytest", "status": "passed", "exit_code": 0}],
                "isolation": {"network": "none", "host_mounts": 0},
            }

    monkeypatch.setattr(main_module, "SandboxController", FakeController)
    owner = accounts("sandbox-owner@example.com")
    viewer = accounts("sandbox-viewer@example.com")
    outsider = accounts("sandbox-outsider@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Code review"}).json()["id"]
    membership = owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "sandbox-viewer@example.com", "role": "viewer"},
    )
    assert membership.status_code == 200

    response = owner.post(
        "/api/v1/sandbox/run",
        json={
            "workspace_id": workspace,
            "files": {"main.py": "print('base')\n"},
            "patch": {"main.py": "print('proposed')\n"},
            "checks": ["pytest"],
        },
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    assert viewer.get(f"/api/v1/sandbox/runs/{run_id}").status_code == 200
    assert outsider.get(f"/api/v1/sandbox/runs/{run_id}").status_code == 404
    denied = viewer.post(
        "/api/v1/sandbox/run",
        json={
            "workspace_id": workspace,
            "files": {"main.py": "pass"},
            "patch": {"main.py": "pass"},
            "checks": ["pytest"],
        },
    )
    assert denied.status_code == 403


def test_sandbox_api_rejects_requests_when_runtime_is_disabled(api_client, accounts, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "sandbox_runtime_configured", lambda: False)
    owner = accounts("sandbox-disabled@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Disabled sandbox"}).json()["id"]
    response = owner.post(
        "/api/v1/sandbox/run",
        json={
            "workspace_id": workspace,
            "files": {"main.py": "pass"},
            "patch": {"main.py": "pass"},
            "checks": ["pytest"],
        },
    )
    assert response.status_code == 503
