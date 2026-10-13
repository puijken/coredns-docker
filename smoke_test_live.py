#!/usr/bin/env python3
"""Smoke test for the built image: real CoreDNS and update_hosts.py against a real Docker daemon.

    python3 smoke_test_live.py IMAGE

smoke_test.py checks the address-selection logic with Docker faked out. This checks the rest,
which only breaks at runtime: the image's Python and the unpinned `docker` SDK talking to a real
daemon, the event stream, the CoreDNS binary copied from the floating `coredns:latest`, and the
Corefile's hosts reload. It answers real DNS queries:

  1. a container that existed before startup gets an A and a PTR record;
  2. a container started afterwards gets one through the event listener;
  3. a stopped container keeps its static address (Docker's real stop semantics, not a fake).

Needs a Docker daemon and nothing else -- stdlib only (including the DNS client), and the test
containers are the image under test itself running `sleep`, so no other image is pulled. The
network and containers are removed afterwards whatever the outcome.
"""
import os
import random
import socket
import struct
import subprocess
import sys
import time

TAG = f"cdsmoke-{os.getpid()}"
NETWORK = f"{TAG}-net"  # also the NETWORK_PREFIX, so only this test's containers get records
COREDNS = f"{TAG}-coredns"
DOMAIN = "smoke.test"
SUBNET = "172.31.254.0/24"
# (name, static address on NETWORK)
BEFORE = (f"{TAG}-before", "172.31.254.10")
AFTER = (f"{TAG}-after", "172.31.254.11")
LATE = (f"{TAG}-late", "172.31.254.12")

QTYPE_A, QTYPE_PTR = 1, 12


class SmokeFailure(Exception):
    pass


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SmokeFailure(f"docker {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def logs(container):
    result = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Minimal DNS client (stdlib only, so the test needs no dig/dnspython)
# ---------------------------------------------------------------------------
def read_name(data, off):
    """Decode a (possibly compressed) name at `off`; return (name, offset after it)."""
    labels, end = [], None
    while True:
        length = data[off]
        if length & 0xC0 == 0xC0:
            if end is None:
                end = off + 2
            off = ((length & 0x3F) << 8) | data[off + 1]
            continue
        off += 1
        if length == 0:
            break
        labels.append(data[off:off + length].decode())
        off += length
    return ".".join(labels), end if end is not None else off


def query(port, name, qtype):
    """Answers of type `qtype` for `name` from 127.0.0.1:`port`; [] on no answer or timeout."""
    qid = random.randrange(1 << 16)
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    packet += b"".join(bytes([len(p)]) + p.encode() for p in name.strip(".").split(".")) + b"\0"
    packet += struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        try:
            s.sendto(packet, ("127.0.0.1", port))
            data = s.recv(4096)
        except OSError:
            return []

    _, _, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", data[:12])
    off = 12
    for _ in range(qdcount):
        off = read_name(data, off)[1] + 4
    answers = []
    for _ in range(ancount):
        off = read_name(data, off)[1]
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        off += 10
        if rtype == qtype == QTYPE_A:
            answers.append(socket.inet_ntoa(data[off:off + 4]))
        elif rtype == qtype == QTYPE_PTR:
            answers.append(read_name(data, off)[0])
        off += rdlen
    return answers


def wait_for_answer(port, name, qtype, expected, what, timeout=20):
    deadline = time.time() + timeout
    got = []
    while time.time() < deadline:
        got = query(port, name, qtype)
        if expected in got:
            return
        time.sleep(0.5)
    raise SmokeFailure(f"{what}: {name} answered {got!r}, expected {expected!r} "
                       f"within {timeout}s")


def ptr_name(ip):
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
def start_sleeper(image, name, ip):
    # --stop-timeout: `sleep` as PID 1 ignores SIGTERM, so `docker stop` would otherwise wait
    # the full 10s default before killing it.
    docker("run", "-d", "--name", name, "--network", NETWORK, "--ip", ip, "--stop-timeout", "1",
           "--entrypoint", "sleep", image, "600")


def run(image):
    docker("network", "create", "--subnet", SUBNET, NETWORK)
    start_sleeper(image, *BEFORE)

    docker("run", "-d", "--name", COREDNS, "-v", "/var/run/docker.sock:/var/run/docker.sock",
           "-e", f"DOMAIN={DOMAIN}", "-e", f"NETWORK_PREFIX={NETWORK}",
           "-p", "127.0.0.1::53/udp", image)
    port = int(docker("port", COREDNS, "53/udp").splitlines()[0].rsplit(":", 1)[1])

    name, ip = BEFORE
    wait_for_answer(port, f"{name}.{DOMAIN}", QTYPE_A, ip, "initial population")
    wait_for_answer(port, ptr_name(ip), QTYPE_PTR, f"{name}.{DOMAIN}", "PTR record")
    print("  existing container: A and PTR records served")

    start_sleeper(image, *AFTER)
    name, ip = AFTER
    wait_for_answer(port, f"{name}.{DOMAIN}", QTYPE_A, ip, "event listener")
    print("  container started later: record added from the Docker event stream")

    # Stop BEFORE, then start LATE: events are handled in order, so once LATE resolves the hosts
    # file has been rewritten after the stop, and BEFORE's record reflects its stopped state.
    docker("stop", BEFORE[0])
    start_sleeper(image, *LATE)
    wait_for_answer(port, f"{LATE[0]}.{DOMAIN}", QTYPE_A, LATE[1], "event after a stop")
    name, ip = BEFORE
    got = query(port, f"{name}.{DOMAIN}", QTYPE_A)
    if got != [ip]:
        raise SmokeFailure(f"stopped container answered {got!r}, expected its static "
                           f"address [{ip!r}]")
    print("  stopped container: still resolves to its static address")

    if docker("inspect", "-f", "{{.State.Running}}", COREDNS) != "true":
        raise SmokeFailure("coredns container exited")
    if "Error updating hosts" in logs(COREDNS):
        raise SmokeFailure("update_hosts.py logged an error")


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    try:
        run(sys.argv[1])
    except SmokeFailure as e:
        print(f"\nSMOKE TEST FAILED: {e}\n\n--- coredns logs ---\n{logs(COREDNS)}",
              file=sys.stderr)
        return 1
    finally:
        docker("rm", "-f", COREDNS, BEFORE[0], AFTER[0], LATE[0], check=False)
        docker("network", "rm", NETWORK, check=False)

    print("smoke test passed: A + PTR, event listener, stopped-container static address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
