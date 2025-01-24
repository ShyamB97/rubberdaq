#!/usr/bin/env python
"""
Created on: 05/12/2024 12:17

Author: Shyam Bhuller

Description: Create a cpu pinning file for a readout server.

#! Pinning file needs to figure out the thread names somehow...
#! rte-worker threads are predefined in the OKS configuration, must read them in.

#! quick way is to pass the script a template pinning file with thread names (and rte-worker-threads), script then assigns the core numbers appropriately
#! correct way is to read in OKS file, somehow infer names from the configuration (unclear how) and create json file.

"""
import argparse
import copy
import json
import os
import subprocess

from socket import gethostname

from rich import print

class CoreList:
    """
    A class to represent a list oc CPU cores. Cores can be retireved from the list,
    and if so, that core number is removed from the list. Used to keep track
    of cores when assigning them to threads.

    Attributes
    ----------
    core_list : list[int]
        Flat list of available CPUs.
    core_list_regions : list[list[list[int]]]
        List of available CPUs split into numa and if applicable, regions.

    Methods
    -------
    __getitem__:
        Return the desired CPU number and remove this from the availble CPUs lists.
    range:
        Loop over the CPU list and return a list of CPUs, and remove them from the available CPUs lists.
    alt_range:
        Loop over the CPU list and return a list of CPUs using "for each", and remove them from the available CPUs lists.
    first_available:
        Return the first CPU (number at index 0) and remove this from the available CPUs lists.
    """
    def __init__(self, core_list : list[int], core_list_regions : list[list[list[int]]]) -> None:
        self.core_list = core_list
        self.core_list_regions = core_list_regions
        pass


    def __getitem__(self, c : int) -> int:
        """ Return the desired core and remove this from the availble cores lists.

        Args:
            c (int): Core number.

        Raises:
            Exception: Available Core list is empty.
            Exception: Core number was not found.

        Returns:
            int: Core number.
        """
        if c in self.core_list:
            self.core_list.remove(c)
            for n in self.core_list_regions: # loop over numa
                if len(n) == 0:
                    raise Exception("no more free cores available!")
                if type(n[0]) is list: # there should never be a mix of ints and lists in the cpu list, so this is fine.
                    for h in n: # loop over region
                        if c in h: h.remove(c)
                else:
                    if c in n: n.remove(c)
            return c
        else:
            raise Exception(f"core {c} not found (has it already been allocated?)")


    def range(self, _min : int, _max : int, numa : int, region : int = None) -> list[int]:
        """ Loop over the Core list and return a list of cores, and remove them from the available cores lists.

        Args:
            _min (int): Min index
            _max (int): Max index
            numa (int): Numa to loop over
            region (int, optional): Region to loop over. Defaults to None.

        Returns:
            list[int]: List of selected cores.
        """
        if region is None:
            return [self[i] for i in list(self.core_list_regions[numa]) if (i >= _min) and (i < _max)]
        else:
            return [self[i] for i in list(self.core_list_regions[numa][region]) if (i >= _min) and (i < _max)]


    def alt_range(self, num : int, numa : int, region : int = None) -> list[int]:
        """ Loop over the core list and return a list of cores using "for each", and remove them from the available cores lists.

        Args:
            num (int): Number of cores to return
            numa (int): Numa to loop over
            region (int, optional): Region to loop over. Defaults to None.

        Returns:
            list[int]: List of selected cores.
        """
        if region is None:
            return [self[i] for i in list(self.core_list_regions[numa][:num])]
        else:
            return [self[i] for i in list(self.core_list_regions[numa][region][:num])]


    def first_available(self, numa : int, region : int = None) -> int:
        """ Return the first core (number at index 0) and remove this from the available cores lists.

        Args:
            numa (int): Numa to select from
            region (int, optional): Region to select from. Defaults to None.

        Returns:
            int: first available core
        """
        if region is not None:
            return self.core_list_regions[numa][region][0]
        else:
            return self.core_list_regions[numa][0]


    def num_regions(self, numa : int) -> type:
        """ Count the number of regions in for a given numa region.

        Args:
            numa (int): numa number.

        Returns:
            int: number of regions
        """
        if type(self.core_list_regions[numa][0]) == list:
            return len(self.core_list_regions[numa])
        else:
            return 1


