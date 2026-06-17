# Hardening Guide for the Autoscaling Research Cluster

This guide walks you through hardening your DigitalOcean Droplet running the k3s + Ballerina + Prometheus + Grafana + HPA-watcher experimental setup. It is adapted from a peer's log-aggregation research guide and tailored to:

- Your specific services (Grafana on 3000, Prometheus on 9090, NodePort on 30080)
- Your **running** cluster (we will not wipe or reinstall anything)
- Your need to work from multiple networks (no IP whitelist required — security comes from key-only SSH + fail2ban + UFW)
- Your beginner level (each command is explained)

**Time estimate:** ~90–120 minutes including verification between stages.

**Rollback safety net:** You took a Droplet snapshot before starting. If anything goes catastrophically wrong, you can restore the entire VM from that snapshot in DigitalOcean → Droplets → Backups & Snapshots → "Restore" next to your snapshot.

---

## ✅ COMPLETED — Execution Notes (23 May 2026)

This hardening was completed successfully. The steps below were followed, with these real-world deviations and lessons worth recording. **If you ever rebuild this server, read this section first.**

### SSH key recovery (before hardening could start)
The original key `~/.ssh/id_ed25519` had a forgotten passphrase and was not saved in macOS Keychain, so it was unrecoverable. We generated a new key **with no passphrase**: `~/.ssh/id_ed25519_2026`, added its public key to the Droplet via the DigitalOcean Web Console, and configured the Mac's `~/.ssh/config`:

```
Host droplet 206.189.133.70
  HostName 206.189.133.70
  User chamodi          # was 'root' before Stage 4
  IdentityFile ~/.ssh/id_ed25519_2026
  IdentitiesOnly yes
```

Lesson: SSH only auto-tries standard key names (`id_ed25519`). A non-standard key name **must** be pointed to in `~/.ssh/config`, or SSH ignores it.

### Stage 5 (UFW) — k3s-aware adaptations (IMPORTANT)
A naive UFW setup breaks k3s networking (CoreDNS, metrics-server → HPA). We added two things the generic guide omits:

```bash
# Keep packet forwarding ON (k3s pod/service routing needs it)
sudo sed -i 's/DEFAULT_FORWARD_POLICY="DROP"/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw

# Trust the k3s pod and service networks
sudo ufw allow from 10.42.0.0/16 comment 'k3s pod network'
sudo ufw allow from 10.43.0.0/16 comment 'k3s service network'
```

Final open ports: **22, 3000 (Grafana), 9090 (Prometheus), 30080 (NodePort)**. We did **not** open 6443 to the internet — kubectl runs locally and pods reach the API via the service network. All commands were run as `chamodi` with `sudo` (not as root, since root SSH was already disabled in Stage 4).

### Stage 6 (fail2ban)
On Ubuntu 24.04 fail2ban reads the **systemd journal**, not `/var/log/auth.log` — this is normal and more reliable. It began auto-banning brute-force IPs within minutes (confirmed in `ufw status` after reboot).

### Stage 8 (auditd) — research-integrity adaptation
We **dropped** the generic guide's two broad failed-access rules (`-a always,exit -F arch=b64 -S all -F exit=-EACCES/-EPERM`). On a k8s node they fire constantly and add variable CPU overhead that could **contaminate the autoscaling CPU measurements**. Kept only the targeted file watches.

### Stage 10 (AIDE)
The daily integrity check is auto-scheduled via the systemd `dailyaidecheck.timer` (installed with the package) — the manual cron line in the generic guide is **not** needed.

### Stage 11 (Lynis) — score 71
Applied four quick fixes: hid the postfix SMTP banner (`postconf -e 'smtpd_banner=$myhostname ESMTP'`), set SSH `LogLevel VERBOSE`, `apt autoremove --purge`, and added legal banners to `/etc/issue` + `/etc/issue.net`. Deliberately skipped process-accounting/sysstat (measurement overhead), separate partitions (not feasible on a running VM), and malware scanners (k8s noise).

