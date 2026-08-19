---
title: pole-instrument Technical Design
tags: [architecture, python, instrumentation]
links: [pole-python-monorepo, todo]
updated: 2026-08-19
sources: 0
---

# pole-instrument Technical Design

## Status and product boundary

Proposed for implementation. Automatic instrumentation ships as the separate PyPI
distribution `pole-instrument-python`, imported as `pole_instrument`, and depends on the
public API of `pole-client-python`. The compatibility client remains unchanged: its wheel
must not contain `sitecustomize.py`, `usercustomize.py`, `.pth` files, framework imports,
or automatic patches.

The console entry point is `pole-instrument`. The instrumentation core has no framework
dependency; adapter dependencies are isolated in extras. Version 1 begins with one
reference adapter, `httpx`, supporting HTTPX 0.27–0.28 sync and async clients. Server
instrumentation, streaming bodies, gRPC, Dubbo, Thrift, and frameworks not listed here
are unsupported in version 1.

## Configuration

Instrumentation is disabled by default. Configuration precedence is programmatic
arguments, then command-line options, then environment variables, then defaults.
`POLE_INSTRUMENT_ENABLED` is a boolean and defaults to false.
`POLE_INSTRUMENT_STRICT` is a boolean and defaults to false. The enabled adapter list
defaults to all installed supported adapters and may be set with
`POLE_INSTRUMENT_ADAPTERS=httpx`. Boolean values are exactly `true`, `false`, `1`, or `0`
after ASCII case folding; adapter names are ASCII lowercase tokens.

Invalid values and unknown adapters produce an `invalid_configuration` diagnostic.
The programmatic API raises `ConfigurationError`; the launcher exits with status 2.
No framework patch or Sidecar session is created after invalid configuration. Strict
mode affects request-time failures only and does not make invalid configuration valid.

## Activation lifecycle

`pole_instrument.activate(config=None)` is explicit and idempotent: repeated calls with
equivalent configuration return the existing `Instrumentation` handle; conflicting
configuration raises `AlreadyActiveError`. Activation after framework import is
supported by patching the documented public HTTPX client send methods. Activation
before import registers a bounded import callback and installs the same patch once.

The handle's `shutdown()` is idempotent. It closes the owned `SidecarSession`, removes
the import callback, and performs unpatch only when the currently installed method is
the wrapper owned by that handle. Existing user wrappers and callbacks are preserved.
The console launcher activates immediately before invoking the target module or command
and always shuts down in `finally`. No session, channel, or worker thread is created at
module import time.

## Outbound HTTPX sequence

The wrapper reads the original request URL before mutation. A DNS host supplies service;
the optional `x-pole-target-namespace` request extension supplies namespace and defaults
to `default`. IP literals, missing hosts, and values rejected by `TargetService` mean
that no target can be derived. The adapter must not guess a target or listener port.

For a valid target, the wrapper obtains one snapshot, reads
`ListenerSnapshot.generation`, calls
`SidecarSession.endpoint(ListenerProtocol.HTTP)`, constructs public
`TargetService(namespace, service)`, and calls `TargetService.to_metadata()` with the
request headers. That public call incorporates `current_traffic_context()` and replaces
forged reserved metadata. The request authority and Host header continue to identify the
original service while the network connection is made to the Sidecar listener. Calls
made by the instrumentation package set a context-local recursion guard.

The adapter maintains a transport pool per snapshot generation. Before acquiring a
connection it compares the current `ListenerSnapshot.generation`; a change closes and
discards the old pool atomically. `SidecarUnavailableError` also invalidates the pool
before fallback. A request that has observed invalidation may not acquire from the old
generation. In-flight requests may complete on the generation they already acquired.

## Failure policy

