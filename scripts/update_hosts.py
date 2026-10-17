#!/usr/bin/env python3
import os
import docker

# -------------------------
# Configuration
# -------------------------
DOMAIN = os.environ.get("DOMAIN", "docker.local")
HOSTS_FILE = "/etc/coredns/hosts"
NETWORK_PREFIXES = os.environ.get("NETWORK_PREFIX", "macvlan").split(",")
FALLBACK_IP = os.environ.get("FALLBACK_IP", "0.0.0.0")

# Docker client
client = docker.from_env()

# -------------------------
# Address selection
# -------------------------
def resolve_ip(net_info):
    """The address to publish for one container-on-network attachment.

    "IPAddress" is the *runtime* address: Docker populates it while the container runs and clears
    it as soon as it stops. Taking it alone meant every stopped container fell through to
    FALLBACK_IP and published a 0.0.0.0 record, even though a statically assigned container knows
    its address perfectly well -- Docker keeps it under IPAMConfig.IPv4Address whatever the
    container's state. That mattered most for exactly the containers meant to sit stopped: the
    on-demand ones started by a docker-watchdog only on request, whose DNS has to work *before*
    anything starts them.

    FALLBACK_IP still applies to an attachment with no static address configured, which is the
    case it was there for.
    """
    runtime = net_info.get("IPAddress")
    if runtime:
        return runtime

    ipam = net_info.get("IPAMConfig") or {}
    configured = ipam.get("IPv4Address")
    if configured:
        return configured

    return FALLBACK_IP


# -------------------------
# Generate hosts file
# -------------------------
def generate_hosts():
    # Include all containers, even stopped ones. ignore_removed: a container destroyed between the
    # list and its inspect is skipped instead of failing the whole update with "No such container".
    containers = client.containers.list(all=True, ignore_removed=True)
    lines = []

    for c in containers:
        name = c.name
        networks = c.attrs["NetworkSettings"]["Networks"]

        # Only add an entry for networks matching one of the configured prefixes
        for net_name, net_info in networks.items():
            if not any(net_name.startswith(prefix.strip()) for prefix in NETWORK_PREFIXES):
                continue

            ip = resolve_ip(net_info)
            fqdn = f"{name}.{DOMAIN}"
            lines.append(f"{ip}\t{fqdn} {name}")

    # Write to hosts file
    with open(HOSTS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✅ Updated {HOSTS_FILE} with {len(lines)} entries "
          f"for networks: {', '.join(NETWORK_PREFIXES)}")

# -------------------------
# Event listener
# -------------------------
# Events that can change an address. Matched exactly: a substring match also caught
# "exec_start: <cmd>" and "exec_die", so every healthcheck rewrote the hosts file (and logged the
# command line). Network attach/detach arrive as Type "network", not "container".
RELEVANT_EVENTS = {
    "container": {"start", "die", "destroy"},
    "network": {"connect", "disconnect"},
}


def event_listener():
    for event in client.events(decode=True, filters={"type": list(RELEVANT_EVENTS)}):
        kind = event.get("Type", "")
        action = event.get("Action", "")
        if action in RELEVANT_EVENTS.get(kind, ()):
            print(f"🔁 Docker event: {kind} {action} - updating hosts file...")
            try:
                generate_hosts()
            except Exception as e:
                print(f"❌ Error updating hosts: {e}")

# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    generate_hosts()  # Initial population
    print(f"📡 Listening for Docker events "
          f"with domain '{DOMAIN}' and network prefixes '{', '.join(NETWORK_PREFIXES)}' ...")
    event_listener()