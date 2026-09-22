#!/usr/bin/env python3

import argparse
import fcntl
import hashlib
import json
import ipaddress
import re
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



def validate_name(name):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}", name):
        raise SystemExit(
            "Use a namespace name of 1–63 letters, numbers, "
            "hyphens, or underscores; start with a letter or number."
        )


def validate_ip(value):
    try:
        address = ipaddress.IPv4Interface(value)
    except ValueError:
        raise SystemExit("Use an IPv4 address with /24, such as 10.244.0.5/24")

    network = ipaddress.IPv4Network(BRIDGE_IP, strict=False)
    if address.network != network:
        raise SystemExit(f"Address must belong to {network} and use /24")

    reserved = {
        network.network_address,
        network.broadcast_address,
        ipaddress.IPv4Address(GATEWAY),
    }
    if address.ip in reserved:
        raise SystemExit("That address is reserved for the network, gateway, or broadcast")



def used_ipv4_addresses():
    def read_json(*args):
        result = subprocess.run(
            args, capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)

    used = set()

    def collect(interfaces):
        for interface in interfaces:
            for address in interface.get("addr_info", []):
                if address.get("family") == "inet":
                    used.add(ipaddress.IPv4Address(address["local"]))

    collect(read_json("ip", "-j", "-4", "addr", "show"))

    for namespace in read_json("ip", "-j", "netns", "list"):
        collect(read_json(
            "ip", "-n", namespace["name"],
            "-j", "-4", "addr", "show",
        ))

    return used


def choose_ip(requested):
    network = ipaddress.IPv4Network(BRIDGE_IP, strict=False)

    if requested is not None:
        validate_ip(requested)

    used = used_ipv4_addresses()
    used.add(ipaddress.IPv4Address(GATEWAY))

    if requested is not None:
        address = ipaddress.IPv4Interface(requested)
        if address.ip in used:
            raise SystemExit(f"Address {address.ip} is already in use")
        return str(address)

    for address in network.hosts():
        if address not in used:
            return f"{address}/{network.prefixlen}"

    raise SystemExit(f"No available addresses in {network}")


