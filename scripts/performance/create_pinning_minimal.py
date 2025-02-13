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

import utils
import llc_domain_parser

from dataclasses import dataclass

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


def get_numa_info(host : str) -> tuple[dict, int]:
    """ Get CPU information needed to make the pinning file.

    Args:
        host (str): Server host name.

    Returns:
        tuple[dict, int]: Dictionary of values about cpu cores and cache size.
    """
    numa_dict = {}
    numa_nodes = None

    numactl_out = utils.parse_output(utils.run_command(host, "numactl -H"))
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


def load_template(template_file : str) -> dict:
    #! this should be read from the oks config
    pinning = {"daq_application" : {}}
    with open(template_file, "r") as f:
        template = json.load(f)

    for k, v in template["daq_application"].items():
        pinning["daq_application"][k] = {}

        if "parent" in v:
            pinning["daq_application"][k]["parent"] = None
        if "threads" in v:
            pinning["daq_application"][k]["threads"] = {}
            for t in v["threads"]:
                pinning["daq_application"][k]["threads"][t] = None
    return pinning


def cpu_pin_numa_only(args : argparse.Namespace):
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

    pinning = load_template(args.template)

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


    for k in pinning_pre_conf["daq_application"]:
        numa = int(k.split("eth")[-1][0])
        pinning_pre_conf["daq_application"][k]["parent"] = core_list_to_str([j for numa in cores.core_list_regions[numa] for j in numa])

    # write to a json file
    for p, n in zip([pinning, pinning_pre_conf],["cpupin-all-running.json", "cpupin-all.json"]):
        with open(n, "w") as f:
            json.dump(p, f, indent = 4)

        print(f"pinning has been written to {n}")

    return


class ElementList:
    def __init__(self, elements : list, domain_map):
        self.elements = elements
        self.map = domain_map
        return


    def __getitem__(self, i : int):
        e = self.elements[i]
        self.elements.remove(e)
        if self.map: self.map.remove(e)
        return e


    def get_id(self, i : int):
        for e in self.elements:
            if e.id == i:
                self.elements.remove(e)
                self.map.remove(e)
                return e
        raise Exception(f"Element with id {i} was not found!")

    @property
    def first(self):
        return self.__getitem__(0)


    def __len__(self):
        return len(self.elements)


@dataclass
class Element:
    id : int
    children : list[int] # only keep the ID not the object itself
    parent : "Element"
    type : str = None

    def __repr__(self):
        return f"{self.type}(id : {self.id}, children : {len(self.children) if self.children else None}, parent : {self.parent})"


    def get_type(self, type : str):
        cores = []
        if self.children:
            for c in self.children:
                if c.type == type:
                    cores.append(c)
                else:
                    cores.extend(c.get_type(type))
        return cores


def assign_element_type(e : Element):
    # code asssumes all children are the same type (which should be true)
    if not e.parent:
        e.type = "Socket" # we are at the highest level
    elif not e.children:
        e.type == "PU" # we are at the lowest level
    elif e.children[0].type == "PU":
        e.type = "Core"
    elif e.children[0].type == "Core":
        e.type = "Cache"
    elif e.children[0].type == "Cache":
        e.type = "NUMA"
    elif e.children[0].type == None:
        pass
    else:
        raise Exception(f"do not know how to interpret Element with type: {e.type}")
    return


def nest_loop(container, parent : Element = None, element_list : list = []):
    if type(container) == dict:
        for item in container.items():
            if hasattr(item[1], "__iter__"):
                e = Element(item[0], [], parent)
                if parent: parent.children.append(e)
                element_list.append(e)
                nest_loop(item[1], e, element_list)
                assign_element_type(e)

                #* loop through all items, and return list of elements who are children of this item
                #* assign the parent to each child
                #* add elements to a flat list

    else: # assume list-like
        for item in container:
            if hasattr(item, "__iter__"):
                e = Element(None, [], parent)
                if parent: parent.children.append(e)
                element_list.append(e)
                nest_loop(item)
                assign_element_type(e)
            else:
                e = Element(item, None, parent, "PU") # this is the deepest part of the map
                parent.children.append(e) # add child to parent
                element_list.append(e) # add element to flat list
                assign_element_type(e)
    return