### Stage 12 (reboot) — hostname-change lesson (IMPORTANT)
Changing the hostname to `k3s-research` caused two issues, because **k3s names its node after the machine hostname**:
1. k3s registered a fresh node `k3s-research` and orphaned the old `ubuntu-s-...` node (showed `NotReady`). Fix: `kubectl delete node ubuntu-s-2vcpu-2gb-90gb-intel-blr1`.
2. The watcher's `local-path` PersistentVolume was pinned to the old hostname (node-affinity conflict), leaving the pod `Pending`. PV node-affinity is immutable, so we deleted the PVC/PV and recreated the watcher, which provisioned a fresh volume on the new node.

**Lesson: do not rename a k3s node's hostname after the cluster is established.** If you must, expect to clean up the orphaned node and any `local-path` volumes afterward.

### Daily operating commands (post-hardening)
- Log in: `ssh droplet` (connects as chamodi via the new key)
- Grafana: `kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 --address 0.0.0.0` → `http://206.189.133.70:3000`
- Prometheus: `kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090 --address 0.0.0.0` → `http://206.189.133.70:9090`
- Banned IPs: `sudo fail2ban-client status sshd`
- Emergency back door if SSH ever fails: DigitalOcean Web Console (bypasses sshd + UFW)

---

## Pre-flight Checklist

Before starting, confirm all of these:

- [ ] Droplet snapshot exists (taken May 13, ~12.7 GB) — your rollback button
- [ ] DigitalOcean Web Console works (your emergency back door if SSH breaks)
- [ ] Your SSH key (`chamodi-MAC`) is in DigitalOcean
- [ ] GitHub 2FA enabled with authenticator app
- [ ] Fresh GitHub recovery codes downloaded
- [ ] You can paste output back to me for me to verify each step
- [ ] You have ~2 hours uninterrupted

**Open three browser tabs / terminals before starting:**

