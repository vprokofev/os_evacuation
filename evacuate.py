#!/usr/bin/env python3

import sys
import logging
import argparse
import openstack
from time import sleep
from novaclient import client
from keystoneauth1 import session

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--parallel", metavar="N",
                        help="run N parallel migrations",
                        default=5)
    parser.add_argument("-t", "--target", metavar="HOST",
                        help="migrate all servers to this host")
    parser.add_argument("-q", "--query", metavar="HOST",
                        help="qeuery target host for each instance")
    parser.add_argument("--timeout", metavar="N", type=int,
                        help="how many seconds to wait for instance migration",
                        default=300)
    parser.add_argument("-d", "--debug", action="store_true",
                        help="enable debug logging")
    parser.add_argument("host", metavar="HOST",
                        help="host to evacuate")
    args = parser.parse_args()
    return args


def create_logger():
    level=logging.INFO
    if args.debug:
        level=logging.DEBUG
    logger = logging.getLogger(__name__)
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(u"[%(asctime)s] - %(filename)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def create_nova():
    conn = openstack.connect()
    auth = conn.config._auth
    sess = session.Session(auth=auth)
    nova = client.Client(2, session=sess)
    return nova


def migrate(instance, target=None):
    if instance.status in ("ACTIVE", "PAUSED"):
        migrate_live(instance, target)
    elif instance.status == "SHUTOFF":
        # older nova does not support specifying host for cold migrations
        # but we still pass it here for future implementation
        migrate_cold(instance, target)
    elif instance.status == "SUSPENDED":
        migrate_suspended(instance, target)
    else:
        log.warning(f"instance {instance.name} state {instance.status} is not supported, skipping")


def migrate_cold(instance, target=None):
    instance.migrate()
    sleep(5)
    i=5
    instance.get()
    while instance.status == "SHUTOFF":
        if i>args.timeout:
            log.error(f"Something went wrong: instance {instance.name} still migrating but we're over timeout. Aborting.")
            sys.exit(1)
        sleep(2)
        i+=2
        instance.get()
    if instance.status == "VERIFY_RESIZE":
        instance.confirm_resize()
    while instance.status == "VERIFY_RESIZE":
        if i>args.timeout:
            log.error(f"Something went wrong: instance {instance.name} did not confirm migartion. Aborting.")
            sys.exit(1)
        sleep(2)
        i+=2
        instance.get()
    if getattr(instance, "OS-EXT-SRV-ATTR:hypervisor_hostname") == args.host:
        log.error(f"Something went wrong: instance {instance.name} did not migrate from {args.host}. Aborting.")
        sys.exit(1)
    log.info(f"instance {instance.name} migrated to {getattr(instance, 'OS-EXT-SRV-ATTR:hypervisor_hostname')}, state {instance.status}")


def migrate_suspended(instance, target=None):
    log.info(f"instance {instance.name} is in {instance.status} state, resuming")
    instance.resume()
    sleep(5)
    i=5
    instance.get()
    while instance.status == "SUSPENDED":
        if i>args.timeout:
            log.error(f"Something went wrong: instance {instance.name} did not resume in time. Aborting.")
            sys.exit(1)
        sleep(2)
        i+=2
        instance.get()
    if instance.status == "ACTIVE":
        migrate_live(instance, target)
    else:
        log.error(f"Something went wrong: instance {instance.name} is in {instance.status} state while it should be ACTIVE. Aborting.")
        sys.exit(1)
    instance.suspend()
    sleep(5)
    i=5
    instance.get()
    while instance.status != "SUSPENDED":
        if i>args.timeout:
            log.error(f"Something went wrong: instance {instance.name} is in {instance.status} state while it should be SUSPENDED. Aborting.")
            sys.exit(1)
        sleep(2)
        i+=2
        instance.get()
    log.info(f"{instance.name} is {instance.status}")


def migrate_live(instance, target=None):
    instance.live_migrate(host=target)
    sleep(5)
    i=5
    instance.get()
    while instance.status == "MIGRATING":
        if i>args.timeout:
            log.error(f"Something went wrong: instance {instance.name} still migrating but we're over timeout. Aborting.")
            sys.exit(1)
        sleep(2)
        i+=2
        instance.get()
    if getattr(instance, "OS-EXT-SRV-ATTR:hypervisor_hostname") == args.host:
        log.error(f"Something went wrong: instance {instance.name} did not migrate from {args.host}. Aborting.")
        sys.exit(1)
    log.info(f"instance {instance.name} migrated to {getattr(instance, 'OS-EXT-SRV-ATTR:hypervisor_hostname')}, state {instance.status}")


def main(args):
    nova = create_nova()
    instance_list = nova.servers.list(search_opts={"host": args.host, "all_tenants": True})
    if len(instance_list) == 0:
        log.info(f"Host {args.host} has {len(instance_list)} instances.")
        sys.exit(0)
    log.info(f"Host {args.host} has {len(instance_list)} instances. Migrating...")
    for instance in instance_list:
        log.debug(f"{instance.name}: {instance.status}")
    for instance in instance_list:
        migrate(instance, args.target)


if __name__ == "__main__":
    args = parse_args()
    log = create_logger()
    main(args)
