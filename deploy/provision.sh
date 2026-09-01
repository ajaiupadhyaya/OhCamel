#!/usr/bin/env bash
#
# First boot on a fresh Ubuntu 24.04 droplet. Run as root:
#
#   ssh root@DROPLET 'bash -s' < deploy/provision.sh
#
# Idempotent -- safe to re-run, which matters because the first run is the one
# most likely to be interrupted.
#
# What it does NOT do: clone the repository, write any credential, or start
# anything. Provisioning prepares a machine; deploying puts software on it, and
# keeping those separate means a bad deploy is never a reason to rebuild a box.

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-ohcamel}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

[ "$(id -u)" -eq 0 ] || { echo "provision: run as root" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
	ca-certificates curl gnupg git ufw fail2ban unattended-upgrades jq

# ---------------------------------------------------------------------------
say "Swap"
#
# Two gigabytes, for the BUILD and not for the runtime. Compiling owl next to
# Jane Street's core and async is memory-hungry in a way the running process is
# not; both engines together sit well under 500MB resident. A server that is
# swapping while serving is already wrong -- this exists so that a build on a
# 4GB box does not get OOM-killed at minute eighteen of twenty.
if [ ! -f /swapfile ]; then
	fallocate -l 2G /swapfile
	chmod 600 /swapfile
	mkswap /swapfile
	swapon /swapfile
	grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
	# Swap as an emergency floor, not a routine tier.
	sysctl -w vm.swappiness=10
	grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >>/etc/sysctl.conf
else
	echo "  /swapfile exists, leaving it alone"
fi

# ---------------------------------------------------------------------------
say "Docker"
if ! command -v docker >/dev/null 2>&1; then
	install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
		-o /etc/apt/keyrings/docker.asc
	chmod a+r /etc/apt/keyrings/docker.asc
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
		>/etc/apt/sources.list.d/docker.list
	apt-get update
	apt-get install -y docker-ce docker-ce-cli containerd.io \
		docker-buildx-plugin docker-compose-plugin
	systemctl enable --now docker
else
	echo "  docker present: $(docker --version)"
fi

# ---------------------------------------------------------------------------
say "Deploy user"
#
# Unprivileged, in the docker group. Note that docker-group membership is
# root-equivalent in practice -- it can bind-mount / into a container. This is
# a convenience boundary against mistakes, not a security boundary against an
# attacker who already has this account.
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
	adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

# Whatever key already reaches root reaches the deploy user too, so the first
# deploy does not need a second key exchange.
if [ -f /root/.ssh/authorized_keys ]; then
	install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
	install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
		/root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

# ---------------------------------------------------------------------------
say "Secrets directory"
#
# Created empty and locked down now, so that when the live credentials arrive
# in Phase 4 there is no window in which they sit somewhere world-readable.
install -d -m 0700 -o root -g root /etc/ohcamel

# ---------------------------------------------------------------------------
say "Firewall"
#
# Docker famously writes its own iptables rules and can bypass ufw for
# PUBLISHED ports. That is survivable here only because of a specific choice in
# docker-compose.yml: the two engine containers publish nothing. Caddy is the
# only service with a host port, and 80/443 are open on purpose. If a future
# change adds `ports:` to an engine, this firewall will not save it -- see
# assertion 5 in smoke.sh, which checks for exactly that mistake from outside.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'ssh'
ufw allow 80/tcp comment 'http -- acme challenge and the redirect'
ufw allow 443/tcp comment 'https'
ufw allow 443/udp comment 'http/3'
ufw --force enable
ufw status verbose

# ---------------------------------------------------------------------------
say "Unattended upgrades and fail2ban"
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now fail2ban

# ---------------------------------------------------------------------------
say "Done"
cat <<EOF

  Next:

    1. As $DEPLOY_USER, clone the repository:
         sudo -iu $DEPLOY_USER git clone https://github.com/ajaiupadhyaya/OhCamel.git

    2. Write deploy/.env from deploy/deploy.env.example (hostnames, ACME
       email, the bcrypt hash from \`caddy hash-password\`).

    3. Point DNS at $(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo 'this droplet') and WAIT for it to resolve.
       Caddy will fail its ACME challenge otherwise, and Let's Encrypt counts
       those failures.

    4. deploy/deploy.sh

EOF
