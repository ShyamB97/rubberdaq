#! python3

import argparse
import os
import sys
import psutil
import json

import subprocess
from subprocess import check_output

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

dev_dict={}
for dev in args.device:
  dev_dict[dev] = {}

##### Check NUMA
numa_dict={}
numactl_out_raw = check_output(['numactl', '-H']).decode("utf-8")
numactl_out_lines = numactl_out_raw.splitlines()
for numal in numactl_out_lines:
  if (numal.find('cpus') != -1):
    cpu_line = numal.split()
    if cpu_line[1] not in numa_dict:
      numa_dict[cpu_line[1]] = {}
    numa_dict[cpu_line[1]]['cpus'] = cpu_line[3:]
  if (numal.find('size') != -1):
    size_line = numal.split()
    numa_dict[size_line[1]]['size'] = int(size_line[3])*1024
  if (numal.find('free') != -1):
    free_line = numal.split()
    numa_dict[free_line[1]]['free'] = int(free_line[3])*1024

numa_nodes = int(numactl_out_raw.splitlines()[0].split()[1])

##### Check LSPCI
lspci_out_raw = check_output(["lspci"])
lspci_out = lspci_out_raw.decode("utf-8").splitlines()
for item in lspci_out:
  devl = item.split(' ')
  devl[1:len(devl)] = [' '.join(devl[1:len(devl)])]
  for dev in args.device:
    if (devl[1].find(dev) != -1):
      dev_dict[dev][devl[0]] = devl
      dev_lspci_out = check_output(["lspci", "-s", devl[0], "-vvvvv"])
      verbose_info = dev_lspci_out.decode("utf-8").splitlines()
      for vline in verbose_info:
        if (vline.find('NUMA') != -1):
          zone = vline.split()[2]
          dev_dict[dev][devl[0]].append(int(zone))

for nodeid in range(numa_nodes):
  numa_dict[str(nodeid)]['devices'] = []

for cat in dev_dict:
  for dev in dev_dict[cat]:
    zone = dev_dict[cat][dev][2]
    numa_dict[str(zone)]['devices'].append((dev, dev_dict[cat][dev][1]))

##### Check NVMe
nvme_dict={}
nvmesys_out = check_output(['nvme', 'list-subsys']).decode("utf-8").splitlines()
clean_sys_out = [line for line in nvmesys_out if line.find('+-') != -1]
for nvmel in clean_sys_out:
  nvmed = nvmel.split()
  nvme_dict[nvmed[1]] = {}
  nvme_dict[nvmed[1]]['pcie'] = nvmed[3][5:]

nvmelst_out = check_output(['nvme', 'list']).decode("utf-8").splitlines()
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
lsraid_out_raw = check_output(['ls','/dev/md/'])
lsraid_out = lsraid_out_raw.decode('utf-8').splitlines()
for raid_syml in lsraid_out:
  raid_dict[raid_syml] = {}
  raid_dict[raid_syml]['symlink'] = '/dev/md/'+raid_syml
  raidsyml_out_raw = check_output(['ls', '-l', raid_dict[raid_syml]['symlink']])
  raidsyml_out = raidsyml_out_raw.decode('utf-8').split()
  raid_dict[raid_syml]['device'] = '/dev/'+raidsyml_out[len(raidsyml_out)-1].replace('../', '')
  mdadm_out_raw = check_output(['mdadm', '--detail', raid_dict[raid_syml]['symlink']])
  mdadm_out = mdadm_out_raw.decode('utf-8').splitlines()
  for mdadml in mdadm_out:
    if (mdadml.find('Raid Devices') != -1):
      raid_dict[raid_syml]['raid_devices'] = int(mdadml.split()[3])
    if (mdadml.find('Total Devices') != -1):
      raid_dict[raid_syml]['total_devices'] = int(mdadml.split()[3])
    if (mdadml.find('wctive Devices') != -1):
      raid_dict[raid_syml]['active_devices'] = int(mdadml.split()[3])
    if (mdadml.find('Working Devices') != -1):
      raid_dict[raid_syml]['working_devices'] = int(mdadml.split()[3])
    if (mdadml.find('Failed Devices') != -1):
      raid_dict[raid_syml]['failed_devices'] = int(mdadml.split()[3])
  raid_devs = raid_dict[raid_syml]['raid_devices']
  #for rid in range(raid_devs):
  devs_of_rid = mdadm_out[len(mdadm_out)-raid_devs:]
  raid_dict[raid_syml]['drives'] = []
  for dev in devs_of_rid:
    dev_items = dev.split()
    raid_dict[raid_syml]['drives'].append(dev_items[len(dev_items)-1]) 

partitions = psutil.disk_partitions()
for raid in raid_dict:
  for part in partitions:
    if (part.device == raid_dict[raid]['device']):
      raid_dict[raid]['mount'] = part.mountpoint
      raid_dict[raid]['usage'] = psutil.disk_usage(part.mountpoint)
  
#### Check essentials (e.g.: services on/off)
# Check for irqbalance stopped
# Check for numad stopped
# Check for fstrim stopped systemctl status fstrim.timer
#fstrim_out_raw = check_output()

#exit(0)

##### Print info
dev_cat = dev_dict.keys()
if args.verbose:
  print('#### PCIe devices')
  print('Looked up device names in lspci:', dev_cat)
  print('Found devices:')
  for cat in dev_cat:
    dev_ids = dev_dict[cat].keys()
    print('Category', cat, ':', len(dev_ids))
    print('  -> lspci ids:', str(dev_ids))
  print('\n')

print('#### Hardware info...')
lcpu_count = len(os.sched_getaffinity(0))
pcpu_count = psutil.cpu_count(logical=False)
vmem = psutil.virtual_memory()
print('  -> Logical CPU count:', lcpu_count)
print('  -> Physical CPU count:', pcpu_count)
print('  -> NUMA nodes:', numa_nodes)
print('\n')
for numa in numa_dict:
  print('   * CPUs node', numa, numa_dict[numa]['cpus'])
  print('   * size node', numa, numa_dict[numa]['size'])
  print('   * free node', numa, numa_dict[numa]['free'])
  print('   * devs node', numa, json.dumps(numa_dict[numa]['devices'], indent=4))
  print('\n')
print('\n')

print('#### Memory status:\n', vmem)
print('\n')

if args.verbose:
  print('#### NVMe drives:')
  print(json.dumps(nvme_dict, sort_keys=True, indent=4))
  print('\n')

  print('#### RAID devices:')
  print(json.dumps(raid_dict, sort_keys=False, indent=4)) 
  print('\n')

  print('#### Full NUMA map')
  print(json.dumps(numa_dict, sort_keys=False, indent=4))
  print('\n')

if args.diag:
  print('Should run diagnostics...')
  # are raids mounted
  # irqbalance and stuff is OFF. 

exit(0)

