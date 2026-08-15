#!/usr/bin/env python3
"""Smoke test for scripts/update_hosts.py.

Runs the real generate_hosts() against a fake Docker client and asserts the hosts file it
writes. No Docker daemon, no network -- the point is that CI can catch a records regression,
which a build-only check cannot: the image builds perfectly well while publishing wrong
addresses.

    python3 smoke_test.py
"""
import os
import sys
import tempfile
import types


# ---------------------------------------------------------------------------
# Stub out `docker` before importing the script, which calls docker.from_env()
# at module level and would otherwise need a live daemon.
# ---------------------------------------------------------------------------
class FakeContainer:
    def __init__(self, name, networks):
        self.name = name
        self.attrs = {"NetworkSettings": {"Networks": networks}}


class FakeContainers:
    def __init__(self, containers):
        self._containers = containers

    def list(self, all=False):  # noqa: A002 -- matches docker-py's signature
        return self._containers


class FakeClient:
    def __init__(self, containers=()):
        self.containers = FakeContainers(list(containers))


fake_docker = types.ModuleType("docker")
fake_docker.from_env = lambda: FakeClient()
sys.modules["docker"] = fake_docker

os.environ.setdefault("DOMAIN", "example.test")
os.environ.setdefault("NETWORK_PREFIX", "macvlan")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import update_hosts  # noqa: E402


def run(containers):
    """Run generate_hosts() over `containers`, returning {name: ip}."""
    update_hosts.client = FakeClient(containers)
    fd, path = tempfile.mkstemp()
    os.close(fd)
    original = update_hosts.HOSTS_FILE
    update_hosts.HOSTS_FILE = path
    try:
        update_hosts.generate_hosts()
        with open(path) as f:
            body = f.read()
    finally:
        update_hosts.HOSTS_FILE = original
        os.unlink(path)

    out = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        ip, names = line.split("\t", 1)
        out[names.split()[-1]] = ip
    return out


def check_running_uses_runtime_ip(failures):
    """A running container publishes the address Docker actually gave it."""
    got = run([FakeContainer("running", {
        "macvlan12": {"IPAddress": "10.0.12.50", "IPAMConfig": {"IPv4Address": "10.0.12.99"}},
    })])
    if got.get("running") != "10.0.12.50":
        failures.append(f"  running container published {got.get('running')!r}, expected the "
                        f"runtime address '10.0.12.50' (IPAMConfig must not win while running)")
        return
    print("  running container: publishes its runtime address, not the configured one")


def check_stopped_uses_configured_ip(failures):
    """The regression this test exists for.

    Docker clears IPAddress the moment a container stops but keeps IPAMConfig.IPv4Address. Reading
    only the runtime address published 0.0.0.0 for every stopped container -- including the
    on-demand ones a docker-watchdog starts, whose DNS has to resolve *before* anything starts
    them.
    """
    got = run([FakeContainer("stopped", {
        "macvlan12": {"IPAddress": "", "IPAMConfig": {"IPv4Address": "10.0.12.112"}},
    })])
    if got.get("stopped") != "10.0.12.112":
        failures.append(f"  stopped container published {got.get('stopped')!r}, expected its "
                        f"configured address '10.0.12.112' -- a stopped container with a static "
                        f"IP must still get a real record")
        return
    print("  stopped container: falls back to its configured static address")


def check_fallback_still_applies(failures):
    """FALLBACK_IP is still used when there is genuinely no address to publish."""
    cases = {
        "no-ipam": {"IPAddress": "", "IPAMConfig": None},
        "empty-ipam": {"IPAddress": "", "IPAMConfig": {}},
        "blank-ipv4": {"IPAddress": "", "IPAMConfig": {"IPv4Address": ""}},
    }
    got = run([FakeContainer(name, {"macvlan12": net}) for name, net in cases.items()])
    for name in cases:
        if got.get(name) != update_hosts.FALLBACK_IP:
            failures.append(f"  {name}: published {got.get(name)!r}, expected FALLBACK_IP "
                            f"{update_hosts.FALLBACK_IP!r}")
            return
    print(f"  no address configured: still falls back to {update_hosts.FALLBACK_IP} "
          f"(all of missing/empty/blank IPAMConfig)")


def check_network_prefix_filter(failures):
    """Only prefix-matching networks produce records; a backend bridge must not."""
    got = run([FakeContainer("backend", {
        "some_internal": {"IPAddress": "172.18.0.5", "IPAMConfig": None},
        "macvlan12": {"IPAddress": "10.0.12.60", "IPAMConfig": None},
    })])
    if got.get("backend") != "10.0.12.60":
        failures.append(f"  prefix filter: published {got.get('backend')!r}, expected only the "
                        f"macvlan address '10.0.12.60'")
        return
    print("  network prefix filter: non-matching networks produce no record")


def check_record_format(failures):
    """Each line stays `ip<TAB>fqdn shortname` -- CoreDNS's hosts plugin serves PTR from this."""
    update_hosts.client = FakeClient([FakeContainer("svc", {
        "macvlan12": {"IPAddress": "10.0.12.70", "IPAMConfig": None},
    })])
    fd, path = tempfile.mkstemp()
    os.close(fd)
    original = update_hosts.HOSTS_FILE
    update_hosts.HOSTS_FILE = path
    try:
        update_hosts.generate_hosts()
        with open(path) as f:
            line = f.read().strip()
    finally:
        update_hosts.HOSTS_FILE = original
        os.unlink(path)

    expected = f"10.0.12.70\tsvc.{update_hosts.DOMAIN} svc"
    if line != expected:
        failures.append(f"  record format: got {line!r}, expected {expected!r}")
        return
    print("  record format: 'ip<TAB>fqdn shortname' preserved")


def main():
    failures = []
    check_running_uses_runtime_ip(failures)
    check_stopped_uses_configured_ip(failures)
    check_fallback_still_applies(failures)
    check_network_prefix_filter(failures)
    check_record_format(failures)

    if failures:
        print(f"\nSMOKE TEST FAILED ({len(failures)}):", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1

    print("smoke test passed: runtime address, stopped-container fallback, FALLBACK_IP, "
          "network prefix filter, record format")
    return 0


if __name__ == "__main__":
    sys.exit(main())