Default request behavior is fail open: missing socket, initialization timeout, invalid
snapshot, stream loss, unavailable endpoint, target derivation failure, metadata
validation failure, or adapter exception invokes the original HTTPX transport against
the original URL without Pole target headers. Fallback has a bounded deadline and never
waits for Sidecar reconnection. In strict mode the same conditions raise a documented
`InstrumentationUnavailableError` before application I/O. Registration rejection is
not applicable to the version 1 client-only adapter. Diagnostics identify the category
and adapter but never include URL userinfo, target values, headers, baggage, or bodies.

## Patch coexistence and concurrency

Wrappers retain and invoke the method present at activation, so user middleware remains
in the chain exactly once. Ownership markers prevent duplicate and recursive patches.
OpenTelemetry may be installed before or after Pole; Pole changes routing headers only,
does not create spans, and preserves its wrapper so both instrumentations execute once.
Unsupported HTTPX versions remain unpatched and report `unsupported_version`.

`SidecarSession` and sync pools are protected by process-local locks. Async pools belong
to the event loop that created them and are never shared across loops. Activation calls
`os.register_at_fork`: the child clears inherited session, lock, pool, and activation
state without joining parent threads, then lazily creates a new session on its first
instrumented request. Preload servers must activate in a post-fork worker hook when one
is available. Shutdown closes sync resources and schedules async cleanup on the owning
loop; if that event loop is already closed, resources are abandoned with one diagnostic
rather than blocking process exit.

## Diagnostics, security, and compatibility

`pole_instrument.activation_status()` returns an immutable snapshot containing enabled,
strict, installed adapter names, patch errors, Sidecar availability, and generation. It
contains no request data. Structured diagnostics use stable event names, apply a rate
limit of one event per category per minute with a suppressed count, and never record
credentials, target metadata, baggage, arbitrary headers, URL query/userinfo, or request bodies.
Bootstrap connects only to the configured local Unix Domain Socket accepted by
`SidecarSession`; remote bootstrap transports are not added.

Both distributions support Python 3.9–3.13. `pole-client-python` retains its current
exports, dependencies, contracts, and artifact contents. The new distribution depends
on a compatible client range and exposes HTTPX only through `[httpx]`; importing core
with no framework or OpenTelemetry installed succeeds. Release gates inspect both
artifacts and install client-only, instrument-core, and adapter-extra environments.

## Verification plan

- Document-contract tests require every settled decision and this traceability table.
- Activation tests cover disabled import, configuration precedence/errors, idempotency,
  before/after-import patching, coexistence, recursion, and owned unpatch.
- A real HTTPX request plus real UDS Sidecar fixture covers endpoint routing, canonical
  target/TrafficContext metadata, fail open, strict mode, stream loss, and generation
  pool replacement for sync and async clients.
- Lifecycle tests cover threads, two event loops, forked workers, repeated shutdown,
  redacted/rate-limited diagnostics, and secret sentinels.
- Compatibility gates run the existing Thin SDK suite unchanged, compile all sources,
  build both distributions, inspect artifacts, and smoke-test Python 3.9–3.13.

## Traceability

| Acceptance | Design section | Planned test |
| --- | --- | --- |
| AC1, AC13, AC14 | Product boundary; compatibility | artifact and isolated-import tests |
| AC2, AC3 | Configuration; activation lifecycle | activation/config table tests |
| AC4, AC5, AC6 | Outbound sequence; failure policy | real HTTPX plus UDS tests |
| AC7 | Product boundary | explicit v1 server non-goal assertion |
| AC8 | Product boundary | HTTPX support-matrix tests |
| AC9 | Patch coexistence | user/OpenTelemetry ordering tests |
| AC10 | Concurrency | fork, thread, and event-loop tests |
| AC11, AC12 | Diagnostics and security | status, rate limit, secret-sentinel tests |
| AC15 | Traceability | document-contract test |

## Open decisions

There are no open decisions required for the version 1 HTTPX client milestone. Adding
server adapters or another protocol requires a new design revision, support matrix, and
public-seam E2E evidence before implementation.
