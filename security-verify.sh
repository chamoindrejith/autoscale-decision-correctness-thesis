#!/usr/bin/env bash
#
# security-verify.sh
# Verifies the security-hardening controls applied to the k3s autoscaling
# research Droplet. Produces a PASS / WARN / FAIL report suitable for review.
#
# HOW TO RUN (as the chamodi user, NOT via sudo):
#     sudo -v                       # prime sudo once (enter your password)
#     bash ~/security-verify.sh     # run the checks
#
# To save the report to a file you can show your supervisor:
#     bash ~/security-verify.sh | tee ~/security-report-$(date +%Y%m%d).txt
#
# The script uses sudo internally only for the checks that need root.
# kubectl runs as the current (chamodi) user.

# Make kubectl work when run non-interactively
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

# Colour only when writing to a terminal (keeps saved files clean)
if [ -t 1 ]; then
  G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'
else
  G=; R=; Y=; B=; N=
fi

PASS=0; WARN=0; FAIL=0

ok()   { echo "  ${G}[ PASS ]${N} $1"; PASS=$((PASS+1)); }
warn() { echo "  ${Y}[ WARN ]${N} $1"; WARN=$((WARN+1)); }
bad()  { echo "  ${R}[ FAIL ]${N} $1"; FAIL=$((FAIL+1)); }
info() { echo "         $1"; }
hdr()  { echo; echo "${B}== $1 ==${N}"; }

echo "==================================================================="
echo " SECURITY HARDENING VERIFICATION REPORT"
echo " Host : $(hostname)"
echo " Date : $(date)"
echo " User : $(whoami)"
echo "==================================================================="

# ------------------------------------------------------------------ Updates
hdr "1. Operating System Updates"
UPD=$(apt list --upgradable 2>/dev/null | grep -c upgradable)
if [ "${UPD:-0}" -eq 0 ]; then ok "No pending package updates"; else warn "$UPD package(s) can be upgraded"; fi
if grep -q 'Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null; then
  ok "Automatic security updates enabled"
else
  bad "Automatic security updates NOT enabled"
fi
if [ -f /var/run/reboot-required ]; then
  warn "Reboot required (kernel/library update pending)"
else
  ok "No reboot pending — running kernel $(uname -r)"
fi

# -------------------------------------------------------------- User & sudo
hdr "2. User & Privilege Separation"
if id chamodi >/dev/null 2>&1; then ok "Non-root user 'chamodi' exists"; else bad "User 'chamodi' missing"; fi
if id -nG chamodi 2>/dev/null | grep -qw sudo; then ok "chamodi has sudo privileges"; else bad "chamodi not in sudo group"; fi

# ----------------------------------------------------------------- SSH
hdr "3. SSH Hardening"
SSHD=$(sudo sshd -T 2>/dev/null)
echo "$SSHD" | grep -qi '^permitrootlogin no'        && ok "Root SSH login disabled"            || bad "Root SSH login NOT disabled"
echo "$SSHD" | grep -qi '^passwordauthentication no' && ok "Password authentication disabled"   || bad "Password authentication still enabled"
echo "$SSHD" | grep -qi '^allowusers chamodi'        && ok "SSH restricted to user 'chamodi'"   || warn "AllowUsers not restricted to chamodi"
echo "$SSHD" | grep -qi '^loglevel verbose'          && ok "SSH verbose logging enabled"        || warn "SSH LogLevel not VERBOSE"
echo "$SSHD" | grep -qi '^maxauthtries 3'            && ok "SSH MaxAuthTries limited to 3"      || warn "SSH MaxAuthTries not 3"

# ----------------------------------------------------------------- Firewall
hdr "4. Firewall (UFW)"
UFW=$(sudo ufw status verbose 2>/dev/null)
echo "$UFW" | grep -q "Status: active"        && ok "UFW firewall is active"          || bad "UFW is NOT active"
echo "$UFW" | grep -qi "deny (incoming)"      && ok "Default policy: deny incoming"    || bad "Default incoming policy not deny"
grep -q 'DEFAULT_FORWARD_POLICY="ACCEPT"' /etc/default/ufw 2>/dev/null \
  && ok "Forward policy ACCEPT (preserves k3s networking)" \
  || warn "Forward policy not ACCEPT (may break k3s)"
for p in 22 3000 9090 30080; do
  echo "$UFW" | grep -qE "^${p}/tcp .*ALLOW" && info "  - port $p allowed"
done

