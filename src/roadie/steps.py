from dataclasses import dataclass

from wright.graph import Binary, File, Service, Verdict


@dataclass(frozen=True)
class CargoPackage:
    name: str


@dataclass(frozen=True)
class DevDeploymentEnvironment:
    """The local machine: a process on a free loopback port with local storage."""


def rust_binary(package: CargoPackage) -> Binary:
    return Binary("rust_binary", {"package": package})


def openapi(server: Binary) -> File:
    return File("openapi", {"server": server})


def deploy(binary: Binary, environment: DevDeploymentEnvironment) -> Service:
    return Service("deploy", {"binary": binary, "environment": environment})


def contract_fuzz(api: File, service: Service) -> Verdict:
    return Verdict("contract_fuzz", {"api": api, "service": service})
