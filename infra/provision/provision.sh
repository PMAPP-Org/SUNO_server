#!/usr/bin/env bash
# Aprovisionamiento endurecido del VPS de VisitaCampo (Ubuntu 24.04 LTS o Debian 12).
#
# Idempotente: se puede ejecutar de nuevo para reaplicar la configuración. Se
# prueba en CI ejecutándolo dos veces sobre una VM real (.github/workflows/provision.yml).
#
# Uso (como root, en el servidor recién creado):
#   DEPLOY_SSH_PUBKEY="ssh-ed25519 AAAA… operador@equipo" bash provision.sh
#
# Variables:
#   DEPLOY_SSH_PUBKEY   llave pública del usuario `deploy` (obligatoria: sin ella
#                       NO se desactiva el acceso por contraseña, para no dejarte
#                       fuera del servidor)
#   SSH_ALLOW_USERS     opcional: lista para `AllowUsers` (p. ej. "deploy admin")
#   DEPLOY_DIR          directorio de despliegue (def. /opt/visitacampo)
#
# Qué deja configurado (ISO 27001 A.8.9, A.8.17, A.8.19, A.8.20; OWASP A02):
#   - usuario `deploy` sin contraseña, solo con llave, en el grupo docker;
#   - SSH: sin contraseña ni root, MaxAuthTries 3, túneles solo locales (-L);
#   - firewall (ufw): entrada denegada salvo 22, 80, 443 (TCP) y 443/UDP;
#   - fail2ban para SSH; chrony (NTP); actualizaciones de seguridad automáticas;
#   - Docker desde el repositorio oficial (llave verificada por huella), con
#     rotación de logs de contenedores y live-restore;
#   - sysctl de red endurecido.
set -euo pipefail

DEPLOY_USER=deploy
DEPLOY_DIR="${DEPLOY_DIR:-/opt/visitacampo}"
DOCKER_GPG_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88

log() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "ejecuta como root (sudo)."
[ -n "${DEPLOY_SSH_PUBKEY:-}" ] || die "falta DEPLOY_SSH_PUBKEY: sin llave no se endurece SSH (te quedarías fuera)."
case "$DEPLOY_SSH_PUBKEY" in
	ssh-ed25519\ * | ecdsa-sha2-* | ssh-rsa\ *) ;;
	*) die "DEPLOY_SSH_PUBKEY no parece una llave pública SSH." ;;
esac

# shellcheck source=/dev/null
. /etc/os-release
case "${ID:-}" in
	ubuntu | debian) ;;
	*) die "sistema no soportado: ${ID:-desconocido} (Ubuntu 24.04 o Debian 12)." ;;
esac

export DEBIAN_FRONTEND=noninteractive

log "Paquetes base y actualizaciones"
apt-get update -q
apt-get -y -q upgrade
apt-get install -y -q --no-install-recommends \
	ca-certificates curl gnupg openssh-server ufw fail2ban chrony unattended-upgrades python3 python3-systemd

log "Docker (repositorio oficial, llave verificada)"
if command -v docker > /dev/null 2>&1; then
	echo "Docker ya instalado: $(docker --version)"
else
	install -m 0755 -d /etc/apt/keyrings
	key="$(mktemp)"
	curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o "$key"
	fpr="$(gpg --show-keys --with-colons "$key" | awk -F: '/^fpr/ {print $10; exit}')"
	[ "$fpr" = "$DOCKER_GPG_FINGERPRINT" ] || die "huella de la llave de Docker inesperada: $fpr"
	install -m 0644 "$key" /etc/apt/keyrings/docker.asc
	rm -f "$key"
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
		> /etc/apt/sources.list.d/docker.list
	apt-get update -q
	apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

log "Docker: rotación de logs y live-restore (sin pisar ajustes existentes)"
install -m 0755 -d /etc/docker
python3 - << 'PY'
import json, pathlib
path = pathlib.Path("/etc/docker/daemon.json")
config = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
config.update({
    "log-driver": "json-file",
    "log-opts": {"max-size": "10m", "max-file": "5"},
    "live-restore": True,
})
path.write_text(json.dumps(config, indent=2) + "\n")
PY
systemctl enable --now docker
systemctl restart docker

log "Usuario ${DEPLOY_USER}"
if ! id "$DEPLOY_USER" > /dev/null 2>&1; then
	useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
passwd -l "$DEPLOY_USER" > /dev/null # sin contraseña: solo llave
usermod -aG docker "$DEPLOY_USER"
home="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$home/.ssh"
keys="$home/.ssh/authorized_keys"
touch "$keys"
grep -qxF "$DEPLOY_SSH_PUBKEY" "$keys" || echo "$DEPLOY_SSH_PUBKEY" >> "$keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "$keys"
chmod 0600 "$keys"
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_DIR"

log "SSH endurecido"
conf=/etc/ssh/sshd_config.d/10-visitacampo.conf
{
	echo "# Gestionado por infra/provision/provision.sh: no editar a mano."
	echo "PasswordAuthentication no"
	echo "KbdInteractiveAuthentication no"
	echo "PermitRootLogin no"
	echo "PubkeyAuthentication yes"
	echo "MaxAuthTries 3"
	echo "X11Forwarding no"
	# Solo túneles locales (-L): los paneles de monitoreo y el Admin se usan así.
	echo "AllowTcpForwarding local"
	echo "ClientAliveInterval 300"
	echo "ClientAliveCountMax 2"
	if [ -n "${SSH_ALLOW_USERS:-}" ]; then echo "AllowUsers ${SSH_ALLOW_USERS}"; fi
} > "$conf"
chmod 0644 "$conf"
install -d -m 0755 /run/sshd # con ssh.socket (Ubuntu 24.04) puede no existir aún
sshd -t || die "la configuración de SSH no es válida; no se recarga."
systemctl reload ssh 2> /dev/null || systemctl reload sshd 2> /dev/null || true

log "Firewall (ufw)"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow 22/tcp comment 'ssh'
ufw allow 80/tcp comment 'http (redirige a https, ACME)'
ufw allow 443/tcp comment 'https'
ufw allow 443/udp comment 'http3'
ufw --force enable

log "fail2ban (SSH)"
cat > /etc/fail2ban/jail.d/visitacampo.local << 'EOF'
# Gestionado por infra/provision/provision.sh.
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable fail2ban
systemctl restart fail2ban

log "Hora sincronizada (chrony)"
systemctl enable --now chrony

log "Actualizaciones de seguridad automáticas"
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

log "sysctl de red"
cat > /etc/sysctl.d/90-visitacampo.conf << 'EOF'
# Gestionado por infra/provision/provision.sh. No toca ip_forward (lo usa Docker).
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.log_martians = 1
EOF
sysctl --system > /dev/null

log "Listo"
echo "Siguiente: como ${DEPLOY_USER}, clonar el repositorio en ${DEPLOY_DIR} y seguir docs/operacion/despliegue.md."
