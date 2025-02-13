#!/usr/bin/env python
"""
Created on: 05/12/2024 12:17

Author: Shyam Bhuller

Description: Create a cpu pinning file for a readout server.

#! Pinning file needs to figure out the thread names somehow...
#! rte-worker threads are predefined in the OKS configuration, must read them in.

#! quick way is to pass the script a template pinning file with thread names (and rte-worker-threads), script then assigns the core numbers appropriately
#! correct way is to read in OKS file, somehow infer names from the configuration (unclear how) and create json file.

#! rte-worker and raw processors are linked in some way (not exposed in the configurtion). This is needed to ensure rtes and raw procs are in the same l3 domain.

#! tpproc, parent and ccp should be in l3 domain other than rawprocs, rtes and recording

"""
import argparse
import copy
import json

import utils
import llc_domain_parser

from dataclasses import dataclass

from socket import gethostname

from rich import print


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


class CoreMap:
    def __init__(self, domain_map : dict):
        self.elements = []
        CoreMap.ParseMap(domain_map, element_list = self.elements)

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


    @staticmethod
    def ParseMap(container, parent : Element = None, element_list : list = []):
        if type(container) == dict:
            for item in container.items():
                if hasattr(item[1], "__iter__"):
                    e = Element(item[0], [], parent)
                    if parent: parent.children.append(e)
                    element_list.append(e)
                    CoreMap.ParseMap(item[1], e, element_list)
                    CoreMap.AssignElementType(e)

                    #* loop through all items, and return list of elements who are children of this item
                    #* assign the parent to each child
                    #* add elements to a flat list

        else: # assume list-like
            for item in container:
                if hasattr(item, "__iter__"):
                    e = Element(None, [], parent)
                    if parent: parent.children.append(e)
                    element_list.append(e)
                    CoreMap.ParseMap(item)
                    CoreMap.AssignElementType(e)
                else:
                    e = Element(item, None, parent, "PU") # this is the deepest part of the map
                    parent.children.append(e) # add child to parent
                    element_list.append(e) # add element to flat list
                    CoreMap.AssignElementType(e)
        return

    @staticmethod
    def AssignElementType(e : Element):
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


def core_list_to_str(cores : list[int]) -> str:
    """ Convert a list of cores to a string format for the json file.

    Args:
        cores (list[int]): List of cores.

    Returns:
        str: Core list string.
    """
    #! for now, just use join, but can try to condense it later on.
    return ",".join(str(c) for c in cores)


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



def assign_cores_map(core_map : CoreMap, numa_region : Element, max_cores : int) -> list[int]:
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