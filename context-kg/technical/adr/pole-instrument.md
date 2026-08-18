---
title: pole-instrument Technical Design
tags: [architecture, python, instrumentation]
links: [pole-python-monorepo, todo]
updated: 2026-08-18
status: proposed
---

# pole-instrument Technical Design

## Status and scope

This proposal freezes the minimum runtime boundary needed to implement instrumentation without
changing the Thin SDK contracts. It intentionally approves no framework adapter yet: an adapter
can move to supported only after its framework, version range, target-selection API, and integration
tests are approved.

## Packaging boundary

- distribution: `pole-instrument`
- import package: `pole_instrument`
- console script: `pole-instrument`
- runtime dependency: a compatible `pole-client-python`

`pole-client-python` does not depend on `pole-instrument`. Its wheel retains no
`sitecustomize.py`, `usercustomize.py`, or `.pth` startup mechanism and importing `pole_client`
does not import or install instrumentation. The new distribution owns the launcher, lifecycle,
diagnostics, and optional framework dependencies.

## Launcher contract

The initial command grammar is:

```text
pole-instrument [--diagnostic-level LEVEL] -- COMMAND [ARG ...]
```

`LEVEL` is one of `error`, `warning`, or `info`, defaulting to `warning`. A missing separator,
missing command, invalid level, or nonexistent executable is rejected with exit status 2 and a
diagnostic on stderr before application startup.

After best-effort installation, the launcher replaces itself with the application through
`os.execvpe`. This preserves argv, environment, current directory, standard streams, signals, and
the application's exit status, and starts the application exactly once. Instrumentation setup is
fail-open: an installation or Sidecar error emits one bounded diagnostic and still executes the
application. An exec failure returns 127. Application exceptions and statuses are never rewritten.

## Runtime and adapter boundary

`pole_instrument.install()` returns a process-idempotent runtime handle. Repeated calls in the same
PID return the installed handle and do not add wrappers, control sessions, shutdown callbacks, or
metadata. `close()` is idempotent and releases only instrumentation-owned resources.

Each adapter implements `probe()`, `install(runtime)`, and `uninstall()`. It must retain original
public framework callables, install at most one wrapper layer, and restore only wrappers it owns.
Absent or incompatible optional frameworks are skipped fail-open. Adapters may not become required
dependencies of `pole-client-python`.

| Adapter | Supported versions | Status |
| --- | --- | --- |
| None | — | Not yet supported |

Therefore the proposed launcher currently preserves application execution but performs no request
patching. Adding the first adapter requires a follow-up decision and tests; this document does not
implicitly claim support for installed HTTP, gRPC, Dubbo, or Thrift libraries.

## Shared routing behavior

An approved adapter must accept an explicit `TargetService`; it never infers a target from a URL,
DNS name, route, or ambient state. Calls without a target use the framework's original destination
and carrier unchanged.

For targeted calls, shared runtime code:

1. asks its worker-owned `SidecarSession` for the listener endpoint for the request protocol;
2. calls `TargetService.to_metadata()` for HTTP-style headers or
   `TargetService.to_grpc_metadata()` for gRPC metadata;
3. lets those public methods read `current_traffic_context()` when no explicit context is supplied;
4. preserves unrelated caller fields while canonical target values replace forged values; and
5. uses only protocol-native HTTP headers, gRPC metadata, or future Dubbo attachments.

Adapters do not duplicate target encoding, baggage parsing, or Sidecar snapshot validation.
Context remains request-scoped through the existing `contextvars`/optional OpenTelemetry storage.

## Sidecar lifecycle and freshness

The runtime never invents or falls back to a business listener port. Until `SidecarSession` has a
valid initial snapshot, targeted routing is skipped fail-open. An adapter records the public
snapshot generation with every adapter-owned connection. On stream loss or
`SidecarUnavailableError`, it immediately discards that connection and does not reuse its endpoint.
After reconnection, a higher snapshot generation causes lazy creation of a connection to the newly
reported endpoint.

## Process and shutdown safety

Installation records the owning PID. `os.register_at_fork(after_in_child=...)` clears inherited
runtime handles, wrappers' ownership state, sessions, and connection pools in the child without
calling parent-owned gRPC objects. A pre-fork master may load modules but must install no live
session. Each worker installs its own runtime after fork. A later server-specific adapter must name
and test the exact worker hooks it uses.

One instrumentation-owned `atexit` callback closes the worker runtime. It does not close clients,
sessions, transports, or pools supplied by application code.

## Diagnostics and data safety

Diagnostics go to stderr as one-line records containing category, adapter (when applicable), and a
bounded reason. Categories are `invalid-launcher`, `adapter-unavailable`, `install-failed`,
`sidecar-unavailable`, and `sidecar-reconnected`. Identical runtime events are emitted once per
category/adapter/generation; a reconnection permits a later loss event.

Default diagnostics never include authorization values, baggage values, or payloads. They also do
not serialize arbitrary application headers, metadata, command environments, or exception locals.

## Verification required for implementation

Implementation is accepted only with launcher subprocess tests, install/close/fork idempotence
tests, clean-wheel import isolation, existing Thin SDK regression tests, and—once an adapter is
approved—a real framework plus local UDS Sidecar integration test covering disconnect and a changed
snapshot generation. The supported matrix above is the sole source of framework claims.

## Consequences

The compatibility package and frozen Sidecar Session, TargetService v1, and TrafficContext v1 wire
contracts remain unchanged. This decision resolves package ownership, launcher syntax, stderr
diagnostics, and generic process lifecycle. It deliberately leaves framework selection and its
target-selection API to a separately approved change.

## Related pages

- [[pole-python-monorepo]]
- [[todo]]
