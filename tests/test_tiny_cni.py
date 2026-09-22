import ipaddress

import pytest

from src import tiny_cni


def test_interface_names_are_stable():
    first = tiny_cni.interface_names("cni-a")
    second = tiny_cni.interface_names("cni-a")

    assert first == second
    assert first[0].startswith("tc-")
    assert first[0].endswith("-h")
    assert first[1].endswith("-p")


def test_different_names_get_different_interfaces():
    assert tiny_cni.interface_names("cni-a") != tiny_cni.interface_names("cni-b")


@pytest.mark.parametrize(
    "name",
    [
        "cni-a",
        "cni_b",
        "test123",
        "A",
    ],
)
def test_valid_namespace_names(name):
    tiny_cni.validate_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "-bad",
        "_bad",
        "has space",
        "bad.name",
    ],
)
def test_invalid_namespace_names(name):
    with pytest.raises(SystemExit):
        tiny_cni.validate_name(name)


def test_valid_ip():
    tiny_cni.validate_ip("10.244.0.50/24")


@pytest.mark.parametrize(
    "address",
    [
        "10.244.0.1/24",
        "10.244.0.0/24",
        "10.244.0.255/24",
        "10.244.1.5/24",
        "10.244.0.5/16",
        "not-an-ip",
    ],
)
def test_invalid_ip(address):
    with pytest.raises(SystemExit):
        tiny_cni.validate_ip(address)


def test_choose_requested_ip(monkeypatch):
    monkeypatch.setattr(
        tiny_cni,
        "used_ipv4_addresses",
        lambda: set(),
    )

    assert tiny_cni.choose_ip("10.244.0.20/24") == "10.244.0.20/24"


def test_choose_ip_rejects_used_address(monkeypatch):
    monkeypatch.setattr(
        tiny_cni,
        "used_ipv4_addresses",
        lambda: {ipaddress.IPv4Address("10.244.0.20")},
    )

    with pytest.raises(SystemExit, match="already in use"):
        tiny_cni.choose_ip("10.244.0.20/24")


def test_choose_ip_automatically_selects_first_free_address(monkeypatch):
    monkeypatch.setattr(
        tiny_cni,
        "used_ipv4_addresses",
        lambda: set(),
    )

    assert tiny_cni.choose_ip(None) == "10.244.0.2/24"
