# Copyright (c) 2018 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import pbr
import uuid

from neutron.objects import network
from neutron.objects import ports
from neutron.objects import trunk
from neutron.plugins.ml2 import driver_context
from neutron_lib.api.definitions import portbindings
from neutron_lib.api.definitions import provider_net
from oslo_config import cfg
from oslotest import base
from tooz import coordination
from unittest import mock

from networking_ansible import constants as c
from networking_ansible import config
from networking_ansible.ml2 import mech_driver
from network_runner.types import validators

QUOTA_REGISTRIES = (
    "neutron.quota.resource_registry.unregister_all_resources",
    "neutron.quota.resource_registry.register_resource_by_name",
)

COORDINATION = 'networking_ansible.ml2.mech_driver.coordination'


def patch_neutron_quotas():
    """Patch neutron quotas.

    This is to avoid "No resource found" messages printed to stderr from
    quotas Neutron code.
    """
    for func in QUOTA_REGISTRIES:
        mock.patch(func).start()


class MockConfig(object):
    def __init__(self, host=None, mac=None):
        self.inventory = {host: {'mac': mac}} if host and mac else {}
        self.mac_map = {}
        self.port_mappings = {}

    def add_extra_params(self):
        for i in self.inventory:
            self.inventory[i]['stp_edge'] = True

    def add_custom_params(self):
        for i in self.inventory:
            self.inventory[i]['cp_custom'] = 'param'


class BaseTestCase(base.BaseTestCase):
    test_config_files = []
    parse_config = True

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        super(BaseTestCase, self).setUp()
        if self.parse_config:
            self.setup_config()

        self.ansconfig = config
        self.testhost = 'testhost'
        # using lowercase to ensure case sensitivity is handled correctly
        # the code applys upper() to everything
        self.testmac = '01:23:45:67:89:ab'
        self.testphysnet = 'physnet'

        self.m_config = MockConfig(self.testhost, self.testmac)

    def setup_config(self):
        """Create the default configurations."""
        version_info = pbr.version.VersionInfo('networking_ansible')
        config_files = []
        for conf in self.test_config_files:
            config_files += ['--config-file', conf]
        cfg.CONF(args=config_files,
                 project='networking_ansible',
                 version='%%(prog)s%s' % version_info.release_string())


