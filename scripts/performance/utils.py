"""
Created on: 12/02/2025 15:07

Author: Shyam Bhuller

Description: Utility functions.
"""

import os
import subprocess

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
