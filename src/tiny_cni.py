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


def interface_names(name):
    short_id = hashlib.sha1(name.encode()).hexdigest()[:6]
    return f"tc-{short_id}-h", f"tc-{short_id}-p"


def add_network(name, ip_address):
    if namespace_exists(name):
        raise SystemExit(f"Namespace {name!r} already exists")

    host_veth, peer_veth = interface_names(name)

    if not link_exists(BRIDGE):
        run("ip", "link", "add", BRIDGE, "type", "bridge")

    run("ip", "addr", "replace", BRIDGE_IP, "dev", BRIDGE)
    run("ip", "link", "set", BRIDGE, "up")

    run("ip", "netns", "add", name)

    run(
        "ip", "link", "add", host_veth,
        "type", "veth",
        "peer", "name", peer_veth,
    )

    run("ip", "link", "set", peer_veth, "netns", name)

    run("ip", "link", "set", host_veth, "master", BRIDGE)
    run("ip", "link", "set", host_veth, "up")

    run("ip", "netns", "exec", name, "ip", "link", "set", "lo", "up")

    run(
        "ip", "netns", "exec", name,
        "ip", "link", "set", peer_veth, "name", "eth0",
    )

    run(
        "ip", "netns", "exec", name,
        "ip", "link", "set", "eth0", "up",
    )

    run(
        "ip", "netns", "exec", name,
        "ip", "addr", "add", ip_address, "dev", "eth0",
    )

    run(
        "ip", "netns", "exec", name,
        "ip", "route", "add", "default", "via", GATEWAY,
    )

    dns_dir = Path("/etc/netns") / name
    dns_dir.mkdir(parents=True, exist_ok=True)
    (dns_dir / "resolv.conf").write_text("nameserver 1.1.1.1\n")

    print()
    print(f"Created {name}")
    print(f"  IP:        {ip_address}")
    print(f"  Gateway:   {GATEWAY}")
    print(f"  Bridge:    {BRIDGE}")
    print(f"  Host veth: {host_veth}")


def delete_network(name):
    host_veth, _ = interface_names(name)

    if namespace_exists(name):
        run("ip", "netns", "del", name)
    elif link_exists(host_veth):
        run("ip", "link", "del", host_veth)
    else:
        print(f"No network namespace or veth found for {name}")

    dns_dir = Path("/etc/netns") / name
    dns_file = dns_dir / "resolv.conf"

    if dns_file.exists():
        dns_file.unlink()

    if dns_dir.exists():
        try:
            dns_dir.rmdir()
        except OSError:
            pass

    print(f"Deleted {name}")


def main():
    parser = argparse.ArgumentParser(description="Tiny CNI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="create network endpoint")
    add_parser.add_argument("name")
    add_parser.add_argument("ip")

    del_parser = subparsers.add_parser("del", help="delete network endpoint")
    del_parser.add_argument("name")

    args = parser.parse_args()

    if args.command == "add":
        add_network(args.name, args.ip)
    elif args.command == "del":
        delete_network(args.name)


if __name__ == "__main__":
    main()
