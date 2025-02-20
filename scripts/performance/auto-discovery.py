#!/usr/bin/env python
import argparse
import os
import sys
import psutil
import json

import subprocess
from subprocess import check_output

from rich import print


def get_numa_info() -> tuple[dict, int]:
    numa_dict = {}
    numa_nodes = None
    numactl_out = run_cmd(['numactl', '-H'])
    if numactl_out:
        for numal in numactl_out:
            if numal.find('cpus') != -1:
                cpu_line = numal.split()
                if cpu_line[1] not in numa_dict:
                    numa_dict[cpu_line[1]] = {}
                numa_dict[cpu_line[1]]['cpus'] = [int(cpu) for cpu in cpu_line[3:]]

            for mem in ["size", "free"]:
                if numal.find(mem) != -1:
                    value = numal.split()
                    numa_dict[value[1]][mem] = int(value[3])*1024 # convert to KB

    numa_nodes = int(numactl_out[0].split()[1])

    for nodeid in range(numa_nodes):
        numa_dict[str(nodeid)]['devices'] = []

    return numa_dict, numa_nodes


def run_cmd(cmd : list[str]):
    try:
        return check_output(cmd, stderr = subprocess.STDOUT).decode("utf-8").splitlines()
    except subprocess.CalledProcessError as err:
        print(f"{cmd} ran with error: {err.output.decode('utf-8')}")


def get_info(devices : list[str]):
    dev_dict = {}
    for dev in devices:
        dev_dict[dev] = {}

    ##### Check NUMA
    numa_dict, numa_nodes = get_numa_info()

    ##### Check LSPCI
    lspci_out = run_cmd(["lspci"])
    if lspci_out:
        for item in lspci_out:
            devl = item.split(' ')
            devl[1:len(devl)] = [' '.join(devl[1:len(devl)])]
            for dev in devices:
                if devl[1].find(dev) != -1:
                    dev_dict[dev][devl[0]] = devl
                    verbose_info = run_cmd(["lspci", "-s", devl[0], "-vvvvv"])
                    for vline in verbose_info:
                        if vline.find('NUMA') != -1:
                            zone = vline.split()[2]
                            dev_dict[dev][devl[0]].append(int(zone))

        for cat in dev_dict:
            for dev in dev_dict[cat]:
                zone = dev_dict[cat][dev][2]
                if str(zone) not in numa_dict:
                    numa_dict[str(zone)] = {'devices' : []}
                else:
                    numa_dict[str(zone)]['devices'].append((dev, dev_dict[cat][dev][1]))

    ##### Check NVMe
    nvme_dict={}
    nvmesys_out = run_cmd(['nvme', 'list-subsys'])
    if nvmesys_out:
        clean_sys_out = [line for line in nvmesys_out if line.find('+-') != -1]
        for nvmel in clean_sys_out:
            nvmed = nvmel.split()
            nvme_dict[nvmed[1]] = {}
            nvme_dict[nvmed[1]]['pcie'] = nvmed[3][5:]

    nvmelst_out = run_cmd(['nvme', 'list'])
    if nvmelst_out:
        nvmelst_out = nvmelst_out[2:] # remove headers
        for nvmel in nvmelst_out:
            nvmelsplt = nvmel.split()
            nvmebrand = ' '.join(nvmelsplt[2:])
            nvmenode = nvmelsplt[0]
            nvmedev = nvmenode[5:len(nvmenode[0])-3]
            nvme_dict[nvmedev]['dev'] = nvmenode
            nvme_dict[nvmedev]['type'] = nvmebrand 

    ##### Check RAIDs
    raid_dict={}
    lsraid_out = run_cmd(['ls','/dev/md/'])
    if lsraid_out:
        for raid_syml in lsraid_out:
            raid_dict[raid_syml] = {}
            raid_dict[raid_syml]['symlink'] = '/dev/md/'+raid_syml

            raidsyml_out = run_cmd(['ls', '-l', raid_dict[raid_syml]['symlink']])
            raid_dict[raid_syml]['device'] = '/dev/'+raidsyml_out[len(raidsyml_out)-1].replace('../', '')

            mdadm_out = run_cmd(['sudo', 'mdadm', '--detail', raid_dict[raid_syml]['symlink']])
            if mdadm_out:
                for mdadml in mdadm_out:
                    if "Devices" in mdadml:
                        words = mdadml.split()
                        dev = words[0].lower()
                        value = int(words[3])
                        raid_dict[raid_syml][f'{dev}_devices'] = value
                raid_devs = raid_dict[raid_syml]['raid_devices']
                devs_of_rid = mdadm_out[len(mdadm_out)-raid_devs:]
                raid_dict[raid_syml]['drives'] = []
                for dev in devs_of_rid:
                    dev_items = dev.split()
                    raid_dict[raid_syml]['drives'].append(dev_items[len(dev_items)-1]) 

    partitions = psutil.disk_partitions()
    for raid in raid_dict:
        for part in partitions:
            if part.device == raid_dict[raid]['device']:
                raid_dict[raid]['mount'] = part.mountpoint
                raid_dict[raid]['usage'] = psutil.disk_usage(part.mountpoint)

    # Network
    lshw_out = run_cmd(["lshw", "-C", "Network", "-C", "Storage", "-json"])
    lshw_out = "".join([i for i in lshw_out if "WARNING:" not in i])
    lshw_out = json.loads(lshw_out)

    for i in lshw_out:
        if "pci" in i["businfo"]:
            pci_addr = "".join(i["businfo"].split("@")[-1])
            for k in numa_dict:
                for j in range(len(numa_dict[k]["devices"])):
                    if numa_dict[k]["devices"][j][0] == ":".join(pci_addr.split(":")[1:]):
                        numa_dict[k]["devices"][j] = (pci_addr, i)

    hostname = run_cmd(["hostname"])[0]
    return {"host" : hostname, "dev" : dev_dict, "raid" : raid_dict, "nvme" : nvme_dict, "numa" : numa_dict}


