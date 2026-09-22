#!/usr/bin/env python3

import argparse
import hashlib
import subprocess
from pathlib import Path

BRIDGE = "cni0"
BRIDGE_IP = "10.244.0.1/24"
GATEWAY = "10.244.0.1"


def run(*args):
    print("+", " ".join(args))
    subprocess.run(args, check=True)


def namespace_exists(name):
    result = subprocess.run(
        ["ip", "netns", "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return any(line.split()[0] == name for line in result.stdout.splitlines())


def link_exists(name):
    return subprocess.run(
        ["ip", "link", "show", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def add_network(name, ip_address):
    if namespace_exists(name):
        raise SystemExit(f"Namespace {name!r} already exists")

    # Linux interface names can only be 15 characters long.
    short_id = hashlib.sha1(name.encode()).hexdigest()[:6]
    host_veth = f"tc-{short_id}-h"
    peer_veth = f"tc-{short_id}-p"

    # Create the bridge if necessary.
    if not link_exists(BRIDGE):
        run("ip", "link", "add", BRIDGE, "type", "bridge")

    run("ip", "addr", "replace", BRIDGE_IP, "dev", BRIDGE)
    run("ip", "link", "set", BRIDGE, "up")

    # Create an isolated network namespace.
    run("ip", "netns", "add", name)

    # Create the virtual Ethernet cable.
    run(
        "ip", "link", "add", host_veth,
        "type", "veth",
        "peer", "name", peer_veth,
    )

    # Move one end into the namespace.
    run("ip", "link", "set", peer_veth, "netns", name)

    # Plug the host end into our Linux bridge.
    run("ip", "link", "set", host_veth, "master", BRIDGE)
    run("ip", "link", "set", host_veth, "up")

    # Inside the namespace, make the interface look container-like.
    run("ip", "netns", "exec", name, "ip", "link", "set", "lo", "up")
    run(
        "ip", "netns", "exec", name,
        "ip", "link", "set", peer_veth, "name", "eth0",
    )
    run("ip", "netns", "exec", name, "ip", "link", "set", "eth0", "up")

    # Assign the container IP and gateway.
    run(
        "ip", "netns", "exec", name,
        "ip", "addr", "add", ip_address, "dev", "eth0",
    )
    run(
        "ip", "netns", "exec", name,
        "ip", "route", "add", "default", "via", GATEWAY,
    )

    # Namespace-specific DNS configuration.
    dns_dir = Path("/etc/netns") / name
    dns_dir.mkdir(parents=True, exist_ok=True)
    (dns_dir / "resolv.conf").write_text("nameserver 1.1.1.1\n")

    print()
    print(f"Created {name}")
    print(f"  IP:      {ip_address}")
    print(f"  Gateway: {GATEWAY}")
    print(f"  Bridge:  {BRIDGE}")
    print(f"  Host veth: {host_veth}")


def main():
    parser = argparse.ArgumentParser(description="Tiny CNI network creator")
    parser.add_argument("name", help="network namespace name")
    parser.add_argument("ip", help="namespace IPv4 address with prefix")
    args = parser.parse_args()

    add_network(args.name, args.ip)


if __name__ == "__main__":
    main()
