
import os
import yaml
import fcntl

from .utils import fread

from runner_service import configuration

import logging
logger = logging.getLogger(__name__)


# Ny default None is represented in YAML as a 'null' string, so we override
# the representer for NONE type vars with a '' to make the config file more
# human readable
def represent_null(self, _):
    return self.represent_scalar('tag:yaml.org,2002:null', '')


# Add our representer
yaml.add_representer(type(None), represent_null)


class InventoryGroupExists(Exception):
    pass


class InventoryGroupMissing(Exception):
    pass


class InventoryHostMissing(Exception):
    pass


class InventoryGroupEmpty(Exception):
    pass


class InventoryWriteError(Exception):
    pass


def no_group(func):
    def func_wrapper(*args):
        obj, group = args
        if group in obj.groups:
            logger.debug("Group add request for '{}' failed - it already "
                         "exists".format(group))
            if obj.exclusive_lock:
                obj.unlock()
            raise InventoryGroupExists("Group {} already exists".format(group))
        else:
            return func(*args)
    return func_wrapper


def group_exists(func):
    def func_wrapper(*args):
        obj, group, *rest = args
        if group not in obj.groups:
            logger.debug("Group request for '{}' failed - it's not in "
                         "the inventory".format(group))
            if obj.exclusive_lock:
                obj.unlock()
            raise InventoryGroupMissing("{} not found in the Inventory".format(group))
        else:
            return func(*args)
    return func_wrapper


class AnsibleInventory(object):
    inventory_seed = {"all": {"children": None}}

    def __init__(self, inventory_file=None, excl=False):

        if not inventory_file:
            self.filename = os.path.join(configuration.settings.playbooks_root_dir,
                                         "inventory",
                                         "hosts")
        else:
            self.filename = os.path.expanduser(inventory_file)

        self.inventory = None
        self.exclusive_lock = excl
        self.fd = None
        self.load()

    def load(self):

        if not os.path.exists(self.filename):
            try:
                with open(self.filename, 'w') as inv:
                    inv.write(yaml.dump(AnsibleInventory.inventory_seed,
                                        default_flow_style=False))
            except IOError:
                raise InventoryWriteError("Unable to create the seed inventory"
                                          " file at {}".format(self.filename))

        if self.exclusive_lock:

            try:
                self.fd = open(self.filename, 'r+')
                self.lock()
            except (BlockingIOError, OSError):
                # Can't obtain an exclusive_lock
                self.fd.close()
                return
            else:
                raw = self.fd.read().strip()
        else:
            raw = fread(self.filename)

        if not raw:
            self.inventory = None
        else:
            # TODO what happens with invalid yaml?
            self.inventory = yaml.safe_load(raw)

    def _dump(self):
        return yaml.dump(self.inventory, default_flow_style=False)

    def save(self):
        self.fd.seek(0)
        self.fd.write(self._dump())
        self.fd.truncate()
        self.unlock()

    def lock(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(self):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        self.fd.close()

    def __str__(self):
        return self._dump()

    @property
    def loaded(self):
        return self.inventory is not None

    @property
    def hosts(self):
        _host_list = list()
        for group_name in self.groups:
            try:
                _host_list.extend(list(self.inventory['all']['children'][group_name]['hosts'].keys()))
            except (AttributeError, TypeError):
                # group is empty
                pass
        return _host_list

    @property
    def groups(self):
        try:
            return list(self.inventory['all']['children'].keys())
        except (AttributeError, TypeError):
            return []

    @no_group
    def group_add(self, group):
        node = self.inventory['all']['children']
        if not isinstance(node, dict):
            self.inventory['all']['children'] = dict()
        self.inventory['all']['children'][group] = {"hosts": None}
        logger.info("Group '{}' added to the inventory".format(group))
        self.save()

    @group_exists
    def group_remove(self, group):
        del self.inventory['all']['children'][group]
        logger.info("Group '{}' removed from the inventory".format(group))
        if not self.inventory['all']['children']:
            self.inventory['all']['children'] = None
        self.save()

    @group_exists
    def group_show(self, group):
        if isinstance(self.inventory['all']['children'][group], dict):
            if isinstance(self.inventory['all']['children'][group]['hosts'], dict):
                return list(self.inventory['all']['children'][group]['hosts'].keys())
            else:
                return []
        else:
            return []

    @group_exists
    def host_add(self, group, host):
        node = self.inventory['all']['children'][group]['hosts']
        if not isinstance(node, dict):
            self.inventory['all']['children'][group]['hosts'] = {}
        self.inventory['all']['children'][group]['hosts'][host] = None
        logger.info("Host '{}' added to the inventory group "
                    "'{}'".format(host, group))
        self.save()

    @group_exists
    def host_remove(self, group, host):
        node = self.inventory['all']['children'][group]['hosts']
        if isinstance(node, dict):
            if host not in self.inventory['all']['children'][group]['hosts']:
                raise InventoryHostMissing("Host {} not in {}".format(host,
                                                                      group))
            else:
                del self.inventory['all']['children'][group]['hosts'][host]
                logger.info("Host ''{}' removed from inventory group "
                            "'{}'".format(host, group))
                if not self.inventory['all']['children'][group]['hosts']:
                    self.inventory['all']['children'][group]['hosts'] = None
                self.save()
        else:
            logger.debug("Host removal attempted against the empty "
                         "group '{}'".format(group))
            raise InventoryGroupEmpty("Group is empty")

    def host_show(self, host):
        host_groups = list()
        for group in self.groups:
            if host in self.group_show(group):
                host_groups.append(group)

        return host_groups