def add_network(name, ip_address):
    validate_name(name)
    if namespace_exists(name):
        raise SystemExit(f"Namespace {name!r} already exists")

    ip_address = choose_ip(ip_address)

    host_veth, peer_veth = interface_names(name)

    dns_dir = Path("/etc/netns") / name
    if dns_dir.exists() or dns_dir.is_symlink():
        raise SystemExit(f"DNS directory already exists: {dns_dir}")
    if link_exists(host_veth) or link_exists(peer_veth):
        raise SystemExit("An endpoint interface already exists; inspect it first.")

    if not link_exists(BRIDGE):
        run("ip", "link", "add", BRIDGE, "type", "bridge")

    run("ip", "addr", "replace", BRIDGE_IP, "dev", BRIDGE)
    run("ip", "link", "set", BRIDGE, "up")

    rollback_actions = []
    try:
        run("ip", "netns", "add", name)
        rollback_actions.append(lambda: run("ip", "netns", "del", name))

        run(
            "ip", "link", "add", host_veth,
            "type", "veth",
            "peer", "name", peer_veth,
        )

        rollback_actions.append(
            lambda: run("ip", "link", "del", host_veth)
            if link_exists(host_veth) else None
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
        dns_dir.mkdir(parents=True, exist_ok=False)
        rollback_actions.append(dns_dir.rmdir)
        rollback_actions.append(
            lambda: (dns_dir / "resolv.conf").unlink(missing_ok=True)
        )
        (dns_dir / "resolv.conf").write_text("nameserver 1.1.1.1\n")

    except (Exception, KeyboardInterrupt):
        print(f"Creation failed; cleaning up {name}.")
        for action in reversed(rollback_actions):
            try:
                action()
            except Exception as cleanup_error:
                print(f"Cleanup warning: {cleanup_error}")
        raise

    print()
    print(f"Created {name}")
    print(f"  IP:        {ip_address}")
    print(f"  Gateway:   {GATEWAY}")
    print(f"  Bridge:    {BRIDGE}")
    print(f"  Host veth: {host_veth}")


def delete_network(name):
    validate_name(name)
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



def list_networks():
    def read_json(*args):
        result = subprocess.run(
            args, capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)

    namespaces = read_json("ip", "-j", "netns", "list")
    rows = []

    for namespace in sorted(namespaces, key=lambda item: item["name"]):
        name = namespace["name"]
        interfaces = read_json(
            "ip", "-n", name, "-j", "-4", "addr", "show"
        )
        found = False
        for interface in interfaces:
            if interface["ifname"] == "lo":
                continue
            addresses = [
                f'{address["local"]}/{address["prefixlen"]}'
                for address in interface.get("addr_info", [])
                if address.get("family") == "inet"
            ]
            rows.append((
                name,
                interface["ifname"],
                ", ".join(addresses) or "(no IPv4 address)",
            ))
            found = True
        if not found:
            rows.append((name, "(no non-loopback interface)", "-"))

    if not rows:
        print("No named network namespaces found.")
        return

    headers = ("NAMESPACE", "INTERFACE", "IPv4 ADDRESS")
    widths = [
        max(len(row[index]) for row in [headers] + rows)
        for index in range(3)
    ]
    for row in [headers] + rows:
        print("  ".join(
            value.ljust(widths[index])
            for index, value in enumerate(row)
        ))




def check_network(name):
    validate_name(name)

    print(f"Checking {name}")
    failures = []

    if not namespace_exists(name):
        print("  Namespace:  FAIL — namespace not found")
        print("  Health:     FAILED")
        raise SystemExit(1)

    print("  Namespace:  OK")

    result = subprocess.run(
        ["ip", "-n", name, "-j", "-4", "addr", "show"],
        capture_output=True,
        text=True,
    )

    ipv4_address = None

    if result.returncode == 0:
        interfaces = json.loads(result.stdout)

        for interface in interfaces:
            if interface["ifname"] == "lo":
                continue

            for address in interface.get("addr_info", []):
                if address.get("family") == "inet":
                    ipv4_address = (
                        f'{address["local"]}/{address["prefixlen"]}'
                    )
                    break

    if ipv4_address:
        print(f"  IPv4:       {ipv4_address}")
    else:
        print("  IPv4:       FAIL — no IPv4 address found")
        failures.append("IPv4")

    gateway = subprocess.run(
        ["ip", "-n", name, "route", "show", "default"],
        capture_output=True,
        text=True,
    )

    if gateway.returncode == 0 and f"via {GATEWAY}" in gateway.stdout:
        print("  Gateway:    OK")
    else:
        print(f"  Gateway:    FAIL — default route via {GATEWAY} not found")
        failures.append("Gateway")

    if link_exists(BRIDGE):
        print("  Bridge:     OK")
    else:
        print(f"  Bridge:     FAIL — {BRIDGE} not found")
        failures.append("Bridge")

    dns = subprocess.run(
        ["ip", "netns", "exec", name, "getent", "hosts", "example.com"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if dns.returncode == 0:
        print("  DNS:        OK")
    else:
        print("  DNS:        FAIL — name resolution failed")
        failures.append("DNS")

    internet = subprocess.run(
        ["ip", "netns", "exec", name, "ping", "-c", "1", "-W", "2", "1.1.1.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if internet.returncode == 0:
        print("  Internet:   OK")
    else:
        print("  Internet:   FAIL — 1.1.1.1 unreachable")
        failures.append("Internet")

    if failures:
        print("  Health:     FAILED")
        raise SystemExit(1)

    print("  Health:     OK")



def setup_network():
    result = subprocess.run(
        ["ip", "-j", "-4", "route", "show", "default"],
        capture_output=True, text=True, check=True,
    )
    routes = json.loads(result.stdout)
    if len(routes) != 1 or "dev" not in routes[0]:
        raise SystemExit("Expected one default route with an outgoing interface.")

    uplink = routes[0]["dev"]
    if uplink == BRIDGE:
        raise SystemExit("The outgoing interface cannot be the Tiny CNI bridge.")

    subnet = str(ipaddress.IPv4Network(BRIDGE_IP, strict=False))

    def ensure_rule(table, chain, *rule):
        check = subprocess.run(
            ["iptables", "-w", "-t", table, "-C", chain, *rule],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            print(f"Rule already present in {chain}")
        elif check.returncode == 1:
            run("iptables", "-w", "-t", table, "-I", chain, "1", *rule)
        else:
            raise SystemExit(check.stderr.strip())

    docker_chain = subprocess.run(
        ["iptables", "-w", "-S", "DOCKER-USER"],
        capture_output=True, text=True,
    )
    if docker_chain.returncode == 0:
        chain = "DOCKER-USER"
    elif docker_chain.returncode == 1:
        chain = "FORWARD"
    else:
        raise SystemExit(docker_chain.stderr.strip())

    run("sysctl", "-w", "net.ipv4.ip_forward=1")

    ensure_rule(
        "filter", chain,
        "-i", BRIDGE, "-o", uplink, "-j", "ACCEPT",
    )
    ensure_rule(
        "filter", chain,
        "-i", uplink, "-o", BRIDGE,
        "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
        "-j", "ACCEPT",
    )
    ensure_rule(
        "nat", "POSTROUTING",
        "-s", subnet, "-o", uplink, "-j", "MASQUERADE",
    )

    print(f"Internet setup ready through {uplink}.")
    print("These settings apply to the running system; rerun setup after reboot.")


def main():
    parser = argparse.ArgumentParser(description="Tiny CNI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="create network endpoint")
    add_parser.add_argument("name")
    add_parser.add_argument(
        "ip", nargs="?", help="IPv4 address with /24; omit to choose automatically"
    )

    del_parser = subparsers.add_parser("del", help="delete network endpoint")
    del_parser.add_argument("name")

    subparsers.add_parser("list", help="show all named namespaces and IPv4 addresses")

    subparsers.add_parser("setup", help="configure host forwarding, firewall, and NAT")

    check_parser = subparsers.add_parser("check", help="check network endpoint health")
    check_parser.add_argument("name")

    args = parser.parse_args()

    with open("/run/tiny-cni.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "add":
            add_network(args.name, args.ip)
        elif args.command == "del":
            delete_network(args.name)
        elif args.command == "list":
            list_networks()
        elif args.command == "setup":
            setup_network()
        elif args.command == "check":
            check_network(args.name)


if __name__ == "__main__":
    main()