class NetworkingAnsibleTestCase(BaseTestCase):
    def setUp(self):
        patch_neutron_quotas()
        super(NetworkingAnsibleTestCase, self).setUp()
        config_module = 'networking_ansible.ml2.mech_driver.config.Config'
        with mock.patch.object(validators.ChoiceValidator, '__call__',
                               return_value=None):
            with mock.patch(config_module) as m_cfg:
                m_cfg.return_value = self.m_config
                self.mech = mech_driver.AnsibleMechanismDriver()
                with mock.patch(COORDINATION) as m_coord:
                    m_coord.get_coordinator = \
                        lambda *args: mock.create_autospec(
                            coordination.CoordinationDriver
                        ).return_value
                    self.mech.initialize()

        self.testsegid = 37
        self.testsegid2 = 73
        self.testport = 'switchportid'
        self.testid = uuid.uuid4()
        self.testid2 = uuid.uuid4()
        self.test_hostid = 'testhostid'
        self.test_pci_addr = '37:0b'
        self.test_pci_addr2 = '37:03'

        # Define mocked network context
        self.mock_net_context = mock.create_autospec(
            driver_context.NetworkContext).return_value
        self.mock_net_context.current = {
            'id': self.testsegid,
            'binding:host_id': 'fake-host-id',
            provider_net.NETWORK_TYPE: 'vlan',
            provider_net.SEGMENTATION_ID: self.testsegid,
            provider_net.PHYSICAL_NETWORK: self.testphysnet,
        }
        self.mock_net_context.dict = {
        }
        self.mock_net_context._plugin_context = 'foo'

        # mocked network orm object
        self.mock_net = mock.create_autospec(
            network.Network).return_value
        self.mock_net.dict = {
            provider_net.SEGMENTATION_ID: self.testsegid
        }
        self.mock_net.get = mock.Mock(
            side_effect=lambda x, y:
            self.mock_net.dict[x] if x in self.mock_net.dict else y)
        self.mock_netseg = mock.Mock(spec=network.NetworkSegment)
        self.mock_netseg.segmentation_id = self.testsegid
        self.mock_netseg.physical_network = self.testphysnet
        self.mock_netseg.network_type = 'vlan'
        self.mock_net.segments = [self.mock_netseg]

        # alternative segment
        self.mock_netseg2 = mock.Mock(spec=network.NetworkSegment)
        self.mock_netseg2.segmentation_id = self.testsegid2
        self.mock_netseg2.physical_network = self.testphysnet
        self.mock_netseg2.network_type = 'vlan'

        # non-physnet segment
        self.mock_netseg3 = mock.Mock(spec=network.NetworkSegment)
        self.mock_netseg3.segmentation_id = self.testsegid2
        self.mock_netseg3.physical_network = 'virtual'
        self.mock_netseg3.network_type = 'vxlan'

        # Binding profile dicts with
        # Local Link Information dicts
        self.profile_lli_no_mac = {
            'local_link_information': [{
                'switch_info': self.testhost,
                'port_id': self.testport,
            }]
        }
        self.profile_lli_no_info = {
            'local_link_information': [{
                'switch_id': self.testmac,
                'port_id': self.testport,
            }]
        }
        # SRIOV profile
        self.profile_pci_slot = {
            'pci_slot': '0000:{}'.format(self.test_pci_addr)
        }
        self.profile_pci_slot2 = {
            'pci_slot': '0000:{}'.format(self.test_pci_addr2)
        }

        # Mocked trunk port and subport objects
        self.mock_trunk = mock.Mock(spec=trunk.Trunk)
        self.mock_trunk.network_id = uuid.uuid4()
        self.mock_subport_1 = mock.Mock(spec=trunk.SubPort)
        self.mock_subport_1.segmentation_id = self.testsegid2
        self.mock_trunk.sub_ports = [self.mock_subport_1]

        # Mocked port objects
        self.mock_port_bm = mock.create_autospec(
            ports.Port).return_value
        self.mock_port_bm.network_id = self.testid
        self.mock_port_bm.dict = {
            'id': self.testid,
            'network_id': uuid.uuid4(),
            'mac_address': self.testmac,
            c.DEVICE_OWNER: c.BAREMETAL_NONE,
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_OTHER,
            portbindings.VNIC_TYPE: portbindings.VNIC_BAREMETAL,
            portbindings.HOST_ID: self.test_hostid
            }
        self.mock_port_bm.__getitem__ = mock.Mock(
            side_effect=lambda x: self.mock_port_bm.dict[x])

        self.mock_port_vm = mock.create_autospec(
            ports.Port).return_value
        self.mock_port_vm.network_id = self.testid2
        self.mock_port_vm.dict = {
            'id': self.testid2,
            'network_id': uuid.uuid4(),
            'mac_address': self.testmac,
            c.DEVICE_OWNER: c.COMPUTE_NOVA,
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_OVS,
            portbindings.VNIC_TYPE: portbindings.VNIC_NORMAL,
            portbindings.HOST_ID: self.test_hostid
        }
        self.mock_port_vm.__getitem__ = mock.Mock(
            side_effect=lambda x: self.mock_port_vm.dict[x])
        self.mock_port_vm.get = mock.Mock(
            side_effect=lambda x, y:
            self.mock_port_vm.dict[x] if x in self.mock_port_vm.dict else y)

        self.mock_port_dt = mock.create_autospec(
            ports.Port).return_value
        self.mock_port_dt.network_id = self.testid2
        self.mock_port_dt.dict = {
            'id': self.testid,
            'network_id': uuid.uuid4(),
            'mac_address': self.testmac,
            c.DEVICE_OWNER: c.COMPUTE_NOVA,
            portbindings.VIF_TYPE: portbindings.VIF_TYPE_OVS,
            portbindings.VNIC_TYPE: portbindings.VNIC_DIRECT,
            portbindings.HOST_ID: self.test_hostid,
            portbindings.PROFILE: self.profile_pci_slot
        }
        self.mock_port_dt.__getitem__ = mock.Mock(
            side_effect=lambda x: self.mock_port_dt.dict[x])
        self.mock_port_dt.get = mock.Mock(
            side_effect=lambda x, y:
            self.mock_port_dt.dict[x] if x in self.mock_port_dt.dict else y)

        self.mock_ports = [self.mock_port_bm]

        # Mocked port bindings
        self.mock_portbind_bm = mock.Mock(spec=ports.PortBinding)
        self.mock_portbind_bm.profile = self.profile_lli_no_mac
        self.mock_portbind_bm.dict = {'host': self.test_hostid}
        self.mock_portbind_bm.vnic_type = 'baremetal'
        self.mock_portbind_bm.vif_type = 'other'
        self.mock_portbind_bm.__getitem__ = mock.Mock(
            side_effect=lambda x: self.mock_portbind_bm.dict[x])

        self.mock_port_bm.bindings = [self.mock_portbind_bm]

        self.mock_portbind_dt = mock.Mock(spec=ports.PortBinding)
        self.mock_portbind_dt.profile = self.profile_lli_no_mac
        self.mock_portbind_dt.dict = {'host': self.test_hostid}
        self.mock_portbind_dt.vnic_type = portbindings.VNIC_DIRECT
        self.mock_portbind_dt.vif_type = portbindings.VIF_TYPE_OVS
        self.mock_portbind_dt.__getitem__ = mock.Mock(
            side_effect=lambda x: self.mock_portbind_dt.dict[x])

        self.mock_port_dt.bindings = [self.mock_portbind_dt]

        # define mocked port context
        # This isn't a true representation of a real
        # port context. The components are just being
        # staged for the tests to eventually move things
        # around to execute
        self.mock_port_context = mock.create_autospec(
            driver_context.PortContext).return_value
        self.mock_port_context.current = self.mock_port_bm
        self.mock_port_context.original = self.mock_port_vm
        self.mock_port_context._plugin_context = mock.MagicMock()
        self.mock_port_context.network = mock.Mock()
        self.mock_port_context.network.current = {
            'id': self.testid,
            'network_id': self.testid,
            provider_net.NETWORK_TYPE: 'vlan',
            provider_net.SEGMENTATION_ID: self.testsegid,
            provider_net.PHYSICAL_NETWORK: self.testphysnet
        }
        self.mock_port_context.segments_to_bind = [
            self.mock_port_context.network.current
        ]
