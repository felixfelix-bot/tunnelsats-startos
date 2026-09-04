#!/usr/bin/env bash
# startos-vm-dq05.sh
# Stand up a StartOS test VM on DQ05 (T3a).
# This is the reproducible, idempotent provisioning script for the infra task
# "T3a: DQ05 KVM stack + StartOS VM boot".
#
# Targets: DQ05 host (c03rad0r@100.90.22.201, Netbird) with passwordless sudo.
# Produces: /home/c03rad0r/vm/startos/ with qcow2 + ISO, libvirt domain "startos-test".
#
# Usage (as c03rad0r on DQ05, or run via ssh):
#   ssh c03rad0r@100.90.22.201 'bash -s' < startos-vm-dq05.sh
#
# NOTE: this script provisions the KVM stack + VM definition. The StartOS OS
# install itself is driven through the web wizard (port 80) end-to-end, then the
# server boots the installed dashboard on 443 — see EVIDENCE.txt in the task.
set -euo pipefail

# ---- Config ---------------------------------------------------------------
VM_NAME="startos-test"
VM_DIR="$HOME/vm/startos"
DISK="$VM_DIR/startos-disk.qcow2"
ISO_URL="https://startos-images.nyc3.cdn.digitaloceanspaces.com/startos-0.4.0.1-fdb27c7_x86_64.iso"
ISO="$VM_DIR/startos.iso"
PUBLISHED_SHA="9f4f5ad4ece2dc349b0a7b6221b542b26112fe753657024487fc26ef6cf08072"
DISK_SIZE_GB=60
VCPUS=3
MEM_MIB=4096

echo "==> [1/5] Install KVM stack (idempotent)"
sudo apt-get update -y
sudo apt-get install -y qemu-kvm libvirt-daemon-system virtinst qemu-utils
sudo usermod -aG kvm,libvirt "$(id -un)" || true

echo "==> [2/5] Ensure libvirtd + default NAT network"
sudo systemctl enable --now libvirtd || true
virsh net-start default 2>/dev/null || true
virsh net-autostart default 2>/dev/null || true

echo "==> [3/5] Fetch + verify StartOS ISO"
mkdir -p "$VM_DIR"
if [ ! -f "$ISO" ]; then
  curl -fL -o "$ISO" "$ISO_URL"
fi
echo "Verifying SHA-256..."
ACTUAL_SHA=$(sha256sum "$ISO" | awk '{print $1}')
if [ "$ACTUAL_SHA" != "$PUBLISHED_SHA" ]; then
  echo "ERROR: checksum mismatch" >&2
  echo "  got      $ACTUAL_SHA" >&2
  echo "  expected $PUBLISHED_SHA" >&2
  exit 1
fi
echo "checksum OK: $ACTUAL_SHA"

echo "==> [4/5] Create 60GiB qcow2 disk (if absent)"
if [ ! -f "$DISK" ]; then
  qemu-img create -f qcow2 "$DISK" "${DISK_SIZE_GB}G"
fi

echo "==> [5/5] Define + start the VM (if absent)"
if ! virsh list --all --name | grep -qx "$VM_NAME"; then
  virt-install \
    --name "$VM_NAME" \
    --memory "$MEM_MIB" \
    --vcpus "$VCPUS" \
    --disk path="$DISK",format=qcow2,bus=virtio \
    --cdrom "$ISO" \
    --network network=default,model=virtio \
    --graphics none \
    --video virtio \
    --noautoconsole \
    --osinfo detect=on,name=linux2024 \
    --boot cdrom,hd,menu=on
fi
virsh autostart "$VM_NAME" || true

# home dir must be traversable for the libvirt-qemu hypervisor to open the files
sudo chmod o+x "$HOME"
sudo chmod o+rx "$VM_DIR"
sudo chmod o+r "$ISO" || true
sudo chmod o+rw "$DISK" || true

echo "==> done. Domain state:"
virsh domstate "$VM_NAME"
virsh domifaddr "$VM_NAME" 2>/dev/null || true

cat <<EOF

Next steps (manual, via web wizard + guest):
 1. Open the installer:  http://<vm-ip>:80/  (reach via: ssh -L 8080:<vm-ip>:80 c03rad0r@100.90.22.201)
 2. Language -> Keyboard -> Select Drives (OS Drive=/dev/vda, Data Drive=/dev/vda) -> install.
 3. Setup flow -> Start Fresh -> server name + password -> Finish.
 4. "Remove Installation Media":  virsh detach-disk $VM_NAME sda --config  (ISO boot source)
 5. Reboot VM:  virsh reboot $VM_NAME   (now boots installed StartOS from disk)
 6. Dashboard:  ssh -L 8443:<vm-ip>:443 c03rad0r@100.90.22.201 ; curl -k https://localhost:8443/
EOF