# ----------------------------------------------------------------- Fail2Ban
hdr "5. Intrusion Prevention (Fail2Ban)"
if systemctl is-active --quiet fail2ban; then ok "fail2ban service active"; else bad "fail2ban not active"; fi
F2B=$(sudo fail2ban-client status sshd 2>/dev/null)
CUR=$(echo "$F2B" | grep "Currently banned" | grep -oE '[0-9]+$')
TOT=$(echo "$F2B" | grep "Total banned"     | grep -oE '[0-9]+$')
if [ -n "$CUR" ]; then ok "sshd jail active (currently banned: ${CUR}, total banned: ${TOT:-0})"; else warn "sshd jail not reporting"; fi

# ----------------------------------------------------------------- sysctl
hdr "6. Kernel Hardening (sysctl)"
chk() { v=$(sysctl -n "$1" 2>/dev/null); if [ "$v" = "$2" ]; then ok "$1 = $v"; else warn "$1 = ${v:-unset} (expected $2)"; fi; }
chk net.ipv4.tcp_syncookies 1
chk kernel.randomize_va_space 2
chk kernel.dmesg_restrict 1
chk net.ipv4.conf.all.rp_filter 1
chk net.ipv4.ip_forward 1     # must stay 1 for k3s

# ----------------------------------------------------------------- auditd
hdr "7. Audit Logging (auditd)"
if systemctl is-active --quiet auditd; then ok "auditd service active"; else bad "auditd not active"; fi
RULES=$(sudo auditctl -l 2>/dev/null | grep -c .)
if [ "${RULES:-0}" -gt 0 ]; then ok "auditd has ${RULES} watch rule(s) loaded"; else warn "no audit rules loaded"; fi

# ----------------------------------------------------------------- AIDE
hdr "8. File Integrity Monitoring (AIDE)"
if sudo test -f /var/lib/aide/aide.db; then
  SZ=$(sudo du -h /var/lib/aide/aide.db 2>/dev/null | cut -f1)
  ok "AIDE baseline database present (${SZ})"
else
  bad "AIDE database missing"
fi
if systemctl is-enabled --quiet dailyaidecheck.timer 2>/dev/null; then ok "Daily AIDE integrity check scheduled"; else warn "dailyaidecheck.timer not enabled"; fi

# ----------------------------------------------------------------- Services
hdr "9. Attack Surface (unnecessary services)"
for svc in bluetooth cups avahi-daemon ModemManager; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then warn "$svc is running"; else ok "$svc not running"; fi
done

# ----------------------------------------------------------------- Mail
hdr "10. Mail Daemon (Postfix)"
PF=$(sudo postconf -h inet_interfaces 2>/dev/null)
echo "$PF" | grep -qi "loopback-only" && ok "Postfix bound to localhost only" || warn "Postfix inet_interfaces = ${PF}"

# ----------------------------------------------------------------- Ports
hdr "11. Internet-facing Listening Ports"
info "Non-localhost listeners (UFW controls actual external access):"
sudo ss -tlnH 2>/dev/null | awk '{print $4}' \
  | grep -vE '127\.0\.0\.|\[::1\]' | sort -u | sed 's/^/           /'

# ----------------------------------------------------------------- Cluster
hdr "12. Experiment / Cluster Integrity"
READY=$(kubectl get nodes --no-headers 2>/dev/null | grep -cw Ready)
TOTAL=$(kubectl get nodes --no-headers 2>/dev/null | grep -c .)
if [ "${READY:-0}" -ge 1 ]; then ok "${READY}/${TOTAL} node(s) Ready"; else bad "no Ready nodes"; fi
NOTOK=$(kubectl get pods -A --no-headers 2>/dev/null | grep -vE 'Running|Completed' | grep -c .)
if [ "${NOTOK:-1}" -eq 0 ]; then ok "All pods Running/Completed"; else warn "${NOTOK} pod(s) not Running"; fi
HPA=$(kubectl get hpa -n autoscale-research --no-headers 2>/dev/null)
if echo "$HPA" | grep -q "<unknown>"; then
  bad "HPA metrics show <unknown> (metrics pipeline broken)"
elif [ -n "$HPA" ]; then
  ok "HPA reporting live metrics: $(echo "$HPA" | awk '{print $3, $4}')"
else
  warn "No HPA found in autoscale-research namespace"
fi

# ----------------------------------------------------------------- Summary
echo
echo "==================================================================="
echo "${B} SUMMARY${N}"
echo "   ${G}PASS: ${PASS}${N}    ${Y}WARN: ${WARN}${N}    ${R}FAIL: ${FAIL}${N}"
if [ "$FAIL" -eq 0 ]; then
  echo "   ${G}All critical controls verified.${N}"
else
  echo "   ${R}Some critical controls need attention (see FAIL items above).${N}"
fi
echo "==================================================================="