def run_command(host : str, cmd : str) -> subprocess.CompletedProcess:
    """ Run bash command on a given host.

    Args:
        host (str): Host name.
        cmd (str): Command to run.

    Returns:
        subprocess.CompletedProcess: Output of command. 
    """
    return subprocess.run(['ssh', f'{os.environ["USER"]}@{host}', f'{cmd}'], capture_output = True)


def parse_output(output: subprocess.CompletedProcess, separator : str = None) -> list | dict:
    """ Get output from run_command and apply some simple formatting.

    Args:
        output (subprocess.CompletedProcess): Subprocess output.
        separator (str, optional): String separator to split key-value pairs. Defaults to None.

    Returns:
        list | dict: _description_
    """
    output_lines = str(output.stdout)[2:].split("\\n")

    if separator:
        parsed = {}
        for i in output_lines:
            info = i.split(separator)
            if len(info) > 1:
                parsed[info[0]] = info[1].replace("  ", "")

        return parsed
    else:
        return output_lines


def get_numa_info(host : str) -> tuple[dict, int]:
    """ Get CPU information needed to make the pinning file.

    Args:
        host (str): Server host name.

    Returns:
        tuple[dict, int]: Dictionary of values about cpu cores and cache size.
    """
    numa_dict = {}
    numa_nodes = None

    numactl_out = parse_output(run_command(host, "numactl -H"))
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


def core_list_to_str(cores : list[int]) -> str:
    """ Convert a list of cores to a string format for the json file.

    Args:
        cores (list[int]): List of cores.

    Returns:
        str: Core list string.
    """
    #! for now, just use join, but can try to condense it later on.
    return ",".join(str(c) for c in cores)


def assign_cores_tpproc(cores : CoreList, numa : int, n_cores : int) -> list[int]:
    """ Assign cores to the tp processors.

    Args:
        cores (CoreList): Available cores.
        numa (int): Numa region.
        n_cores (int): Number of cores to assign to the tpproc thread.

    Returns:
        list[int]: list of assigned cores.
    """
    if cores.num_regions(numa) == 1:
        assigned_cores = [cores[cores.first_available(numa)] for i in range(n_cores)]
    else:
        remaining = n_cores
        assigned_cores = []
        while remaining > 0:
            for i in range(cores.num_regions(numa)):
                if remaining == 0: break
                assigned_cores.append(cores[cores.first_available(numa, i)])
                remaining -= 1
    return assigned_cores



