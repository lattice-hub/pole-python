---
title: pole-instrument Technical Design
tags: [architecture, python, instrumentation, sidecar]
links: [pole-python-monorepo]
updated: 2026-08-20
sources: 4
---

# pole-instrument Technical Design

## Status and decisions

Proposed for the first implementation increment. This document defines the runtime and packaging
contract; it does not add executable instrumentation.

Decisions made now:

- Publish a separate `pole-instrument` distribution with import package `pole_instrument`, console
  entry point `pole-instrument`, and explicit API `pole_instrument.activate()`.
- Support HTTPX 0.27–0.28 (sync and async request APIs) and gRPC Python 1.71–1.x (sync unary calls)
  initially. Defer gRPC async/streaming, Dubbo, and Thrift automatic adapters.
- Activation is explicit. Installing either distribution never starts a session or patches a
  framework. `sitecustomize.py`, `usercustomize.py`, `.pth` activation, and implicit import hooks are
  prohibited.
- Targeted calls fail closed when a valid Sidecar endpoint cannot be obtained. Untargeted calls are
  left unchanged. Activation itself is fail open by default after a bounded diagnostic, with a
  strict option for deployments that require readiness.
- A live `SidecarSession` belongs to one worker process and must never cross `fork()`.

Open questions for a later ADR are the target declaration syntax for framework integrations beyond
the initial APIs, Dubbo/Thrift client selection, and whether gRPC async/streaming can preserve all
call semantics without private framework hooks.

## Goals and non-goals

Goals are explicit opt-in instrumentation, reuse of the stable Thin SDK seam, deterministic routing,
safe recovery, reversible narrowly scoped adapters, and diagnostics that do not disclose request
content. The initial release supports Python 3.9–3.13, matching `pole-client-python`.

Non-goals are server instrumentation, target discovery, remote Sidecar configuration, protocol
contract changes, tracing correlation through `traceparent`, automatic application target guessing,
and blanket support for every client that speaks HTTP, gRPC, Dubbo, or Thrift.

## Distribution and dependency boundaries

`pole-instrument` depends on a compatible `pole-client-python` minor series and exposes framework
extras: `pole-instrument[httpx]` and `pole-instrument[grpc]`. Its base install contains the activation,
registry, lifecycle, configuration, and diagnostic surfaces but no framework client dependency.
The monorepo releases both distributions independently; compatibility metadata records supported
Python, framework, Thin SDK, and Sidecar contract versions. Incompatible versions are diagnosed and
their adapter is not installed.

The existing `pole-client-python` distribution, `pole_client` import path, exports, dependency set,
and explicit-call behavior remain unchanged. Its wheel must continue to exclude launchers,
instrumentation modules, startup files, and framework monkey patches.

## Startup and activation semantics

The launcher form is:

```text
pole-instrument [instrument options] -- python -m application [application arguments]
```

The launcher parses only arguments before `--`; it passes the remaining argument vector and
environment to the child unchanged and never logs them. The API form calls `activate(config)` before
the application creates instrumented clients.

Configuration precedence is explicit API values, `POLE_INSTRUMENT_*` environment variables, then
documented defaults. Unknown keys and invalid values fail activation before patching. Secrets are
not accepted as instrumentation configuration.

Activation performs these steps in order:

1. Validate configuration, process identity, Python/framework compatibility, and requested adapters.
2. Start one worker-owned `SidecarSession` with bounded initialization.
3. Install requested adapters in the registry.
4. Import or execute the application only after successful launcher activation.
5. Register shutdown callbacks after all owned resources exist.

`activate()` is idempotent for an identical effective configuration and returns the existing runtime.
A conflicting second activation raises `AlreadyActivatedError` without modifying the active runtime.
Each adapter installation uses an ownership marker and cannot double-wrap a callable.

Framework modules imported before API activation are supported only where the adapter can replace a
documented public method safely. Otherwise activation skips that adapter with
`framework_imported_too_early`; strict mode raises before any partial installation. The launcher is
the recommended path because it activates before application import.

## Existing public seam and request flow

The runtime owns, but does not reimplement, `pole_client.SidecarSession`. An outbound adapter reads a
fresh `listener_snapshot()` for each new pooled transport and records its generation. The protocol
address comes only from `SidecarSession.endpoint(ListenerProtocol...)`; there is no fallback business
listener, remote override, or cached direct destination.

