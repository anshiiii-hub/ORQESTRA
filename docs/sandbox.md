# Code proposal sandbox

Checkpoint 10 adds a code patch review path. A workspace member submits a small baseline source snapshot, proposed replacement files, and one or more fixed Python checks. The controller returns a unified diff and bounded test output and stores the result in the same workspace. It does not mount the repository, host filesystem, Docker socket, or application credentials into the code container.

## Runtime setup

Execution is disabled by default. Build the pinned runtime image from the repository root:

```sh
docker build -f backend/sandbox/Dockerfile -t orqestra-sandbox:0.1.0 backend
```

The API must reach a **dedicated sandbox Docker Engine** over TLS. Do not point it at a production or shared daemon: Docker daemon access is highly privileged. Configure `SANDBOX_ENABLED=true`, `SANDBOX_DOCKER_HOST=tcp://...`, `SANDBOX_DOCKER_TLS_VERIFY=1`, and `SANDBOX_DOCKER_CERT_PATH` to a directory containing `ca.pem`, `cert.pem`, and `key.pem`. Mount those client certificates read-only into the API service through a local Compose override. The certificates are used by the trusted API controller only and are never passed into sandbox containers. Make the prebuilt `orqestra-sandbox:0.1.0` image available on that daemon. The controller does not pull images at run time.

The default Compose setup does not mount a Docker socket and leaves the sandbox disabled. A daemon must report cgroup v2 support; the controller rejects hosts that cannot report it. Docker’s resource flags depend on host kernel support, so verify the daemon’s resource controls before enabling execution.

## API

- `GET /api/v1/sandbox/config` reports runtime availability and execution bounds.
- `POST /api/v1/sandbox/run` accepts `workspace_id`, baseline `files`, replacement `patch`, and a list of fixed check IDs: `pytest`, `unittest`, and `compileall`.
- `GET /api/v1/sandbox/runs/{id}` returns a stored diff and test artifact only to a workspace member.

Owners and members may submit runs. Viewers may inspect completed artifacts. The API caps the total source and patch text at 1 MiB, baseline files at 30, patch files at 20, and each file at 256 KiB. File paths must be normalized relative paths; parent traversal, absolute paths, shell commands, and arbitrary command arguments are rejected.

## Container boundary

For each run, the controller creates a new labeled runtime container, copies source files through an in-memory archive into a 64 MiB tmpfs, applies the patch there, executes only a mapped Python check command, and force-removes the container in a `finally` block. The container has no mounts, no network, no devices, a read-only image filesystem, a 16 MiB temporary filesystem, UID/GID 10001, all capabilities dropped, `no-new-privileges`, a 256 MiB memory and swap cap, 0.5 CPU, a 32-process cap, file/CPU ulimits, and a 35-second controller deadline. Standard output and error are each truncated to 16 KiB.

The result records the review diff, selected checks, exit statuses, bounded output, image identity, and configured isolation limits. The baseline source snapshot is not persisted; the proposed diff and execution artifact are.

## Verification and limits

Tests mock the Docker API to assert container options, fixed command mapping, path validation, access control, diff persistence, timeout bounds, and forced cleanup. This environment has no usable Docker daemon, so it cannot perform a live hostile-code escape or resource-exhaustion test. The optional runtime must receive those tests on its dedicated host before it is enabled. Docker containers share the host kernel; this prototype is not a substitute for a stronger VM or gVisor boundary for hostile multi-tenant workloads.