def assign_cores_default(cores : CoreList, numa : int, n_cores : int) -> list[int]:
    """ Assign cores to a thread, used for the rawproc and cleanup, consumer, periodic threads (ccp).

    Args:
        cores (CoreList): Available cores.
        numa (int): Numa region.
        n_cores (int): Number of cores to assign to the thread.

    Returns:
        list[int]: list of assigned cores.
    """
    if cores.num_regions(numa) == 1:
        cores = cores.alt_range(n_cores, numa)
    else:
        cores = cores.alt_range(n_cores//2, numa, 0) + cores.alt_range(n_cores//2, numa, 1)
    return cores


def assign_cores_recording(cores : CoreList, numa : int, n_cores : int) -> list[int]:
    """ Assign cores to the recording threads.

    Args:
        cores (CoreList): Available cores.
        numa (int): Numa region.
        n_cores (int): Number of cores to assign to the recording thread.

    Returns:
        list[int]: list of assigned cores.
    """
    if cores.num_regions(numa) == 1:
        assigned_cores = cores.range(cores.core_list_regions[numa][-1] - (n_cores - 1), cores.core_list_regions[numa][-1] + 1, numa)
    else:
        n_cores = n_cores // cores.num_regions(numa)
        remainder = n_cores % cores.num_regions(numa)
        assigned_cores = []
        for i in range(cores.num_regions(numa)):
            if i == (cores.num_regions(numa) - 1):
                n = n_cores + remainder
            else:
                n = n_cores
            assigned_cores.extend(cores.range(cores.core_list_regions[numa][i][-1] - (n - 1), cores.core_list_regions[numa][i][-1] + 1, numa, i))
    return assigned_cores


def fill_pinning(pinning : dict, cores : CoreList, n_cores : dict[int]) -> dict:
    """ Assign the cores to the CPU pinning based on the rules for each specific thread type.

    Args:
        pinning (dict): Pinning dictionary.
        cores (CoreList): List of cores to use when assigning.
        n_cores (dict[int]): number of cores to add for each thread type.

    Raises:
        Exception: Thread type not known (so it is unknown how the cores should be assigned to this thread).

    Returns:
        dict: Filled pinning dictionary.
    """
    for apps in pinning["daq_application"]:
        if not apps[-2:].isalpha():
            numa = int(apps[-1])
        else:
            numa = int(apps[-2])

        ccp_cores = None
        rawproc_cores = None
        for t in pinning["daq_application"][apps]["threads"]:
            if ("tpproc" in t) or ("tpset" in t):
                pinning["daq_application"][apps]["threads"][t] = core_list_to_str(assign_cores_tpproc(cores, numa, n_cores["tpproc"]))
            elif "rte-worker" in t:
                pinning["daq_application"][apps]["threads"][t] = str(cores[int(t.split("-")[-1])]) # rte worker lcores are assigned in the OKS configuration, so these are already pre-defined.
            elif ("rawproc" in t) or ("postproc" in t):
                if rawproc_cores is None:
                    rawproc_cores = core_list_to_str(assign_cores_default(cores, numa, n_cores["rawproc"]))
                pinning["daq_application"][apps]["threads"][t] = rawproc_cores
            elif ("cleanup" in t) or ("consumer" in t) or ("periodic" in t):
                if ccp_cores is None:
                    ccp_cores = core_list_to_str(assign_cores_default(cores, numa, n_cores["ccp"]))
                pinning["daq_application"][apps]["threads"][t] = ccp_cores
            elif "recording" in t:
                pinning["daq_application"][apps]["threads"][t] = core_list_to_str(assign_cores_recording(cores, numa, n_cores["recording"]))
            else:
                raise Exception(f"do not know how to assign cores to thread {t}")

        pinning["daq_application"][apps]["parent"] = ",".join([ccp_cores, rawproc_cores])
    return pinning


def main(args = argparse.Namespace):
    numa_dict = get_numa_info(args.readout_server)[0]

    #* this is just to emulate the numactl output for np0x machines for testing purposes
    if args.fake is True:
        if args.readout_server == "np04-srv-031":
            fake_cpu_pinning = {
                "0" : list(range(0, 32)) + list(range(64,96)),
                "1" : list(range(32, 64)) + list(range(96, 128))
            }
        elif args.readout_server == "np02-srv-003":
            fake_cpu_pinning = {
                "0" : list(range(0, 112, 2)),
                "1" : [i + 1 for i in range(0, 112, 2)]
            }
        else:
            raise Exception(f"Cannot generate fake CPU info for {args.readout_server}")

    if args.fake is True:
        for k in numa_dict:
            numa_dict[k]["cpus"] = fake_cpu_pinning[k]

    cores_all = []
    for i in numa_dict.values():
       cores_all += i["cpus"]

    n_cores_total = len(cores_all)

    # define the cpu regions i.e. if the cpu has hypercores assigned to the different numas
    # e.g. 0,32, 63,95, these will be defined as two distinct regions
    for numa in numa_dict:
        cores = numa_dict[numa]["cpus"]
        min_stride = min([cores[i] - cores[i-1] for i in range(1, len(numa_dict[numa]["cpus"]))])
        region_boundaries = []
        for i in range(1, len(numa_dict[numa]["cpus"])):
            if (cores[i] - cores[i-1]) > min_stride:
                region_boundaries.append(i)
        if len(region_boundaries) > 1:
            raise Exception("more than two regions has not been supported yet.")
        elif len(region_boundaries) == 0:
            print("only one region was found")
            n_regions = 1
            numa_dict[numa]["regions"] = cores
        else:
            n_regions = 2
            numa_dict[numa]["regions"] = [cores[:region_boundaries[0]], cores[region_boundaries[0]:]]

    cores_remaining = list(cores_all)
    remaining_regions = [list(v["regions"]) for v in numa_dict.values()]

    # how many cores should be assigned to a single thread (sharing rules are omitted here). Taken from np04-srv-031 pinning
    max_cores = {k : getattr(args, k) for k in max_cores_default}

    #! this should be read from the oks config
    pinning = {"daq_application" : {}}
    app_names = []
    with open(args.template, "r") as f:
        template = json.load(f)

    for k, v in template["daq_application"].items():
        app_names.append(k)
        pinning["daq_application"][k] = {}

        if "parent" in v:
            pinning["daq_application"][k]["parent"] = None
        if "threads" in v:
            pinning["daq_application"][k]["threads"] = {}
            for t in v["threads"]:
                pinning["daq_application"][k]["threads"][t] = None


    # remove first thread and hypercore on each numa node
    if n_regions == 1:
        cores_remaining.remove(numa_dict["0"]["regions"][0])
        cores_remaining.remove(numa_dict["1"]["regions"][0])
        remaining_regions[0].pop(0)
        remaining_regions[1].pop(0)

    else: # must have hypercores
        cores_remaining.remove(numa_dict["0"]["regions"][0][0])
        cores_remaining.remove(numa_dict["0"]["regions"][1][0])
        cores_remaining.remove(numa_dict["1"]["regions"][0][0])
        cores_remaining.remove(numa_dict["1"]["regions"][1][0])

        remaining_regions[0][0].pop(0)
        remaining_regions[0][1].pop(0)
        remaining_regions[1][0].pop(0)
        remaining_regions[1][1].pop(0)

    # make the pinning configuration for running with the DAQ
    cores = CoreList(copy.deepcopy(cores_remaining), copy.deepcopy(remaining_regions))

    fill_pinning(pinning, cores, max_cores) # create pinning for running

    # print created pinning and remaning cores that were not assigned (excluding the first core in each region.)
    print(pinning)
    print("remaining cores:")
    print(cores.core_list_regions)

    cores = CoreList(copy.deepcopy(cores_remaining), copy.deepcopy(remaining_regions))
    pinning_pre_conf = copy.deepcopy(pinning)

    app_names = list(pinning["daq_application"].keys())

    for k in pinning_pre_conf["daq_application"]:
        numa = int(k.split("eth")[-1][0])
        pinning_pre_conf["daq_application"][k]["parent"] = core_list_to_str([j for numa in cores.core_list_regions[numa] for j in numa])

    # write to a json file
    for p, n in zip([pinning, pinning_pre_conf],["cpupin-all-running.json", "cpupin-all.json"]):
        with open(n, "w") as f:
            json.dump(p, f, indent = 4)

        print(f"pinning has been written to {n}")

    return

if __name__ == "__main__":
    max_cores_default = {
        "rte" : 1,
        "tpproc" : 2,
        "rawproc" : 16,
        "ccp" : 6,
        "recording" : 6
    }

    parser = argparse.ArgumentParser("Generate a pinning file for a readout machine.")
    parser.add_argument("-t", "--template", type = str, help = "pinning file template. must be a json file.", required = True)
    parser.add_argument("-r", "--readout_server", type = str, default = gethostname(), help = "hostname for the machine, if not provided the current machine hostname is used.")
    parser.add_argument("-f", "--fake", action="store_true", help = "fake the numactl output for the specified readout machine.")

    for k, v in max_cores_default.items():
        if k == "ccp":
            name = "consumer, cleanup or periodic"
        else:
            name = k
        parser.add_argument(f"--{k}", dest = k, type = int, default = v, help = f"number of cores to assign to a {name} thread. Set to {max_cores_default[k]} by default.")

    args = parser.parse_args()

    print(args)
    main(args)