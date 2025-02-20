#!/usr/bin/env python
import argparse
import sys

from abc import ABC

from rich import print

import importlib  
auto_discovery = importlib.import_module("auto-discovery")

class Resource(ABC):
    """ A component of the Host that can be used by the TDAQ. """
    name : str

    def __init__(self, name : str):
        self.name = name

    def __repr__(self):
        return f"{self.__class__}:{vars(self)}"


class RAID(Resource):
    """ A RAID controller adapter. """
    symlink : str
    drives : list[str]

    def __init__(self, name : str, symlink : str, drives : list[str]):
        self.symlink = symlink
        self.drives = drives
        super().__init__(name)


class NUMADevice(Resource):
    """ A device allocated to a specific NUMA region. """
    numa : str
    pcie : str
    product : str

    def __init__(self, name : str, pcie : str, numa : int, product : str):
        self.pcie = pcie
        self.numa = numa
        self.product = product
        super().__init__(name)


class NetworkDevice(NUMADevice):
    """ A network recieving/transmitting device. """
    mac : str
    ip : str

    def __init__(self, name : str, pcie : str, numa : int, product : str, mac : str, ip : str):
        self.mac = mac
        self.ip = ip
        super().__init__(name, pcie, numa, product)


class NVMe(NUMADevice):
    """ An NVMe device. """
    def __init__(self, name : str, pcie : str, numa : int, product : str):
        super().__init__(name, pcie, numa, product)


class Node(Resource):
    """ a NUMA node/region. """
    numa : int
    cpus : list[int]
    size : str
    devices : list[NUMADevice]

    def __init__(self, name : str, numa : int, cpus : list[int], size : str, devices : list[NUMADevice]):
        self.numa = numa
        self.cpus = cpus
        self.size = size
        self.devices = devices
        super().__init__(name)


class Host:
    """ A host, which is a collection of Resources. """
    name : str
    raid : list[RAID]
    numa : list[Node]

    def __repr__(self):
        return str(vars(self))

    def __init__(self, name : str, raid : list[RAID] = [], numa : list[Node] = []):
        self.name = name
        self.raid = raid
        self.numa = numa


def main(args : argparse.Namespace):
    info = auto_discovery.get_info(['Ethernet', 'Non-Volatile', 'Xilinx', 'CERN'])
    host = Host(info["host"])

    for r in info["raid"].values():
        host.raid.append(RAID(r["device"].split("-> ")[-1], r["symlink"], r["drives"]))

    for k, v in info["numa"].items():
        node = Node("name", int(k), v["cpus"], v["size"], [])
        for d in v["devices"]:
            if "network" in d[1]["id"]:
                mac = "not found"
                if "serial" in d[1]:
                    mac = d[1]["serial"]
                ip = "not found"
                if "ip" in d[1]["configuration"]:
                    ip = d[1]["configuration"]["ip"]

                if "logicalname" in d[1]:
                    name = d[1]["logicalname"]
                else:
                    name = d[1]["id"] # logical names are missing if hugepages are setup for the 100G NICs

                node.devices.append(NetworkDevice(name, d[0], int(k), d[1]["product"], mac, ip))
            elif "nvme" in d[1]["id"]:
                node.devices.append(NVMe(d[1]["logicalname"].split("/")[-1], d[0], int(k), d[1]["product"]))
            else:
                node.devices.append(NUMADevice(d[1], d[0], int(k), d[1]))
        host.numa.append(node)

    print(f"{host.name=}")
    for node in host.numa:
        print(f"{node.numa=}")
        print(f"{node.cpus=}")
        print(f"{node.name=}")
        print(f"{node.size=}")
        print("node.devices=")
        print(node.devices)

    return


if __name__ == "__main__":
    desc='Discover hardware setup and available resources. Necessary tools installed: lspci, numactl, mdadm, nvme-cli'
    parser = argparse.ArgumentParser(description=desc)
    try:
        args = parser.parse_args()
    except:
        parser.print_help()
        sys.exit(0)

    main(args)