def main(args : argparse.Namespace):

    info = get_info(args.device)

    #### Check essentials (e.g.: services on/off)
    # Check for irqbalance stopped
    # Check for numad stopped
    # Check for fstrim stopped systemctl status fstrim.timer
    #fstrim_out_raw = check_output()

    ##### Print info
    dev_cat = info["dev"].keys()
    if args.verbose:
        print('#### PCIe devices')
        print('Looked up device names in lspci:', dev_cat)
        print('Found devices:')
        for cat in dev_cat:
            dev_ids = info["dev"][cat].keys()
            print('Category', cat, ':', len(dev_ids))
            print('  -> lspci ids:', str(dev_ids))

    print('#### Hardware info...')
    lcpu_count = len(os.sched_getaffinity(0))
    pcpu_count = psutil.cpu_count(logical=False)
    vmem = psutil.virtual_memory()
    print('  -> Logical CPU count:', lcpu_count)
    print('  -> Physical CPU count:', pcpu_count)

    if len(info["numa"]) > 0:
        print('  -> NUMA nodes:', len(info["numa"]))
    else:
        print("NUMA nodes was not found")

    for numa in info["numa"]:
        if ("cpus" not in info["numa"][numa]):
            print("cpus for each NUMA node could not be found")
            continue
        print('   * CPUs node', numa, *info["numa"][numa]['cpus'])
        print('   * size node', numa, info["numa"][numa]['size'])
        print('   * free node', numa, info["numa"][numa]['free'])
        print('   * devs node', numa, json.dumps(info["numa"][numa]['devices'], indent=4))

    print('#### Memory status:\n', vmem)

    if len(info["raid"]) == 0:
        print("no RAID devices were found.")
    else:
        print('#### RAID status:\n', info["raid"])

    if args.verbose:
        print('#### NVMe drives:')
        print(json.dumps(info["nvme"], sort_keys=True, indent=4))

        print('#### RAID devices:')
        print(json.dumps(info["raid"], sort_keys=False, indent=4)) 

        print('#### Full NUMA map')
        print(json.dumps(info["numa"], sort_keys=False, indent=4))

    if args.diag:
        print('Should run diagnostics...')
        # are raids mounted
        # irqbalance and stuff is OFF. 
    return


if __name__ == "__main__":
    desc='Discover hardware setup and available resources. Necessary tools installed: lspci, numactl, mdadm, nvme-cli'
    def_devs=['Ethernet', 'Non-Volatile', 'Xilinx', 'CERN']
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--device', '-d', action='append', required=False, help='device to try auto-discover')
    parser.add_argument('--diag', action='store_true', required=False, help='do quick system diagnostics')
    parser.add_argument('--verbose', '-v', action='store_true', required=False, help='verbose output')
    parser.set_defaults(device=def_devs)
    parser.set_defaults(diag=False)
    parser.set_defaults(verbose=False)

    try:
        args = parser.parse_args()
    except:
        parser.print_help()
        sys.exit(0)

    main(args)