1. **Terminal A** — your current SSH session on your Mac (logged in as `root` to start)
2. **Terminal B** — a SECOND empty Terminal window on your Mac (we'll use this to test the new `chamodi` user without losing the root session in Terminal A)
3. **Browser tab** — DigitalOcean Web Console open and logged in, as ultimate emergency back door

Never close Terminal A until everything is verified in Terminal B.

---

## Stage 1 — System Update + Install Security Tools

### Goal
Bring the OS fully patched and install every security tool we'll need for the rest of the guide, in one go.

### Why
- Your Droplet has 16 pending updates (3 security). Patches close known vulnerabilities.
- Installing all tools now means later stages just configure them rather than install them.

### Commands (run as root on the Droplet)

```bash
# Refresh package index and upgrade everything
apt update
apt full-upgrade -y

# Install security tooling
apt install -y \
    curl wget git vim htop jq python3-pip python3-venv \
    apt-transport-https ca-certificates gnupg lsb-release ncdu \
    unattended-upgrades fail2ban auditd audispd-plugins aide lynis \
    ufw net-tools
```

### What each tool does (beginner glossary)

- `unattended-upgrades` — automatically installs security patches nightly
- `fail2ban` — bans IPs that fail SSH login N times
- `auditd` + `audispd-plugins` — Linux audit framework; logs every security-sensitive action
- `aide` — file integrity monitoring; detects tampering with system files
- `lynis` — security audit scanner; gives you a score after hardening
- `ufw` — Uncomplicated Firewall (a friendly wrapper around iptables)
- `ncdu` — disk usage explorer (handy when troubleshooting space issues)

### Verify

```bash
# All tools should print a version number or help text
fail2ban-client --version
ufw --version
auditctl --version
aide --version
lynis --version
unattended-upgrade --help | head -3
```

### What could go wrong

- `apt full-upgrade` may prompt to keep or replace config files. Press **Enter** to keep the existing version (the default).
- If `apt` complains about locked dpkg, another update may be running. `ps aux | grep apt` to find it; wait or kernel-reboot.

### Don't reboot yet
A kernel was likely upgraded, so the Droplet will need a reboot eventually. We'll do that at the very end (Stage 12) after everything is configured, not now.

---

## Stage 2 — Configure Automatic Security Updates

### Goal
Make Ubuntu install security patches itself, every night.

### Why
Patches close known vulnerabilities. If you forget to update for a month, attackers don't.

### Commands

```bash
# Interactive prompt - choose "Yes" to enable
dpkg-reconfigure --priority=low unattended-upgrades

# Verify config exists
cat /etc/apt/apt.conf.d/20auto-upgrades
# You should see:
#   APT::Periodic::Update-Package-Lists "1";
#   APT::Periodic::Unattended-Upgrade "1";

# Dry-run test (won't actually install, just shows what it would do)
unattended-upgrade --dry-run --debug 2>&1 | tail -20
```

### Verify
The dry-run output should end with `No packages found that can be upgraded unattended and no pending auto-removals` (since we just upgraded everything in Stage 1).

---

## Stage 3 — Create the `chamodi` User

### Goal
Stop operating as `root`. Create a non-root user with sudo, and copy your SSH key over so you can log in as that user.

### Why
- If an attacker ever does get into your `chamodi` account, they don't immediately have root. They have to additionally crack `sudo`, which adds a layer.
- Standard security practice: never SSH as root.

### Commands (run as root)

```bash
# Create the user with home directory and bash shell
useradd -m -s /bin/bash chamodi

# Add to sudo group (allows running sudo commands)
usermod -aG sudo chamodi

# Set a strong password (you'll be prompted - use a password manager, save it)
passwd chamodi

# Copy your SSH key from root to chamodi
mkdir -p /home/chamodi/.ssh
cp /root/.ssh/authorized_keys /home/chamodi/.ssh/authorized_keys
chown -R chamodi:chamodi /home/chamodi/.ssh
chmod 700 /home/chamodi/.ssh
chmod 600 /home/chamodi/.ssh/authorized_keys

# Give chamodi the kubeconfig too, so kubectl works as chamodi
mkdir -p /home/chamodi/.kube
cp /root/.kube/config /home/chamodi/.kube/config 2>/dev/null || cp /etc/rancher/k3s/k3s.yaml /home/chamodi/.kube/config
chown -R chamodi:chamodi /home/chamodi/.kube
chmod 600 /home/chamodi/.kube/config
```

### Verify (CRITICAL — do this in Terminal B before continuing)

In your **second Terminal window (Terminal B)** on your Mac:

```bash
ssh chamodi@206.189.133.70
```

You should land in `chamodi@...:~$` (note: `$` not `#`, because chamodi is not root).

Then test sudo:

```bash
sudo whoami
# Should print: root
```

And test kubectl:

```bash
kubectl get nodes
# Should show the same Ready node as before
```

**Only proceed to Stage 4 if all three of these work in Terminal B.** Leave Terminal A (root) connected as a safety net.

### What could go wrong

- `Permission denied (publickey)` → the SSH key wasn't copied correctly. Re-run the cp/chmod block as root.
- `sudo: a password is required` → that's fine, enter the password you set. Or run `sudo -n whoami` for passwordless test (will fail by default).
- kubectl `permission denied` → check the kubeconfig file permission (`ls -la /home/chamodi/.kube/config` should show `chamodi chamodi`).

---

## Stage 4 — SSH Hardening

### Goal
Lock down the SSH daemon: disable root login, disable password auth, restrict to specific users, upgrade crypto.

### Why
SSH is your most exposed service. Every brute-force bot on the internet probes port 22. Even with fail2ban, the SSH daemon itself should refuse anything except your key and your user.

### Commands (run as root in Terminal A)

```bash
# Create a hardening drop-in config (doesn't replace /etc/ssh/sshd_config,
# just overrides specific settings)
cat << 'EOF' > /etc/ssh/sshd_config.d/99-hardening.conf
# Disable root login over SSH
PermitRootLogin no

# Key-only authentication
PasswordAuthentication no
PermitEmptyPasswords no
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no

# Protocol & session limits
Protocol 2
MaxAuthTries 3
MaxSessions 3
ClientAliveInterval 300
ClientAliveCountMax 2
LoginGraceTime 30

# Disable forwarding (we don't need it)
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no

# Restrict to chamodi user only
AllowUsers chamodi

# Strong crypto only
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com
EOF

# Test the config syntax BEFORE applying
sshd -t
# If this prints nothing, the syntax is OK.
# If it prints errors, fix them - do not restart sshd.
```

### Apply (only after `sshd -t` returns no errors)

```bash
# Reload sshd config (does NOT terminate existing sessions)
systemctl reload ssh
```

### Verify (in Terminal B — do NOT close Terminal A)

```bash
# This should still work in Terminal B (you're already logged in)
sudo whoami

# Open a THIRD terminal on your Mac and try to log in as root - this should fail:
ssh root@206.189.133.70
# Expected: "Permission denied (publickey,publickey)" or similar.
# This confirms root login is now blocked.

# In the third terminal, log in as chamodi - this should work:
ssh chamodi@206.189.133.70
```

If both verifications pass — root rejected, chamodi accepted — Stage 4 is complete. **Keep Terminal A (root) open until Stage 12 in case something later needs fixing.**

### What could go wrong

- If you lose your chamodi SSH ability and Terminal A still has root: edit `/etc/ssh/sshd_config.d/99-hardening.conf` to fix the issue, then `systemctl reload ssh`.
- If you lose BOTH SSH sessions: use the DigitalOcean Web Console as root (root password login via console still works regardless of sshd_config), fix the config, reload.

### What we are NOT doing (and why)

The peer's guide regenerates SSH host keys (`rm -f /etc/ssh/ssh_host_*`). We are **skipping** this. Reasons:

- Your existing host keys are fine — they're generated fresh per DO Droplet at provisioning, not shared.
- Regenerating them invalidates your Mac's `known_hosts` entry and causes scary "REMOTE HOST IDENTIFICATION HAS CHANGED!" warnings.
- For a research project the marginal security gain is not worth the disruption.

---

## Stage 5 — UFW Firewall

### Goal
Close every port except what you explicitly need. Block well-known attack ports proactively.

### Why
Even with strong SSH, every open port is a potential foothold. UFW (Uncomplicated Firewall) gives the Droplet its own firewall, independent of DigitalOcean's Cloud Firewall.

### Important: order matters

We will write all the rules first, then enable UFW last. If we enabled UFW first with default-deny, our SSH session would die.

### Commands (run as root in Terminal A)

```bash
# Default policies: deny incoming, allow outgoing
ufw default deny incoming
ufw default allow outgoing

# === Required for your experiment ===

# SSH
ufw allow 22/tcp comment 'SSH'

# Your autoscale app NodePort (so k6 from your Mac can hit it)
ufw allow 30080/tcp comment 'Autoscale-sample NodePort'

# Grafana (we'll port-forward it to bind to the Droplet's interface)
ufw allow 3000/tcp comment 'Grafana'

# Prometheus UI (we'll port-forward this too)
ufw allow 9090/tcp comment 'Prometheus UI'

# === Cluster internal (k3s API; needed if you ever add a worker node) ===
# Optional - your cluster is single-node, you can omit these if you want.
# But k3s itself binds to them, so allowing them avoids confusion.
ufw allow 6443/tcp comment 'k3s API'

# === Explicitly deny common attack ports (defense in depth) ===
ufw deny 23/tcp comment 'Telnet'
ufw deny 21/tcp comment 'FTP'
ufw deny 53/tcp comment 'DNS TCP (recursive resolver - we are not one)'
ufw deny 53/udp comment 'DNS UDP'
ufw deny 80/tcp comment 'HTTP (Traefik - unused)'
ufw deny 443/tcp comment 'HTTPS (Traefik - unused)'
ufw deny 3306/tcp comment 'MySQL'
ufw deny 5432/tcp comment 'PostgreSQL'
ufw deny 6379/tcp comment 'Redis'
ufw deny 27017/tcp comment 'MongoDB'
ufw deny 9200/tcp comment 'Elasticsearch'
ufw deny 11211/tcp comment 'Memcached'

# === Enable ===
ufw --force enable

# === Verify ===
ufw status verbose
```

### Verify

`ufw status verbose` should show all the rules and "Status: active". Then, from your Mac:

```bash
# Should still work
ssh chamodi@206.189.133.70

# Should still work (Grafana via port-forward we set up earlier)
curl http://206.189.133.70:3000 -I
# Expected: HTTP/1.1 302 Found or similar

# Should fail (port 80 now blocked)
curl http://206.189.133.70:80 -m 5
# Expected: connection refused or timeout
```

### What could go wrong

- If SSH breaks: use DO Web Console as root, run `ufw disable`, fix the rule, re-enable.
- If kubectl from inside the Droplet breaks: that means UFW is blocking the local API server. Run `ufw allow from 127.0.0.1` as a quick fix.

---

## Stage 6 — Fail2Ban

### Goal
Auto-ban IPs that fail SSH login.

### Why
Even with key-only SSH, brute-force attempts waste log space and CPU. fail2ban tails `/var/log/auth.log` and uses UFW to ban offending IPs.

### Commands (run as root)

```bash
cat << 'EOF' > /etc/fail2ban/jail.local
[DEFAULT]
# After 5 fails in 10 minutes, ban for 1 hour by default
bantime = 3600
findtime = 600
maxretry = 5
banaction = ufw

[sshd]
enabled = true
port = 22
filter = sshd
logpath = /var/log/auth.log
# Stricter for SSH: 3 fails in 5 minutes = 2 hour ban
maxretry = 3
findtime = 300
bantime = 7200
EOF

systemctl enable fail2ban
systemctl restart fail2ban
```

### Verify

```bash
fail2ban-client status sshd
# Should show jail status with Currently failed: 0, Currently banned: 0
```

After a few hours, run it again — you should start seeing banned IPs (the brute-force scanners we saw earlier in `lastb`).

---

## Stage 7 — Kernel Hardening (sysctl)

### Goal
Enable Linux kernel-level network and process protections.

### Why
The kernel has dozens of security knobs that ship in moderate-default mode. Cranking them up costs nothing and protects against common attack patterns: SYN floods, IP spoofing, ICMP-based info leaks, kernel info disclosure, etc.

### Commands (run as root)

```bash
cat << 'EOF' > /etc/sysctl.d/99-hardening.conf
# IP Spoofing protection (reverse-path filter)
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Ignore ICMP broadcasts (no participation in smurf attacks)
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Disable source routing (attackers can't dictate path)
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0

# Disable ICMP redirects (prevents MITM)
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0

# SYN flood protection
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_syn_retries = 5

# Log suspicious "martian" packets (spoofed/malformed)
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1

# TIME-WAIT assassination protection
net.ipv4.tcp_rfc1337 = 1

# Restrict ptrace (debugging) to direct children only
kernel.yama.ptrace_scope = 1

# Restrict dmesg (kernel log) to root
kernel.dmesg_restrict = 1

# Disable magic SysRq key
kernel.sysrq = 0

# Full ASLR (address-space layout randomization)
kernel.randomize_va_space = 2
EOF

# Apply immediately (no reboot needed for sysctl)
sysctl -p /etc/sysctl.d/99-hardening.conf
```

### Note on what we are skipping
The peer's guide includes `net.ipv4.ip_forward = 0` and `net.ipv6.conf.all.forwarding = 0`. **We must NOT set these to 0** because k3s relies on IP forwarding for pod-to-pod and pod-to-service traffic. Setting it off would break the cluster networking. Leave it as k3s configured it.

The peer's guide also has `vm.max_map_count = 262144` for OpenSearch — we don't need this since you're not running OpenSearch.

### Verify

```bash
sysctl net.ipv4.tcp_syncookies
# Should print: net.ipv4.tcp_syncookies = 1

sysctl kernel.randomize_va_space
# Should print: kernel.randomize_va_space = 2

# Sanity check: kubectl should still work
kubectl get nodes
```

---

## Stage 8 — Audit Logging (auditd)

### Goal
Record every change to security-sensitive files and every privileged action.

### Why
If something does go wrong later, you want a forensic trail. Auditd is what professional ops teams use.

### Commands (run as root)

```bash
cat << 'EOF' > /etc/audit/rules.d/hardening.rules
# Monitor authentication files
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/gshadow -p wa -k identity

# Monitor sudo configuration and use
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d/ -p wa -k sudoers
-w /usr/bin/sudo -p x -k privilege_escalation

# Monitor SSH configuration
-w /etc/ssh/sshd_config -p wa -k sshd_config
-w /etc/ssh/sshd_config.d/ -p wa -k sshd_config

# Monitor cron (attackers love cron for persistence)
-w /etc/crontab -p wa -k cron
-w /etc/cron.d/ -p wa -k cron
-w /etc/cron.daily/ -p wa -k cron
-w /etc/cron.hourly/ -p wa -k cron

# Monitor network config
-w /etc/hosts -p wa -k network
-w /etc/netplan/ -p wa -k network

# Monitor kernel module load/unload
-w /sbin/insmod -p x -k modules
-w /sbin/rmmod -p x -k modules
-w /sbin/modprobe -p x -k modules

# Log failed file access (helpful for spotting probing)
-a always,exit -F arch=b64 -S all -F exit=-EACCES -k access_failure
-a always,exit -F arch=b64 -S all -F exit=-EPERM -k permission_failure
EOF

# Load the rules
augenrules --load
systemctl enable auditd
systemctl restart auditd
```

### Verify

```bash
auditctl -l
# Should list all the -w and -a rules
```

You can browse the audit log later with:

```bash
ausearch -k identity      # any change to passwd/shadow/group
ausearch -k sshd_config   # any change to SSH config
ausearch -k privilege_escalation  # every sudo invocation
```

---

## Stage 9 — Disable Unnecessary Services

### Goal
Stop services that have no business on a research server.

### Why
Every running service is potential attack surface. Bluetooth, printing, network discovery — none belong on a Droplet.

### Commands (run as root)

```bash
# List running services first, so you have a "before" record
systemctl list-units --type=service --state=running > /tmp/services-before.txt

# Disable common unnecessary services. The `2>/dev/null || true` swallows
# errors if the service doesn't exist on your system - that's fine.
systemctl disable --now bluetooth 2>/dev/null || true
systemctl disable --now cups 2>/dev/null || true
systemctl disable --now cups-browsed 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true
systemctl disable --now ModemManager 2>/dev/null || true

# Verify what's still listening on the network
ss -tlnup
```

### Verify

`ss -tlnup` should show only ports for services you actually need: sshd, k3s/kubelet (port 10250 on localhost), Prometheus/Grafana via their cluster IPs, etc. Anything unexpected? Tell me.

### AppArmor sanity check

```bash
aa-status
# Should show many enforced profiles. AppArmor came pre-enabled on Ubuntu.
```

---

## Stage 10 — File Integrity Monitoring (AIDE)

### Goal
Take a cryptographic snapshot of every system file. Detects tampering.

### Why
If an attacker ever does modify `/bin/ls`, `/usr/sbin/sshd`, or any other system binary, AIDE notices. It's a tripwire for advanced persistent threats.

### Commands (run as root)

```bash
# Initialize the database (this takes 5-10 minutes - it hashes everything)
aideinit

# Move the new database into place
mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db

# Schedule daily integrity check via cron (emails root on changes)
cat << 'EOF' > /etc/cron.d/aide-check
0 3 * * * root /usr/bin/aide --check 2>&1 | mail -s 'AIDE Report' root || true
EOF
```

### Verify

```bash
# Run a check manually - should show no changes since the database
# was just initialized
aide --check
# Output ends with "All files match AIDE database. Looks okay!"
```

Future AIDE warnings: legitimate ones (kernel updates, package upgrades) will show up after `apt upgrade`. You'll re-baseline with `aide --update && mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db` after each known maintenance window.

---

## Stage 11 — Lynis Security Audit

### Goal
Run an automated security scanner and review its findings.

### Why
Lynis grades your hardening (0–100) and prints specific suggestions you can address. It's the "report card" step.

### Commands (run as root)

```bash
lynis audit system
# This takes 2-3 minutes. It will scroll a lot of output.

# After it finishes, look at warnings and suggestions
grep -E "^(Warning|Suggestion)" /var/log/lynis-report.dat
```

### What to do with the output

- **Warnings** — address these. Tell me what you see and we'll work through them.
- **Suggestions** — review; many are "nice to have" but not critical. Decide case-by-case.
- **Hardening index** — a score from 0 (none) to 100 (paranoid). After this guide you should be above 75. The friend's setup with full hardening typically hits 80–85.

Save the output to your workspace:

```bash
lynis show report > /tmp/lynis-report.txt
# Then on your Mac: scp chamodi@206.189.133.70:/tmp/lynis-report.txt ~/Documents/
```

---

## Stage 12 — Reboot + Final Verification

### Goal
Apply the pending kernel update (from Stage 1) and verify everything still works after a clean boot.

### Why
The Stage 1 `apt full-upgrade` installed a new kernel. The OS is still running the old kernel until we reboot. Best to do this last so all hardening is in place when we come back up.

### Before rebooting

Make a note of currently running things:

```bash
# Capture the current state for comparison
kubectl get nodes
kubectl get pods -A
kubectl get hpa -n autoscale-research
ufw status verbose
systemctl is-active sshd fail2ban auditd
```

### Commands

```bash
# Set the hostname while we're at it (fixes the cosmetic mismatch)
hostnamectl set-hostname k3s-research

# Reboot
reboot
```

### After reboot (give it ~60 seconds)

From your Mac:

```bash
ssh chamodi@206.189.133.70
```

Then on the Droplet:

```bash
# All services should be Active
systemctl is-active sshd fail2ban auditd unattended-upgrades

# Cluster should be healthy
kubectl get nodes
kubectl get pods -A
kubectl get hpa -n autoscale-research

# UFW should still be active with our rules
sudo ufw status verbose

# Pending updates should be 0
apt list --upgradable 2>/dev/null | wc -l   # should print "1" (header only)

# Hostname should be updated
hostname
# Should print: k3s-research
```

### If anything is wrong, paste the output to me and we debug.

---

## Post-Hardening Checklist

After Stage 12 completes:

- [ ] OS fully patched, kernel current
- [ ] `chamodi` user with sudo + SSH key working
- [ ] Root SSH login disabled
- [ ] Password SSH login disabled
- [ ] SSH restricted to `chamodi` user only
- [ ] UFW firewall enabled, only required ports open
- [ ] fail2ban watching SSH
- [ ] unattended-upgrades enabled
- [ ] Kernel sysctl hardening applied
- [ ] auditd recording security-sensitive events
- [ ] Unnecessary services disabled
- [ ] AIDE baseline established, daily check scheduled
- [ ] Lynis audit run, warnings reviewed
- [ ] Hostname fixed to `k3s-research`
- [ ] Cluster healthy, all original pods Running
- [ ] HPA watcher still capturing decisions (check `data/hpa-events-*.jsonl`)

---

## Daily Operating Procedures (after hardening)

**Logging in:**
```bash
ssh chamodi@206.189.133.70
```

**Accessing Grafana from your Mac:**

```bash
# On the Droplet
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 --address 0.0.0.0
# Then in your browser: http://206.189.133.70:3000
```

**Accessing Prometheus from your Mac:**
```bash
# On the Droplet
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090 --address 0.0.0.0
# Then in your browser: http://206.189.133.70:9090
```

**Running load tests against the app:**
```bash
# On your Mac
k6 run experiment-setup/06-load-tests/step-load.js -e TARGET=http://206.189.133.70:30080
```

**Reviewing security:**
```bash
# Banned IPs
sudo fail2ban-client status sshd

# Recent failed SSH attempts
sudo lastb -F | head -20

# Audit events
sudo ausearch -k privilege_escalation | tail -20

# File integrity status
sudo aide --check
```

---

## Recovery / Rollback Procedures

### If SSH breaks
1. Open the DigitalOcean Web Console (browser).
2. Log in as root (web console bypasses sshd_config).
3. Diagnose: `journalctl -u ssh -n 50`, `sshd -t`, `cat /etc/ssh/sshd_config.d/99-hardening.conf`.
4. Fix and `systemctl reload ssh`.

### If UFW locked you out
1. Web console as root.
2. `ufw disable` to drop all firewall rules.
3. Fix rules, re-enable.

### If the cluster is broken
1. Web console as root.
2. `systemctl status k3s` — should be active.
3. `journalctl -u k3s -n 100` — look for errors.
4. Last resort: restore from the May 13 snapshot.

### If you genuinely cannot recover
DigitalOcean → Droplets → your droplet → Backups & Snapshots → click the **...** next to the snapshot → **Restore Droplet**. Takes ~5 minutes. You lose all changes since the snapshot was taken, but the Droplet returns to the exact pre-hardening state.

---

*End of guide. Refer to specific Stage section above when running each step.*
