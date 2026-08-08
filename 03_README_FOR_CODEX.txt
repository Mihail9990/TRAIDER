# README_FOR_CODEX

# AI Impulse Trader

## Official Entry Point for Codex

This document must be read BEFORE any implementation.

------------------------------------------------------------------------

## Reading Order

1.  README_FOR_CODEX.md
2.  OFFICIAL_SUPPLEMENTS
3.  Main Documentation

If there is any conflict: - OFFICIAL_SUPPLEMENTS have highest
priority. - Main documentation is second. - Existing code is last.

------------------------------------------------------------------------

## Project Goal

Implement the AI Impulse Trader project exactly as described.

Do NOT simplify the architecture.

Do NOT change the Formula Engine.

Do NOT move business logic into the Broker API.

Do NOT violate System Invariants.

------------------------------------------------------------------------

## Development Order

1.  Foundation
2.  Models
3.  Event Bus
4.  Core Managers
5.  Formula Engine
6.  Broker Layer
7.  Strategy
8.  Recovery
9.  Notifications
10. Tests
11. Final Integration

Finish each stage before starting the next.

------------------------------------------------------------------------

## Mandatory Rules

-   Broker confirmation is the only source of truth.
-   Formula Engine is the only module allowed to calculate strategy
    values.
-   Recovery Manager never makes trading decisions.
-   Every module has exactly one responsibility.
-   All public methods must be logged and tested.

------------------------------------------------------------------------

## Before Completing Any Module

Verify:

-   Documentation followed.
-   Logging implemented.
-   Error handling implemented.
-   Tests added.
-   Acceptance Criteria satisfied.

------------------------------------------------------------------------

## Before Completing Entire Project

Verify:

-   No TODO markers.
-   No FIXME markers.
-   No stub implementations.
-   No cyclic dependencies.
-   All OFFICIAL_SUPPLEMENTS implemented.
-   Scenarios 1-9 implemented.
-   Recovery tested.
-   Documentation and code are synchronized.

------------------------------------------------------------------------

## Final Rule

When code conflicts with documentation, documentation is authoritative.
