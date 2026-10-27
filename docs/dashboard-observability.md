# Dashboard, observability, and analytics

The workflow dashboard now renders task dependencies as an accessible SVG DAG. Each task card shows its agent, capability, status, dependencies, protocol version, attempts, request ID, and error code. The SVG edges are decorative; task cards remain available to assistive technology as a labeled list.

## Live updates and recovery

- The browser opens the authenticated workflow SSE endpoint and consumes replayable `workflow` events. Native `EventSource` reconnects with its last event ID.
- REST polling remains active for workflow/task state and acts as the fallback when the stream is reconnecting or unavailable.
- Stream state is exposed through a polite live region. Workflow errors use an alert region, and task progress exposes progressbar values.

## Search and analytics

- The sidebar can filter the latest 50 workspace workflows by prompt text or status.
- Workspace analytics display workflow counts by status and recent worker heartbeats. The metrics endpoint applies workspace membership checks.
- Recovery rate is based on workflows with pause history and a resume record; it remains unavailable until there is an eligible paused workflow.

## Acceptance notes

The current frontend build verifies TypeScript and production bundling. The repository does not yet have browser E2E coverage for keyboard navigation, an interrupted/reconnected SSE stream, or rendering dependency branches. Those manual and automated acceptance checks remain part of completing the handbook checkpoint. The built-in workflow list is capped at 50, so search does not cover older records yet.
