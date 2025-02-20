import json

import conffwk
import confmodel

from rich import print

oksfile = "/nfs/home/sbhuller/NFD_DEV_250130_A9/work/ehn1-daqconfigs/sessions/np02-session.data.xml"

pinning_template = "/nfs/home/sbhuller/NFD_DEV_250130_A9/pin_test/cpupin-all-template.json"

with open(pinning_template, "r") as f:
    pinning_template = json.load(f)


db = conffwk.Configuration("oksconflibs:" + oksfile)

readout = db.get_dals(class_name = "ReadoutApplication")

names = [r.id for r in readout]

for k, v in pinning_template["daq_application"].items():
    app_name = k.split("--name ")[-1]

    for ru in readout:
        if app_name != ru.id:
            continue
        else:
            resources = ru.get("ProcessingResource")
            for r in resources:
                if ("lcore" in r.id):
                    # lcores = r.cpu_cores #* not needed yet, pinning template contains lcore names
                    numa = r.numa_id
                    break
        print(numa)
