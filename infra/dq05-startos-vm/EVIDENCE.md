# T3a — DQ05 KVM stack + StartOS test VM: Evidence

Task `t_9ac92fca`, infra task. Gates 1 (TDD) & 2 (full suite) N/A — no product code.
This directory holds the reproducible provisioning script and the evidence
collected during the run.

## Deliverable
StartOS 0.4.0.1 test VM (`startos-test`, 3 vCPU / 4 GiB, 60 GiB qcow2) running on
DQ05, booted off its own disk with the HTTPS dashboard (443) serving.

## Evidence recap (full output in task completion + run log)
- **KVM stack**: libvirt 11.6.0 / QEMU 10.1.0; `libvirtd` active; user in
  `kvm`,`libvirt` groups; `/dev/kvm` present.
- **ISO verified**: `startos.iso` (1,751,285,760 B) SHA-256
  `9f4f5ad4ece2dc349b0a7b6221b542b26112fe753657024487fc26ef6cf08072` — matches the
  published release checksum. GPG Good signature from Start9 (prior run).
- **VM**: `startos-test` running, autostart on; NAT IP `192.168.122.196/24`.
- **Installed OS (guest agent)**: StartOS v0.4.0.1, kernel `7.1.3+deb13-amd64`
  x86_64; hostname `startos-test-vm`.
- **Boot log (dmesg via guest agent)**: `Linux version 7.1.3+deb13-amd64 ...`,
  `Command line: ... root=UUID=acadcbc9-... ro boot=startos console=ttyS0,115200n8`.
- **Ports on VM**: 443 / 80 / 22 OPEN.
- **Dashboard from T470 via SSH tunnel**
  `ssh -L 8443:192.168.122.196:443 c03rad0r@100.90.22.201`:
  `curl -k https://localhost:8443/` → HTTP 200, `<title>StartOS</title>`.

## Access
```
ssh c03rad0r@100.90.22.201              # DQ05 (Netbird; LAN fallback 'ssh dq05' 192.168.1.218)
ssh -L 8443:192.168.122.196:443 c03rad0r@100.90.22.201   # tunnel to dashboard
# then open https://localhost:8443  (test admin password set during setup)
```
Restart VM if stopped: `virsh start startos-test` (autostart already on host boot).