class CoreMap:
    def __init__(self, domain_map : dict):
        self.elements = []
        nest_loop(domain_map, element_list = self.elements)

        unique_types = []
        for e in self.elements:
            if e.type not in unique_types:
                unique_types.append(e.type)
        for t in unique_types:
            self.__make_func__(t)

        self.__offset_core_id__()

        return


    def __make_func__(self, type : str) -> callable:
        def func(self) -> ElementList:
            return ElementList([i for i in self.elements if i.type == type], self)
        setattr(CoreMap, type.lower(), property(func))


    def __offset_core_id__(self):
        offset = len(self.core) // len(self.socket)
        for c in self.core.elements:
            c.id = c.id + c.parent.parent.parent.id * offset
        return


    def remove(self, e : Element, remove_from_parent : bool = True):
        #* remove any reference to another element: find its parent, and remove self from children
        #* remove any reference to another element: find its children, and remove self from parent
        #* remove self from elements

        if e.children:
            for c in e.children:
                self.remove(c, False)

        self.elements.remove(e)
        if remove_from_parent: e.parent.children.remove(e)
        return


def assign_cores_map(core_map : CoreMap, numa_region : Element, max_cores : int):
    pus = []
    while len(pus) < max_cores:
        tpproc_core = ElementList(numa_region.get_type("Core"), core_map).first
        pus.extend([c.id for c in tpproc_core.children])
    return pus

def fill_pinning_map(pinning : dict, max_cores : dict, core_map : CoreMap) -> dict:

    # First exclude the first core (first two processing units) in each numa region
    for n in core_map.numa.elements:
        core_map.core.get_id(min([c.id for c in n.get_type("Core")]))

    for app in pinning["daq_application"]:
        if not app[-2:].isalpha():
            numa = int(app[-1])
        else:
            numa = int(app[-2])

        for numa_region in core_map.numa.elements: # get the nume region, but do not remove it from the map yet
            if numa_region.id == numa: break

        rawprocs = None
        ccps = None
        for t in pinning["daq_application"][app]["threads"]:
            # print(t)
            # print(numa_region.get_type("PU"))
            if "rte-worker" in t:
                #! probably add some checks here: makre sure lcores are from the numa region, keep track of the cache id for each lcore
                pu = int(t.split("-")[-1])
                pinning["daq_application"][app]["threads"][t] = str(pu)
                core_map.pu.get_id(pu)

            elif "tpproc" in t:
                tpprocs = assign_cores_map(core_map, numa_region, max_cores["tpproc"])
                pinning["daq_application"][app]["threads"][t] = core_list_to_str(tpprocs)

            elif "rawproc" in t:
                if rawprocs is None:
                    rawprocs = assign_cores_map(core_map, numa_region, max_cores["rawproc"])
                pinning["daq_application"][app]["threads"][t] = core_list_to_str(rawprocs)

            elif ("cleanup" in t) or ("consumer" in t) or ("periodic" in t):
                if ccps is None:
                    ccps = assign_cores_map(core_map, numa_region, max_cores["ccp"])                
                pinning["daq_application"][app]["threads"][t] = core_list_to_str(ccps)

            elif "recording" in t:
                recording = assign_cores_map(core_map, numa_region, max_cores["recording"])
                pinning["daq_application"][app]["threads"][t] = core_list_to_str(recording)

            else:
                raise Exception(f"do not know how to assign cores to thread {t}")
        
        pinning["daq_application"][app]["parent"] = core_list_to_str(rawprocs + ccps)

    print(pinning)

    return pinning


def main(args = argparse.Namespace):
    cm = CoreMap(llc_domain_parser.create_llc_domain_map(args.readout_server))

    pus_numa = [[p.id for p in n.get_type("PU")] for n in cm.numa.elements]

    # how many cores should be assigned to a single thread (sharing rules are omitted here). Taken from np04-srv-031 pinning
    max_cores = {k : getattr(args, k) for k in max_cores_default}

    pinning = load_template(args.template)
    pinning = fill_pinning_map(pinning, max_cores, cm)

    pinning_conf = copy.deepcopy(pinning)
    for app in pinning_conf["daq_application"]:
        if not app[-2:].isalpha():
            numa = int(app[-1])
        else:
            numa = int(app[-2])
        pinning_conf["daq_application"][app]["parent"] = core_list_to_str(pus_numa[numa])

    print(pinning_conf) 

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