Target identity is an explicit immutable adapter argument or request extension containing
`namespace` and `service`. Missing or ambiguous identity leaves an untargeted call untouched; APIs
declared as targeted raise `MissingTargetError`. Adapters never infer a target from a URL hostname,
authority, method name, tracing fields, or application package.

At the final outbound request assembly point, adapters construct `TargetService(namespace, service)`
and call `to_metadata()` for HTTP or `to_grpc_metadata()` for gRPC. The optional explicit
`TrafficContext` is passed at that same point; otherwise the Thin SDK reads the current scope.
Consequently caller metadata is preserved except for canonical replacement of reserved target keys
and cleanup/replacement of reserved TrafficContext baggage. `traceparent` remains unrelated data.
No adapter imports generated protobuf modules or depends on private `pole_client` state.

## Adapter contract and support matrix

| Protocol / adapter | Initial status and versions | Interception and target source | Semantics and exclusions |
|---|---|---|---|
| HTTP / HTTPX | Supported: HTTPX 0.27–0.28, sync and async | Public `Client.send` / `AsyncClient.send`; explicit request extension or bound client target | Rewrites scheme/authority to the Sidecar HTTP endpoint at send time and uses `TargetService.to_metadata`. Redirect hops require an explicit retained target. Existing client pools are partitioned by snapshot generation and closed on invalidation. Custom transports are unsupported unless they opt into the adapter wrapper. |
| gRPC Python | Supported: grpcio 1.71–1.x, sync unary-unary only | Public client interceptor; explicit target bound when constructing the instrumented channel | Creates the channel against the Sidecar gRPC endpoint and injects `to_grpc_metadata`. User retry policy is retained. Async, client/server/bidi streaming, and user-supplied existing channels are rejected as unsupported, not partially patched. |
| Dubbo | Deferred | No initial client API selected | `ListenerProtocol.DUBBO` availability does not imply framework support. No patching or routing claim. |
| Thrift | Deferred | No initial client API selected | `ListenerProtocol.THRIFT` availability does not imply framework support. No patching or routing claim. |

HTTPX uninstrumentation restores only methods owned by this runtime when no later patch has replaced
them. gRPC interceptors are attached to newly created instrumented channels, so shutdown closes only
owned channels. Retries reuse canonical metadata but perform endpoint availability/generation checks
before opening a new transport. Unsupported streaming and custom-transport cases fail at adapter
construction, before a request can be rerouted incorrectly.

## Failure and recovery semantics

Activation defaults to fail open: if the socket is absent, initialization times out, or the first
snapshot is invalid, no adapters remain installed and the application starts with an error diagnostic.
Strict activation fails closed and does not start the application. Rollback of partial activation is
reverse ordered and closes the session.

Routing is stricter. Once a request is explicitly targeted, `SidecarUnavailableError`, an invalid
snapshot, or adapter failure raises a targeted-routing error. It must never fall back to the original
destination. An in-flight request already accepted by the local Sidecar follows the underlying client
result; instrumentation does not replay it automatically.

On control-stream disconnect, the Thin SDK invalidates the snapshot. The next targeted call fails
before acquiring a stale pooled connection. On snapshot-generation change, adapters atomically mark
all older-generation pools/channels draining, prevent new acquisition from them, close them when
their current users release them, and create transports using the new endpoint. Reconnection is
exclusively the existing `SidecarSession` lifecycle.

An adapter error affects only calls using that adapter. It is reported with a stable reason code and
does not disable unrelated adapters or mutate untargeted requests.

## Process, concurrency, and fork lifecycle

Activation records the owning PID. The launcher activates inside the final application worker. A
Gunicorn-style master must use a worker post-fork hook or application factory; activation in the
master is rejected with `prefork_parent_unsupported`. If a fork occurs after activation, the child
detects the PID mismatch before the next adapter operation, discards inherited locks/channels without
using them, and requires child reactivation. The parent retains ownership of its resources.

Registry and generation transitions are protected by locks, while request-specific target and
`TrafficContext` data remains in arguments and the Thin SDK's `contextvars`/OTel storage. Threads and
asyncio tasks therefore do not share mutable request context. Nested scopes retain the existing
restore behavior.

