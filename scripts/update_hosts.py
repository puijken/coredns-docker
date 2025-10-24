#!/usr/bin/env python3
import os
import docker

# -------------------------
# Configuration
# -------------------------
DOMAIN = os.environ.get("DOMAIN", "docker.local")
HOSTS_FILE = "/etc/coredns/hosts"
NETWORK_PREFIXES = os.environ.get("NETWORK_PREFIX", "macvlan").split(",")

# Docker client
client = docker.from_env()

# -------------------------
# Generate hosts file
# -------------------------
def generate_hosts():
    # Include all containers, even stopped ones
    containers = client.containers.list(all=True)
    lines = []

    for c in containers:
        name = c.name
        networks = c.attrs["NetworkSettings"]["Networks"]

        for net_name, net_info in networks.items():
            # Only include networks matching configured prefixes
            if not any(net_name.startswith(prefix.strip()) for prefix in NETWORK_PREFIXES):
                continue

            ip = net_info.get("IPAddress")
            if not ip:
                continue

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
def event_listener():
    for event in client.events(decode=True):
        action = event.get("Action", "")
        if event.get("Type") == "container" and any(
            x in action for x in ["start", "die", "destroy", "connect", "disconnect"]
        ):
            print(f"🔁 Docker event: {action} - updating hosts file...")
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