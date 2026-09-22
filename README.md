# Tiny CNI

Build the network a container would need, one Linux command at a time.

**CNI stands for Container Network Interface**: a standard for how container runtimes ask plugins to configure networking. Tiny CNI is a learning project that builds the underlying pieces—isolated network environments, virtual cables, a virtual switch, and IP addresses—in Python. Implementing the standard CNI plugin interface is a future step.

## What it does

One command creates a network namespace, connects it to a shared Linux bridge, assigns an IPv4 address, and sets a default route and DNS configuration.

The script includes:

- **IPAM (IP Address Management):** chooses an available address by checking Ubuntu and its named network namespaces, or accepts an address you supply.
- Validation that rejects invalid names, addresses outside the subnet, reserved addresses, and addresses already found in use.
- A shared file lock so this script's create and delete commands take turns.
- Rollback that attempts to remove newly created endpoint resources if setup fails.
- A delete command for removing an endpoint and its DNS configuration.

## Try it

Use a Linux lab machine with Python 3.8 or newer and the `ip` command from iproute2. Network changes require root privileges; run these commands from the repository directory.

Create an endpoint and let the script choose its address:

```bash
sudo python3 src/tiny_cni.py add demo-a
```

Or request a specific unused address:

```bash
sudo python3 src/tiny_cni.py add demo-b 10.244.0.20/24
```

Check the first endpoint's address and test its connection to the bridge gateway:

```bash
sudo ip netns exec demo-a ip -br addr
sudo ip netns exec demo-a ping -c 3 10.244.0.1
```

Here, `ip netns exec demo-a` means “run the following command inside demo-a's network environment.”

Remove the demo endpoints when finished:

```bash
sudo python3 src/tiny_cni.py del demo-a
sudo python3 src/tiny_cni.py del demo-b
```

## Know the addresses

| Role | Value |
|---|---|
| Endpoint subnet | `10.244.0.0/24` |
| Shared virtual switch | `cni0` |
| Bridge gateway address | `10.244.0.1` |
| Endpoint address pool | `10.244.0.2` through `10.244.0.254` |
| Network interface inside each script-created namespace | `eth0` |
| Configured public DNS server | `1.1.1.1` |

Internet access also needs host forwarding, suitable firewall rules, and either upstream routing or **NAT (Network Address Translation)**. Those were configured separately in the development lab; the Python script does not set them up. Writing a DNS configuration alone does not make that server reachable.

## Terms in plain language

| Term | Meaning |
|---|---|
| **Container runtime** | Software that starts and manages containers. |
| **CNI — Container Network Interface** | A standard through which a container runtime asks networking plugins to connect or disconnect containers. |
| **Network namespace** | An isolated network environment inside Linux, with its own interfaces, addresses, and routes. It supplies network isolation; it is not a complete container. |
| **Endpoint** | One network attachment. In this project, a namespace with its configured interface and address. |
| **Veth — virtual Ethernet pair** | Two connected virtual interfaces that work like the ends of a network cable. |
| **Bridge** | A virtual network switch that connects the endpoints. |
| **IP — Internet Protocol** | The protocol used to address and route packets between networks. An IP address identifies an interface for that communication. |
| **Subnet** | A range of addresses belonging to one network. Here, `/24` means the first 24 bits identify the network. |
| **Gateway** | The next router an endpoint sends traffic to when the destination is outside its local network. |
| **Default route** | The route used when no more specific route matches a destination. |
| **IPAM — IP Address Management** | Choosing and tracking addresses to avoid conflicts. Tiny CNI currently checks live assignments rather than keeping a persistent allocation database. |
| **DNS — Domain Name System** | Resolves names such as `example.com` to IP addresses. |
| **NAT — Network Address Translation** | Rewrites addresses as traffic crosses a router, often allowing private addresses to share an outward-facing address. |
| **Lock** | Makes cooperating commands wait their turn before changing shared resources. |
| **Rollback** | Undoes completed setup steps after a later step fails. |

## Checked in the development lab

- Gateway reachability, internet reachability by IP, and DNS resolution from an endpoint.
- Communication between two namespaces through the bridge.
- Automatic address selection and rejection of duplicate or reserved addresses.
- A command waiting for the shared lock, then continuing after release.
- Namespace and virtual-cable cleanup after a deliberately injected setup failure.

The injected failure occurred before DNS setup, so that test did not verify cleanup after a DNS-file write failure.

## Current boundaries

This is a single-host learning tool. Address discovery covers the host and its named namespaces; it does not discover every device on an external network. The lock coordinates commands using the same lock file, not unrelated networking tools.

The shared bridge remains after deletion or endpoint rollback. Network namespaces and virtual links need to be recreated after a reboot. The script currently has no persistent allocation database or standard CNI runtime integration.
