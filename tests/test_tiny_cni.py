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


def test_check_network_healthy(monkeypatch, capsys):
    monkeypatch.setattr(tiny_cni, "namespace_exists", lambda name: True)
    monkeypatch.setattr(tiny_cni, "link_exists", lambda name: True)

    results = iter([
        type("Result", (), {
            "stdout": '[{"ifname":"eth0","addr_info":[{"family":"inet","local":"10.244.0.2","prefixlen":24}]}]',
            "returncode": 0,
        })(),
        type("Result", (), {
            "stdout": "default via 10.244.0.1 dev eth0\n",
            "returncode": 0,
        })(),
        type("Result", (), {"stdout": "", "returncode": 0})(),
        type("Result", (), {"stdout": "", "returncode": 0})(),
    ])

    monkeypatch.setattr(
        tiny_cni.subprocess,
        "run",
        lambda *args, **kwargs: next(results),
    )

    tiny_cni.check_network("cni-a")

    output = capsys.readouterr().out

    assert "Namespace:  OK" in output
    assert "IPv4:       10.244.0.2/24" in output
    assert "Gateway:    OK" in output
    assert "Bridge:     OK" in output
    assert "DNS:        OK" in output
    assert "Internet:   OK" in output


def test_check_network_missing_namespace(monkeypatch):
    monkeypatch.setattr(tiny_cni, "namespace_exists", lambda name: False)

    with pytest.raises(SystemExit) as error:
        tiny_cni.check_network("missing")

    assert error.value.code == 1


def test_check_network_reports_failures(monkeypatch, capsys):
    monkeypatch.setattr(tiny_cni, "namespace_exists", lambda name: True)
    monkeypatch.setattr(tiny_cni, "link_exists", lambda name: False)

    results = iter([
        type("Result", (), {
            "stdout": '[{"ifname":"eth0","addr_info":[]}]',
            "returncode": 0,
        })(),
        type("Result", (), {
            "stdout": "",
            "returncode": 0,
        })(),
        type("Result", (), {"stdout": "", "returncode": 1})(),
        type("Result", (), {"stdout": "", "returncode": 1})(),
    ])

    monkeypatch.setattr(
        tiny_cni.subprocess,
        "run",
        lambda *args, **kwargs: next(results),
    )

    tiny_cni.check_network("cni-a")

    output = capsys.readouterr().out

    assert "IPv4:       FAIL" in output
    assert "Gateway:    FAIL" in output
    assert "Bridge:     FAIL" in output
    assert "DNS:        FAIL" in output
    assert "Internet:   FAIL" in output
