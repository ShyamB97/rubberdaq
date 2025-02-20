import utils
from rich import print

dmi = utils.parse_output(utils.run_command("np02-srv-004", 'sudo dmidecode -t slot | grep -e Designation -e "Bus Address" -e "Current Usage"'))

print(dmi)

pcie_map = {}

for i in range(0, len(dmi)-1, 3):
    slot = dmi[i].split(": ")[-1]
    addr = dmi[i + 2].split(": ")[-1]
    usage = dmi[i + 1].split(": ")[-1]
    pcie_map[addr] = f"{slot}|{usage}"


lspci = utils.parse_output(utils.run_command("np02-srv-004", 'lspci -D'))

lspci_numa = utils.parse_output(utils.run_command("np02-srv-004", 'lspci -vv -m | grep -Ew "Device:|NUMANode:"'))


addr_numa = {}
for i in range(0, len(lspci_numa)-1, 3):
    addr = lspci_numa[i].split("\\t")[-1]
    numa = lspci_numa[i+2].split("\\t")[-1]
    addr_numa[f"0000:{addr}"] = numa

pcie_map_dev = {}

for a in pcie_map:
    dev = None
    for i in lspci:
        if a in i:
            dev = i
            break

    pcie_map_dev[pcie_map[a]] = [dev, addr_numa.get(a)]

print(pcie_map_dev)