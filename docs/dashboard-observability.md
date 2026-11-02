# Dashboard, observability, and analytics

The workflow dashboard now renders task dependencies as an accessible SVG DAG. Each task card shows its agent, capability, status, dependencies, protocol version, attempts, request ID, and error code. The SVG edges are decorative; task cards remain available to assistive technology as a labeled list.

## Live updates and recovery

- The browser opens the authenticated workflow SSE endpoint and consumes replayable `workflow` events. Native `EventSource` reconnects with its last event ID.
- REST polling remains active for workflow/task state and acts as the fallback when the stream is reconnecting or unavailable.
- Stream state is exposed through a polite live region. Workflow errors use an alert region, and task progress exposes progressbar values.

## Search and analytics

- The sidebar filters recent runs. The Workflow history destination searches the entire workspace by prompt or run ID, filters by status, and paginates in pages of 20. The API preserves its array response and supports bounded `q`, `status`, `offset`, and `limit` parameters. SQL wildcard characters in search text are escaped.
- Workspace analytics display workflow counts by status and recent worker heartbeats. The metrics endpoint applies workspace membership checks.
- Recovery rate is based on workflows with pause history and a resume record; it remains unavailable until there is an eligible paused workflow.

## Acceptance notes

The current frontend build verifies TypeScript and production bundling. The repository does not yet have browser E2E coverage for keyboard navigation, an interrupted/reconnected SSE stream, or rendering dependency branches. Manual browser acceptance on 2 November 2026 verified keyboard activation, a three-node execution graph, live events, and automatic reconnect after an API process interruption. Automated browser E2E coverage remains a separate enhancement. Search now covers older workspace records; PostgreSQL and SQLite tests verify paging, literal wildcard searches, and workspace isolation. Same-origin SSE URLs are resolved against the browser origin.