Shutdown is idempotent: stop accepting new targeted calls, remove owned patches, close owned client
resources, then close `SidecarSession`. Each close is bounded; interpreter shutdown is not held open
by non-daemon instrumentation threads.

## Diagnostics and security

`pole_instrument.status()` returns immutable activation state, PID, enabled adapter names and
versions, Sidecar availability, current snapshot generation, and the last stable reason code. Logs
use the same reason codes for activation, compatibility decisions, generation changes, skipped
adapters, and shutdown.

By default diagnostics never include target namespace/service values, baggage, credentials, request
headers, URLs with query strings, application arguments, or environment contents. Debug mode may
include framework method names and numeric generations but not request data. Metrics use bounded
adapter and reason-code labels only.

Only validated loopback endpoints returned by `SidecarSession` are trusted. Caller-supplied endpoint
overrides are not part of the API. Canonical Thin SDK metadata assembly defeats case-variant forged
target fields and removes stale reserved baggage. Patches are limited to documented public seams,
carry ownership markers, and are reversible when no third party has subsequently replaced them.

## Compatibility, rollout, and rollback

Users who install only `pole-client-python`, or install `pole-instrument` without invoking its CLI/API,
observe no behavior change. Rollout is opt-in per adapter and supports `POLE_INSTRUMENT_ENABLED=0` as
an emergency disable switch below explicit API configuration. Operators can use one worker group as
a canary, inspect status/reason codes, then expand adoption.

Rollback disables activation or removes the separate distribution; no Thin SDK downgrade is required.
CI builds both distributions in isolation and checks their contents, entry points, optional dependency
resolution, Python matrix, framework matrix, and declared version compatibility. Release notes state
supported combinations and reject incompatible major contract versions.

Alternatives considered:

- Implicit `sitecustomize`/`.pth` activation was rejected because import order, process ownership,
  debugging, and rollback become nondeterministic.
- An explicit launcher/API was selected because it provides a testable lifecycle and a clean no-op
  path for existing users.
- Direct per-application framework integration remains valid for unsupported clients and is safer
  than pretending a generic adapter can preserve unknown transport semantics.

## Verification strategy

Every implementation behavior begins with a focused test at its public seam:

| Behavior | Smallest test | Broader evidence |
|---|---|---|
| Configuration and idempotent activation | Unit test `activate()` with precedence, duplicate, conflict, and unsupported-version cases | Launcher subprocess integration |
| HTTPX target routing | One sync and one async request through a recording public transport | Real HTTP listener, redirects/retries, pool-generation change |
| gRPC target routing | Unary public interceptor call against a recording server | Real UDS Sidecar fixture plus loopback gRPC listener |
| Metadata/context integrity | Assert adapter output through `TargetService` public methods | Existing conformance vectors and concurrent context isolation |
| Missing target | Public instrumented-client call without identity | Framework integration negative suite |
| Sidecar unavailable/disconnect | Public adapter call with a `SidecarSession` fixture whose stream closes | E2E proves no stale endpoint use and later-generation recovery |
| Fork ownership | PID-change unit test before adapter acquisition | Gunicorn-style master/worker subprocess test |
| Diagnostics/redaction | Public `status()` and captured log test with secret-shaped inputs | Packaging/integration log audit |
| Shutdown/uninstrumentation | Repeated public `close()` and method identity assertion | Worker termination timeout test |
| Packaging compatibility | Install each wheel in an isolated environment | Python 3.9–3.13 and supported framework CI matrix |

The public-seam E2E starts a real UDS gRPC `SidecarSessionService`, activates through the launcher or
public API, makes a supported client call, and observes the validated loopback endpoint plus canonical
TargetService and TrafficContext metadata. It then terminates the control stream, proves the next
targeted call cannot use the stale endpoint, publishes a new snapshot, and proves recovery uses the
new generation.

Test doubles are appropriate for configuration, registry, PID, diagnostic, and adapter assembly unit
tests. Availability, disconnect, reconnection, snapshot generation, and claimed framework routing
require real public-seam fixtures. Unsupported gRPC streaming, custom transports/channels, Dubbo,
Thrift, early-import incompatibility, double activation, and ambiguous identity remain mandatory
negative cases rather than untested implied support.

## Related pages

- [[pole-python-monorepo]]
