---
title: pole-instrument Technical Design
tags: [architecture, python, instrumentation, launcher]
links: [pole-python-monorepo, todo]
updated: 2026-08-18
sources: 4
---

# pole-instrument Technical Design

Status: Proposed. This document fixes the product boundary and runtime contracts that an implementation must satisfy. Shipping remains blocked on the open framework and failure-default decisions below.

## Scope and packaging boundary

`pole-instrument` is a future executable supplied by a separate `pole-instrument` distribution. Its implementation modules live under `pole_instrument` and depend on, but are not imported by, `pole-client-python`. The existing `pole_client` import, exports, version, and explicit-use behavior do not change. Installing or importing `pole_client` never activates instrumentation.

## Activation contract

The proposed command is `pole-instrument [launcher options] -- application [arguments...]`. The separator is mandatory. Argument forwarding preserves every application argument at the Python string boundary and does not log it. Environment variables are inherited except for explicit launcher configuration. Startup validates configuration, loads one selected adapter, installs it, and starts the application. Shutdown unpatches and closes launcher resources. Signals and the application's exit status are preserved. Exit codes are `2` for launcher syntax or validation and `70` for configured fail-closed activation failure.

### Adapter interface

An adapter exposes `install(runtime) -> handle`; the handle exposes idempotent `unpatch()`. The Adapter boundary applies outbound target metadata and TrafficContext baggage only at documented request hooks. No concrete framework adapter is approved or advertised.

## Existing SDK integration

The launcher creates at most one `SidecarSession` per live process and never duplicates UDS logic. Adapters request current endpoints, construct `TargetService` metadata, and use `TrafficContext` extraction, scope, and injection. A disconnect invalidates availability immediately; no stale endpoint may be cached or reused.

## Failure policy

The implementation exposes `fail-open` and `fail-closed`; the default remains an Open question.

| Failure | Fail-open | Fail-closed |
| --- | --- | --- |
| Launcher setup or validation | exit `2` | exit `2` |
| Sidecar unavailable | warn and run uninstrumented | error and exit `70` |
| Invalid listener snapshot | discard it, warn, run without route | error and exit `70` |
| Adapter import or patch | roll back partial patch and continue | roll back and exit `70` |
| Runtime callback | contain, warn, call original path | error and terminate only at a safe boundary |
| Sidecar disconnect | invalidate routes and continue | invalidate routes and request controlled termination |

## Patch lifecycle and Import order

Patching occurs after validation and before application import. Activation is idempotent for the same adapter/configuration; conflicts fail. Every patch registers its inverse, and partial patch failure rolls back in reverse order. Shutdown and test isolation call `unpatch()`; repeated cleanup is harmless. Library imports have no patch side effect.

## Process model

An `exec` requires activation in the replacement program. A subprocess is instrumented only when it opts into the launcher. A pre-fork parent may validate and import adapter code but owns no active thread or gRPC channel. Each post-fork child installs hooks and creates a fresh SidecarSession. Fork after activation is unsupported: the child disables inherited handles and emits a stable diagnostic until explicit reactivation.

## Configuration

Precedence is `CLI > environment > default`.

| Meaning | CLI | Environment | Default / validation |
| --- | --- | --- | --- |
| Adapter | `--adapter` | `POLE_INSTRUMENT_ADAPTER` | required registered name |
| Failure mode | `--failure-mode` | `POLE_INSTRUMENT_FAILURE_MODE` | unresolved; two fixed values |
| Socket | `--sidecar-socket` | `POLE_SIDECAR_SOCKET` | `resolve_sidecar_socket()` semantics |
| Level | `--log-level` | `POLE_INSTRUMENT_LOG_LEVEL` | `warning`; fixed levels |

Empty explicit values fail validation. Secrets, credentials, metadata, application arguments, and baggage values are not logged.

## Diagnostics

Diagnostics are bounded structured records on stderr with timestamp, severity, stable diagnostic ID, component, and redacted message. Categories cover activation, unsupported adapters, Sidecar availability, snapshots, patch failure, callbacks, fork misuse, and shutdown. Records redact secrets and carrier/baggage data and are rate-limited by ID.

## Compatibility and artifacts

The baseline is Python >=3.9. Optional dependencies are isolated by adapter extras or distributions and are not eagerly imported. A future wheel/sdist contains `pole_instrument` and its entry point. Existing `pole-client-python` artifacts still exclude `sitecustomize.py`, `usercustomize.py`, `.pth`, the launcher, adapter dependencies, and implicit patches.

## Verification matrix

| Contract | Required automated evidence before release |
| --- | --- |
| Launcher | argv, environment, streams, signals, and exit codes |
| Failure policy | every failure in both modes |
| Sidecar disconnect | real UDS test proving no stale endpoint reuse |
| Idempotency | duplicate activation, rollback, repeated unpatch |
| Propagation | public Adapter boundary target and baggage test |
| Fork | pre-fork/post-fork, spawn, subprocess, exec fresh-process tests |
| Diagnostics | IDs, severity, rate limiting, redaction |
| Package isolation | wheel/sdist and clean-environment tests |
| Compatibility | every advertised Python/framework pair |

## Non-goals

- Selecting framework-specific support.
- automatic sitecustomize activation or `.pth` activation.
- changes to pole_client public behavior.
- Instrumenting subprocesses without explicit opt-in.

## Open questions

- Which frameworks and versions are approved first?
- Is the default fail-open or fail-closed?
- Which server integrations offer a reliable post-fork callback?
- Is a public activation API needed besides the executable?

No production implementation ships until these are accepted and executable compatibility evidence exists.

## Related pages

- [[pole-python-monorepo]]
- [[todo]]
