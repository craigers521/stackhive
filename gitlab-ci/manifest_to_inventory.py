#!/usr/bin/env python3
"""Convert a StackHive deployment manifest to a static Ansible inventory.

Usage:
    python3 manifest_to_inventory.py <manifest.yml> <output-inventory.yml> [--live]

The manifest (committed by the dashboard on deployment creation) names the
target devices and profile; the generated inventory reproduces the group_vars
(group = profile) + netconf host vars the role expects.
"""
import argparse
import sys

import yaml


def build_inventory(manifest, live):
    profile = manifest["profile"]
    hosts = {}
    for device in manifest.get("devices", []):
        hostname = device["hostname"]
        vars_ = {
            "stackhive_profile": profile,
            "ansible_host": device.get("mgmt_ip") or hostname,
        }
        if live:
            vars_.update(
                {
                    "ansible_connection": "netconf",
                    "ansible_network_os": "cisco.ios",
                    "ansible_netconf_port": 12300,
                    "ansible_netconf_wait_for_ready": True,
                    "ansible_netconf_host": device.get("mgmt_ip") or hostname,
                }
            )
        else:
            vars_["ansible_connection"] = "local"
        hosts[hostname] = vars_
    # iosxe_profile_templates_dir defaults to <playbook_dir>/../templates which
    # resolves to the repo-root templates/<profile> directory in CI.
    return {
        "all": {
            "children": {
                profile: {
                    "hosts": hosts
                }
            }
        }
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("output")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    with open(args.manifest) as fh:
        manifest = yaml.safe_load(fh)
    inventory = build_inventory(manifest, args.live)
    with open(args.output, "w") as fh:
        yaml.safe_dump(inventory, fh, default_flow_style=False)
    print(f"inventory written: {args.output} ({len(manifest.get('devices', []))} hosts)")


if __name__ == "__main__":
    main()
