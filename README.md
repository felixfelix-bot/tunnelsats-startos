# TunnelSats for StartOS

<img src="https://raw.githubusercontent.com/Tunnelsats/tunnelsats/ffb4732328045922dc90eb5580654077e8d3f246/images/brand/logos/ts_logo_rectangle.svg" alt="TunnelSats Logo" width="400"/>

A privacy-focused companion package and routing guide for Lightning Network nodes (LND and Core Lightning) on StartOS.

## Table of Contents

- [Overview](#overview)
- [Quick Reference for AI Consumers](#quick-reference-for-ai-consumers)
- [Architecture & How It Works](#architecture--how-it-works)
- [Volumes & Mount Points](#volumes--mount-points)
- [Subcontainers](#subcontainers)
- [File Models](#file-models)
- [Actions & Tasks](#actions--tasks)
- [Network & Privacy Disclosure](#network--privacy-disclosure)
- [Development & Testing](#development--testing)
- [License](#license)

## Overview

TunnelSats provides dedicated WireGuard VPN infrastructure specifically designed for Lightning Network nodes. On StartOS, WireGuard encapsulation and outbound policy routing are managed natively at the host OS level (**System > Gateways**). When properly connected and assigned as the outbound gateway on the target Lightning node, it enables clearnet inbound connectivity and routes outbound peer traffic through the VPN.

> [!NOTE]
> **Host-Managed Gateway Architecture**: WireGuard network tunnels and policy routes are configured and managed externally by the user at the StartOS host level. This companion package provides:
> - A responsive Web Dashboard (port 80) displaying connection properties, expiration countdowns, and routing guides.
> - Automated StartOS tasks for 1-Click Lightning external host advertisement (`custom-external-host`).
> - Automated expiration alert tasks (7-day and 3-day warnings).
> - Periodic subscription metadata synchronization with `https://tunnelsats.com/api/public/v1/subscription/status`.
> - Package health checks validating Web UI availability and subscription validity (host gateway routing status is audited independently via StartOS Gateway settings).

## Quick Reference for AI Consumers

```yaml
package_id: tunnelsats
title: TunnelSats
description: A privacy-focused VPN gateway for Lightning Nodes (LND/CLN).
architecture:
  model: host-managed gateway companion
  ui_port: 80
  telemetry_daemon: python3 bridge.py
  external_services:
    - https://tunnelsats.com/api/public/v1/subscription/status (subscription metadata sync)
volumes:
  - name: main
    path: /data
subcontainers:
  - name: main
    image: tunnelsats
actions:
  - id: configure
    name: Configure
tasks:
  - tunnelsats:configure (subscription expiry alert)
  - lnd:custom-external-host-config (1-click external host advertisement)
  - c-lightning:config (1-click external host advertisement)
```

## Architecture & How It Works

1. **Host-Managed Gateway**: The WireGuard tunnel is configured under StartOS **System > Gateways**. StartOS kernel networking encapsulates outbound Lightning traffic and forwards inbound connections on your TunnelSats port to port `9735` on your Lightning container.
2. **Companion Service**: The `tunnelsats` container runs as a companion service, hosting the Web Dashboard and maintaining synchronization with the TunnelSats subscription API.
3. **1-Click External Host Configuration**: Once configured, StartOS prompts the user with a 1-Click task to announce the TunnelSats public IP and port to the Lightning Network on LND or Core Lightning.
4. **Subscription Lifecycle & Renewal**: The background daemon monitors subscription expiration, updating the local dashboard and raising StartOS tasks when renewal is required.

## Volumes & Mount Points

| Volume Name | Container Path | Purpose |
|-------------|----------------|---------|
| `main`      | `/data`        | Stores `config.json`, `tunnelsatsv3.conf`, and synchronized metadata (`tunnelsats-meta.json`). |

## Subcontainers

| Subcontainer | Base Image | Entrypoint | Purpose |
|--------------|------------|------------|---------|
| `main`       | Debian Slim (Python 3) | `docker_entrypoint.sh` | Serves web UI on port 80 and runs the subscription synchronization daemon. |

## File Models

- **`config.json`**: Primary service configuration (`enabled`, `target-node`, `tunnelsats-conf`, `allow-ipv6`).
- **`tunnelsatsv3.conf`**: WireGuard configuration file written to disk when enabled.
- **`tunnelsats-meta.json`**: Cached subscription metadata (`expiresAt`, `lastSync`, `syncSuccess`, `serverDomain`, `vpnPort`).

## Actions & Tasks

- **Configure (`configure`)**: Allows users to enable/disable TunnelSats, select their target Lightning node (`lnd` or `cln`), paste their WireGuard configuration, and toggle IPv6 coexistence.
- **Automated Tasks**:
  - `tunnelsats:configure`: Raised when subscription has `<= 7 days` (Important) or `<= 3 days` / expired (Critical). Automatically cleared upon successful renewal.
  - `lnd:custom-external-host-config` / `c-lightning:config`: 1-Click task to populate `custom-external-host` on the target Lightning node.

## Network & Privacy Disclosure

- **Subscription Synchronization**: This package periodically queries `https://tunnelsats.com/api/public/v1/subscription/status` (via the background `subscription_sync_loop` in `bridge.py`) using your WireGuard public key to verify subscription validity, expiration date, and assigned port.
- **IPv4-Only Routing**: TunnelSats WireGuard tunnels route IPv4 traffic only. Under StartOS host gateway routing, IPv6 connections to your Lightning node are blackholed by default to prevent leaking residential ISP IP addresses. If you enable **Allow Home IPv6 Coexistence**, raw IPv6 traffic bypasses the VPN.
- **Egress Health Probe (fail-closed detection)**: The companion service runs a recurring outbound egress probe (`egress_probe_loop` in `bridge.py`) that calls `https://api.ipify.org` to learn the public IPv4 address actually used for outbound traffic, and compares it against the tunnel server IP parsed from the WireGuard `Endpoint` in your config. The classification has three states: `tunneled` (confirmed match), `not_tunneled` (confirmed mismatch — the explicit **"outbound not tunneled"** warning), and `unverified` (the probe could not establish the outbound IP, e.g. `api.ipify.org` unreachable or the `Endpoint` hostname did not resolve). `unverified` is **not** a healthy claim: the `bridge.py health egress` command fails closed (exit 1) for both `not_tunneled` and `unverified`, and the Web Dashboard shows an amber `OUTBOUND NOT TUNNELED` badge/banner or an `EGRESS UNVERIFIED` badge accordingly. `/api/status` reads the cached probe result and never blocks on the network — with no result yet it reports `unverified` with `pending: true`, and the background loop refreshes it. Like the pre-existing `verify.sh` audit, this comparison assumes the TunnelSats server uses the same address for its WireGuard listener and its outbound NAT; if that ever changes, a correctly tunneled user would be reported as `not_tunneled`. This is *detection only* — the service never reconfigures host routing. Outbound tunneling is enabled manually under StartOS host **System > Gateways** and is fail-open by default, so this probe makes the gap visible rather than enforcing it. The probe runs every 5 minutes and performs one short (≤10 s) HTTPS request to `api.ipify.org` per run.

## Development & Testing

```bash
# Run all tests (TypeScript, Python, BATS)
npm run test:all

# Typecheck and build bundle
npm run check && npm run build
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
