# Copyright (c) 2018 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import contextlib
import fixtures
import tempfile
import webob.exc

from tooz import coordination
from unittest import mock

from network_runner import api
from network_runner.types import validators
from neutron.common import test_lib
from neutron.objects import network
from neutron.objects import ports
from neutron.objects import trunk
from neutron.plugins.ml2.common import exceptions as ml2_exc
from neutron import quota
from neutron.tests.unit.plugins.ml2 import test_plugin
from neutron_lib.api.definitions import portbindings
from neutron_lib.api.definitions import provider_net
from neutron_lib.callbacks import resources

from networking_ansible import constants as c
from networking_ansible import exceptions as netans_ml2exc
from networking_ansible.tests.unit import base


class TestLibTestConfigFixture(fixtures.Fixture):
    def __init__(self):
        self._original_test_config = None

    def _setUp(self):
        self.addCleanup(self._restore)
        self._original_test_config = test_lib.test_config.copy()

    def _restore(self):
        if self._original_test_config is not None:
            test_lib.test_config = self._original_test_config


class NetAnsibleML2Base(test_plugin.Ml2PluginV2TestCase):
    def setUp(self):
        base.patch_neutron_quotas()
        with mock.patch.object(validators.ChoiceValidator, '__call__',
                               return_value=None):
            with mock.patch(c.COORDINATION) as m_coord:
                m_coord.get_coordinator = lambda *args: mock.create_autospec(
                    coordination.CoordinationDriver).return_value
                super(NetAnsibleML2Base, self).setUp()


@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver._is_port_supported')
@mock.patch('networking_ansible.ml2.mech_driver.provisioning_blocks',
            autospec=True)
@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver.ensure_port')
class TestBindPort(base.NetworkingAnsibleTestCase):
    def test_bind_port_not_supported(self,
                                     mock_ensure_port,
                                     mock_prov_blocks,
                                     mock_port_supported):
        mock_port_supported.return_value = False
        self.mech.bind_port(self.mock_port_context)
        mock_ensure_port.assert_not_called()

    def test_bind_port_supported(self,
                                 mock_ensure_port,
                                 mock_prov_blocks,
                                 mock_port_supported):
        mock_port_supported.return_value = True
        self.mech.bind_port(self.mock_port_context)
        mock_ensure_port.assert_called_once_with(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)


class TestIsPortSupported(base.NetworkingAnsibleTestCase):
    def test_is_port_supported_baremetal(self):
        self.assertTrue(
            self.mech._is_port_supported(self.mock_port_bm))

    def test_is_port_supported_normal(self):
        self.assertTrue(
            self.mech._is_port_supported(self.mock_port_vm))

    def test_is_port_supported_direct(self):
        self.assertTrue(
            self.mech._is_port_supported(self.mock_port_dt))

    def test_is_port_supported_invalid(self):
        self.mock_port_bm.dict[c.DEVICE_OWNER] = 'invalid'
        self.assertFalse(
            self.mech._is_port_supported(self.mock_port_bm))


@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver._is_port_supported')
class TestIsPortBound(base.NetworkingAnsibleTestCase):
    def test_is_port_bound(self, mock_port_supported):
        mock_port_supported.return_value = True
        self.assertTrue(
            self.mech._is_port_bound(self.mock_port_bm))

    def test_is_port_bound_normal(self, mock_port_supported):
        mock_port_supported.return_value = True
        self.assertTrue(
            self.mech._is_port_bound(self.mock_port_vm))

    def test_is_port_bound_direct(self, mock_port_supported):
        mock_port_supported.return_value = True
        self.assertTrue(
            self.mech._is_port_bound(self.mock_port_dt))

    def test_is_port_bound_not_other(self, mock_port_supported):
        self.mock_port_bm.dict['binding:vif_type'] = 'not-other'
        self.assertFalse(
            self.mech._is_port_bound(self.mock_port_bm))

    def test_is_port_bound_port_not_supported(self, mock_port_supported):
        mock_port_supported.return_value = False
        self.assertFalse(
            self.mech._is_port_bound(self.mock_port_bm))


@mock.patch.object(network.Network, 'get_object')
@mock.patch.object(api.NetworkRunner, 'create_vlan')
class TestCreateNetworkPostCommit(base.NetworkingAnsibleTestCase):
    def test_create_network_postcommit(self,
                                       mock_create_network,
                                       mock_get_network):
        mock_get_network.return_value = self.mock_net
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_called_once_with(self.testhost,
                                                    self.testsegid)

    def test_create_network_postcommit_manage_vlans_false(self,
                                                          mock_create_network,
                                                          mock_get_network):
        self.m_config.inventory[self.testhost]['manage_vlans'] = False
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_not_called()

    def test_create_network_postcommit_fails(self,
                                             mock_create_network,
                                             mock_get_network):
        mock_create_network.side_effect = Exception()
        mock_get_network.return_value = self.mock_net

        self.assertRaises(netans_ml2exc.NetworkingAnsibleMechException,
                          self.mech.create_network_postcommit,
                          self.mock_net_context)
        mock_create_network.assert_called_once_with(self.testhost,
                                                    self.testsegid)

    def test_create_network_postcommit_not_vlan(self,
                                                mock_create_network,
                                                mock_get_network):
        self.mock_net_context.current[provider_net.NETWORK_TYPE] = 'not-vlan'
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_not_called()

    def test_create_network_postcommit_not_segmentation_id(self,
                                                           mock_create_network,
                                                           mock_get_network):
        self.mock_net_context.current[provider_net.SEGMENTATION_ID] = ''
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_not_called()

    def test_create_network_postcommit_was_deleted(self,
                                                   mock_create_network,
                                                   mock_get_network):
        mock_get_network.return_value = None
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_not_called()

    def test_create_network_postcommit_segment_was_deleted(self,
                                                           mock_create_network,
                                                           mock_get_network):
        self.mock_net_context.current[provider_net.SEGMENTATION_ID] = '73'
        mock_get_network.return_value = self.mock_net
        self.mech.create_network_postcommit(self.mock_net_context)
        mock_create_network.assert_not_called()


@mock.patch.object(network.NetworkSegment, 'get_objects')
@mock.patch.object(api.NetworkRunner, 'delete_vlan')
class TestDeleteNetworkPostCommit(base.NetworkingAnsibleTestCase):
    def test_delete_network_postcommit(self,
                                       mock_delete_network,
                                       mock_get_segment):
        mock_get_segment.return_value = []
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_called_once_with(self.testhost,
                                                    self.testsegid)

    def test_delete_network_postcommit_manage_vlans_false(self,
                                                          mock_delete_network,
                                                          mock_get_segment):
        mock_get_segment.return_value = []
        self.m_config.inventory[self.testhost]['manage_vlans'] = False
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_not_called()

    def test_delete_network_postcommit_fails(self,
                                             mock_delete_network,
                                             mock_get_segment):
        mock_get_segment.return_value = []
        mock_delete_network.side_effect = Exception()
        self.assertRaises(netans_ml2exc.NetworkingAnsibleMechException,
                          self.mech.delete_network_postcommit,
                          self.mock_net_context)
        mock_delete_network.assert_called_once_with(self.testhost,
                                                    self.testsegid)

    def test_delete_network_postcommit_not_vlan(self,
                                                mock_delete_network,
                                                mock_get_segment):
        mock_get_segment.return_value = []
        self.mock_net_context.current[provider_net.NETWORK_TYPE] = 'not-vlan'
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_not_called()

    def test_delete_network_postcommit_not_segmentation_id(self,
                                                           mock_delete_network,
                                                           mock_get_segment):
        mock_get_segment.return_value = []
        self.mock_net_context.current[provider_net.SEGMENTATION_ID] = ''
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_not_called()

    def test_delete_network_postcommit_recreated_segment(self,
                                                         mock_delete_network,
                                                         mock_get_segment):
        mock_get_segment.return_value = [self.mock_netseg]
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_not_called()

    def test_delete_network_postcommit_different_segment(self,
                                                         mock_delete_network,
                                                         mock_get_segment):
        mock_get_segment.return_value = [self.mock_netseg2]
        self.mech.delete_network_postcommit(self.mock_net_context)
        mock_delete_network.assert_called_once()


@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver.ensure_port')
class TestDeletePortPostCommit(base.NetworkingAnsibleTestCase):
    def test_delete_port_postcommit_bound(self,
                                          mock_ensure_port):
        self.mech.delete_port_postcommit(self.mock_port_context)
        mock_ensure_port.assert_called_once_with(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid,
            delete=True)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._is_port_bound')
    def test_delete_port_postcommit_not_bound(self,
                                              mock_port_bound,
                                              mock_ensure_port):
        mock_port_bound.return_value = False
        self.mech.delete_port_postcommit(self.mock_port_context)
        mock_ensure_port.assert_not_called()


@mock.patch(c.COORDINATION)
@mock.patch('networking_ansible.config.Config')
class TestInit(base.NetworkingAnsibleTestCase):
    def test_intialize(self, m_config, m_coord):
        m_coord.get_coordinator = lambda *args: mock.create_autospec(
            coordination.CoordinationDriver).return_value
        m_config.return_value = base.MockConfig()
        self.mech.initialize()
        m_config.assert_called_once_with()

    def test_intialize_w_extra_params(self, m_config, m_coord):
        m_coord.get_coordinator = lambda *args: mock.create_autospec(
            coordination.CoordinationDriver).return_value
        m_config.return_value = base.MockConfig(self.testhost,
                                                self.testmac)
        m_config.return_value.add_extra_params()
        self.mech.initialize()
        self.assertEqual(self.mech.kwargs,
                         {self.testhost: {'stp_edge': True}})

    def test_intialize_w_custom_params(self, m_config, m_coord):
        m_coord.get_coordinator = lambda *args: mock.create_autospec(
            coordination.CoordinationDriver).return_value
        m_config.return_value = base.MockConfig(self.testhost,
                                                self.testmac)
        m_config.return_value.add_custom_params()
        self.mech.initialize()
        self.assertEqual(self.mech.kwargs,
                         {self.testhost: {'custom': 'param'}})


@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver._is_port_bound')
@mock.patch('networking_ansible.ml2.mech_driver.provisioning_blocks',
            autospec=True)
@mock.patch('networking_ansible.ml2.mech_driver.'
            'AnsibleMechanismDriver.ensure_port')
class TestUpdatePortPostCommit(base.NetworkingAnsibleTestCase):
    def test_update_port_postcommit_port_bound_curr(self,
                                                    mock_ensure_port,
                                                    mock_prov_blocks,
                                                    mock_port_bound):
        mock_port_bound.return_value = True
        self.mock_port_context.original = self.mock_port_context.current
        self.mech.update_port_postcommit(self.mock_port_context)
        mock_prov_blocks.provisioning_complete.assert_called_once_with(
            self.mock_port_context._plugin_context,
            self.testid,
            resources.PORT,
            c.NETWORKING_ENTITY)

    def test_update_port_postcommit_port_bound_orig(self,
                                                    mock_ensure_port,
                                                    mock_prov_blocks,
                                                    mock_port_bound):
        mock_port_bound.side_effect = [False, True]
        self.mock_port_context.original = self.mock_port_context.current
        self.mech.update_port_postcommit(self.mock_port_context)
        mock_ensure_port.assert_called_once_with(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)

    def test_update_port_postcommit_port_not_bound(self,
                                                   mock_ensure_port,
                                                   mock_prov_blocks,
                                                   mock_port_bound):
        mock_port_bound.return_value = False
        self.mech.update_port_postcommit(self.mock_port_context)
        mock_prov_blocks.provisioning_complete.assert_not_called()

    def test_update_port_postcommit_port_w_normal_port(self,
                                                       mock_ensure_port,
                                                       mock_prov_blocks,
                                                       mock_port_bound):
        mappings = [(self.testhost, self.testport)]
        self.m_config.port_mappings = {self.test_hostid: mappings}
        self.mock_port_context.current = self.mock_port_context.original
        self.mech.update_port_postcommit(self.mock_port_context)
        mock_ensure_port.assert_called_once_with(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)

    def test_update_port_postcommit_port_w_direct_port(self,
                                                       mock_ensure_port,
                                                       mock_prov_blocks,
                                                       mock_port_bound):
        mappings = [(self.testhost, self.testport)]
        sriov_host_id = '{}-{}'.format(self.test_hostid, self.test_pci_addr)
        self.m_config.port_mappings = {sriov_host_id: mappings}
        self.mock_port_context.current = self.mock_port_dt
        self.mock_port_context.original = self.mock_port_dt
        self.mech.update_port_postcommit(self.mock_port_context)
        mock_ensure_port.assert_called_once_with(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)


class TestLinkInfo(base.NetworkingAnsibleTestCase):
    def test_switch_meta_from_link_info_obj_no_net(self):
        mappings, segmentation_id = \
            self.mech._switch_meta_from_link_info(self.mock_port_bm)
        for switch_name, switch_port in mappings:
            self.assertEqual(switch_name, self.testhost)
            self.assertEqual(switch_port, self.testport)
            self.assertEqual(segmentation_id, '')

    def test_switch_meta_from_link_info_obj_net(self):
        mappings, segmentation_id = \
            self.mech._switch_meta_from_link_info(
                self.mock_port_bm,
                self.mock_net_context.current)
        for switch_name, switch_port in mappings:
            self.assertEqual(switch_name, self.testhost)
            self.assertEqual(switch_port, self.testport)
            self.assertEqual(segmentation_id, self.testsegid)

    def test_switch_meta_from_link_info_context_no_net(self):
        mappings, segmentation_id = \
            self.mech._switch_meta_from_link_info(
                self.mock_port_context.current)
        for switch_name, switch_port in mappings:
            self.assertEqual(switch_name, self.testhost)
            self.assertEqual(switch_port, self.testport)
            self.assertEqual(segmentation_id, '')

    def test_switch_meta_from_link_info_context_net(self):
        mappings, segmentation_id = \
            self.mech._switch_meta_from_link_info(
                self.mock_port_context.current,
                self.mock_net_context.current)
        for switch_name, switch_port in mappings:
            self.assertEqual(switch_name, self.testhost)
            self.assertEqual(switch_port, self.testport)
            self.assertEqual(segmentation_id, self.testsegid)

    def test_switch_meta_from_link_info_context_no_name(self):
        self.mock_port_bm.bindings[0].profile = self.profile_lli_no_info
        self.m_config.mac_map = {self.testmac.upper(): self.testhost + '+map'}
        mappings, segmentation_id = \
            self.mech._switch_meta_from_link_info(
                self.mock_port_bm)
        for switch_name, switch_port in mappings:
            self.assertEqual(switch_name, self.testhost + '+map')
            self.assertEqual(switch_port, self.testport)
            self.assertEqual(segmentation_id, '')

    def test_switch_meta_from_link_info_context_no_lli(self):
        self.mock_port_bm.bindings[0].profile[c.LLI] = {}
        self.assertRaises(netans_ml2exc.LocalLinkInfoMissingException,
                          self.mech._switch_meta_from_link_info,
                          self.mock_port_bm)

    def test_link_info_from_port_port_not_supported(self):
        # If this test fails from a missing key in the future
        # it's generall safe to just throw the key into this dict
        # it's testing a case when the value passed it not a port
        # object. So we just port properties into a dict so the
        # properties are there but it's not a proper port object
        port = {'id': self.testid,
                portbindings.VNIC_TYPE: 'not-supported',
                c.DEVICE_OWNER: c.BAREMETAL_NONE}
        self.assertRaises(netans_ml2exc.LocalLinkInfoMissingException,
                          self.mech._switch_meta_from_link_info,
                          port)


@mock.patch('network_runner.api.NetworkRunner.delete_port')
class TestDeleteSwitchPort(base.NetworkingAnsibleTestCase):
    def test_delete_switch_port_fails(self, mock_delete):
        mock_delete.side_effect = Exception()
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._delete_switch_port,
                          1,
                          2)

    def test_delete_switch_port(self, mock_delete):
        self.mech._delete_switch_port(self.testhost, self.testport)
        mock_delete.assert_called_once_with(self.testhost, self.testport)


@mock.patch.object(ports.Port, 'get_objects')
@mock.patch.object(network.Network, 'get_object')
class TestIsDeletedPortInUse(base.NetworkingAnsibleTestCase):
    def test_is_in_use_no_ports(self,
                                mock_net_get_object,
                                mock_port_get_objects):
        mock_port_get_objects.return_value = []
        mock_net_get_object.return_value = self.mock_net
        self.assertFalse(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_one_port(self,
                                mock_net_get_object,
                                mock_port_get_objects):
        mock_port_get_objects.return_value = [self.mock_port_bm]
        mock_net_get_object.return_value = self.mock_net
        self.assertTrue(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_one_port_virtnet(self,
                                        mock_net_get_object,
                                        mock_port_get_objects):
        mock_port_get_objects.return_value = [self.mock_port_bm]
        mock_net_get_object.return_value = self.mock_net
        self.mock_net.segments = [self.mock_netseg3]
        self.assertFalse(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_two_port_virtnet_and_physnet(self,
                                                    mock_net_get_object,
                                                    mock_port_get_objects):
        mock_port_get_objects.return_value = [self.mock_port_bm,
                                              self.mock_port_vm]
        self.mock_net.segments = [self.mock_netseg3]

        mock_net2 = mock.create_autospec(
            network.Network).return_value
        mock_net2.segments = [self.mock_netseg]

        mock_net_get_object.side_effect = [self.mock_net, mock_net2]
        self.assertTrue(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_one_port_no_net(self,
                                       mock_net_get_object,
                                       mock_port_get_objects):
        mock_port_get_objects.return_value = [self.mock_port_bm]
        mock_net_get_object.return_value = None
        self.assertFalse(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_one_port_no_binding(self,
                                           mock_net_get_object,
                                           mock_port_get_objects):
        self.mock_port_bm.bindings.pop()
        mock_port_get_objects.return_value = [self.mock_port_bm]
        mock_net_get_object.return_value = self.mock_net
        self.assertFalse(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))

    def test_is_in_use_one_port_no_lli(self,
                                       mock_net_get_object,
                                       mock_port_get_objects):
        self.mock_port_bm.bindings[0].profile = {}
        mock_port_get_objects.return_value = [self.mock_port_bm]
        mock_net_get_object.return_value = self.mock_net
        self.assertFalse(
            self.mech._is_deleted_port_in_use(self.testphysnet, 2, 3))


@mock.patch.object(coordination.CoordinationDriver, 'get_lock')
@mock.patch.object(ports.Port, 'get_object')
@mock.patch('network_runner.api.NetworkRunner.has_host')
class TestEnsurePort(base.NetworkingAnsibleTestCase):
    def test_ensure_port_no_host(self,
                                 mock_has_host,
                                 mock_port_get_object,
                                 mock_get_lock):
        mock_has_host.return_value = False
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech.ensure_port,
                          self.mock_port_context.current,
                          self.mock_port_context._plugin_context,
                          self.testhost,
                          self.testport,
                          self.testphysnet,
                          self.mock_port_context,
                          self.testsegid)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._delete_switch_port')
    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._is_deleted_port_in_use')
    def test_ensure_port_no_port_delete(self,
                                        mock_is_deleted,
                                        mock_delete_port,
                                        mock_has_host,
                                        mock_port_get_object,
                                        mock_get_lock):
        mock_port_get_object.return_value = None
        mock_is_deleted.return_value = False
        self.mech.ensure_port(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)
        mock_delete_port.assert_called_once_with(self.testhost, self.testport)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._delete_switch_port')
    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._is_deleted_port_in_use')
    def test_ensure_port_no_port_keep(self,
                                      mock_is_deleted,
                                      mock_delete_port,
                                      mock_has_host,
                                      mock_port_get_object,
                                      mock_get_lock):
        mock_port_get_object.return_value = None
        mock_is_deleted.return_value = True
        self.mech.ensure_port(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)
        mock_delete_port.assert_not_called()

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_port_set_port_state(self,
                                        mock_set_state,
                                        mock_has_host,
                                        mock_port_get_object,
                                        mock_get_lock):
        mock_port_get_object.return_value = self.mock_port_bm
        self.mech.ensure_port(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)
        mock_set_state.assert_called_once_with(
            self.mock_port_bm,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_port_set_port_state_binding(self,
                                                mock_set_state,
                                                mock_has_host,
                                                mock_port_get_object,
                                                mock_get_lock):
        mock_port_get_object.return_value = self.mock_port_bm
        mock_set_state.return_value = True
        self.mech.ensure_port(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)
        mock_set_state.assert_called_once_with(
            self.mock_port_bm,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport)
        self.mock_port_context.set_binding.assert_called_once()

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_port_set_port_state_no_binding(self,
                                                   mock_set_state,
                                                   mock_has_host,
                                                   mock_port_get_object,
                                                   mock_get_lock):
        mock_port_get_object.return_value = self.mock_port_bm
        mock_set_state.return_value = False
        self.mech.ensure_port(
            self.mock_port_context.current,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport,
            self.testphysnet,
            self.mock_port_context,
            self.testsegid)
        mock_set_state.assert_called_once_with(
            self.mock_port_bm,
            self.mock_port_context._plugin_context,
            self.testhost,
            self.testport)
        self.mock_port_context.set_binding.assert_not_called()

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_port_normal_port_delete_false(self,
                                                  mock_set_state,
                                                  mock_has_host,
                                                  mock_port_get_object,
                                                  mock_get_lock):
        self.mech.ensure_port(self.mock_port_vm,
                              self.mock_port_vm,
                              self.testhost,
                              self.testport,
                              self.testphysnet,
                              self.mock_port_vm,
                              self.testsegid,
                              delete=False)
        mock_set_state.assert_called_with(self.mock_port_vm,
                                          self.mock_port_vm,
                                          self.testhost,
                                          self.testport)

    @mock.patch('network_runner.api.NetworkRunner.delete_trunk_vlan')
    @mock.patch.object(ports.Port, 'get_objects')
    def test_ensure_port_normal_port_delete_true(self,
                                                 mock_get_objects,
                                                 mock_delete_vlan,
                                                 mock_has_host,
                                                 mock_port_get_object,
                                                 mock_get_lock):
        self.mech.ensure_port(self.mock_port_vm,
                              self.mock_port_vm,
                              self.testhost,
                              self.testport,
                              self.testphysnet,
                              self.mock_port_vm,
                              self.testsegid,
                              delete=True)
        mock_delete_vlan.assert_called_with(self.testhost,
                                            self.testport,
                                            self.testsegid)

    @mock.patch('network_runner.api.NetworkRunner.delete_trunk_vlan')
    @mock.patch.object(ports.Port, 'get_objects')
    def test_ensure_port_no_delete_w_active_ports_vm(self,
                                                     mock_get_objects,
                                                     mock_delete_vlan,
                                                     mock_has_host,
                                                     mock_port_get_object,
                                                     mock_get_lock):
        '''
        Check if there are port bindings associated with the same
        compute host that the port is being deleted from. If there
        are other port bindings on that compute node make sure that
        the vlan won't be deleted from the compute node's switchport
        so that the other portbindings don't loose connectivity
        '''
        mock_get_objects.return_value = [self.mock_port_bm]
        self.mech.ensure_port(self.mock_port_vm,
                              self.mock_port_vm,
                              self.testhost,
                              self.testport,
                              self.testphysnet,
                              self.mock_port_vm,
                              self.testsegid,
                              delete=True)
        mock_delete_vlan.assert_not_called()

    @mock.patch('network_runner.api.NetworkRunner.delete_trunk_vlan')
    @mock.patch.object(ports.Port, 'get_objects')
    def test_ensure_port_direct_port_delete_true(self,
                                                 mock_get_objects,
                                                 mock_delete_vlan,
                                                 mock_has_host,
                                                 mock_port_get_object,
                                                 mock_get_lock):
        self.mech.ensure_port(self.mock_port_dt,
                              self.mock_port_dt,
                              self.testhost,
                              self.testport,
                              self.testphysnet,
                              self.mock_port_dt,
                              self.testsegid,
                              delete=True)
        mock_delete_vlan.assert_called_with(self.testhost,
                                            self.testport,
                                            self.testsegid)

    @mock.patch('network_runner.api.NetworkRunner.delete_trunk_vlan')
    @mock.patch.object(ports.Port, 'get_objects')
    @mock.patch.object(network.Network, 'get_object')
    def test_ensure_port_no_delete_w_active_ports_dt(self,
                                                     mock_get_object,
                                                     mock_get_objects,
                                                     mock_delete_vlan,
                                                     mock_has_host,
                                                     mock_port_get_object,
                                                     mock_get_lock):
        '''
        Check if there are port bindings associated with the same
        compute host that the port is being deleted from. If there
        are other port bindings on that compute node make sure that
        the vlan won't be deleted from the compute node's switchport
        so that the other portbindings don't loose connectivity
        '''
        # Setup port mappings with an SRIOV designation in it
        mappings = [(self.testhost, self.testport)]
        sriov_host_id = '{}-{}'.format(self.test_hostid, self.test_pci_addr)
        sriov_host_id2 = '{}-{}'.format(self.test_hostid, self.test_pci_addr2)
        self.m_config.port_mappings = {sriov_host_id: mappings,
                                       sriov_host_id2: mappings}

        mock_get_object.return_value = self.mock_net

        # Reuse VM port by reassigning it DIRECT properties
        self.mock_port_vm.dict[portbindings.VNIC_TYPE] = \
            portbindings.VNIC_DIRECT
        self.mock_port_vm.dict[portbindings.PROFILE] = \
            self.profile_pci_slot2
        self.mock_port_vm.bindings = [self.mock_portbind_dt]
        mock_get_objects.return_value = [self.mock_port_vm]

        self.mech.ensure_port(self.mock_port_dt,
                              self.mock_port_dt,
                              self.testhost,
                              self.testport,
                              self.testphysnet,
                              self.mock_port_dt,
                              self.testsegid,
                              delete=True)
        mock_delete_vlan.assert_not_called()


@mock.patch.object(ports.Port, 'get_object')
class TestEnsureSubports(base.NetworkingAnsibleTestCase):
    @mock.patch.object(coordination.CoordinationDriver, 'get_lock')
    def test_ensure_subports_deleted(self,
                                     mock_get_lock,
                                     mock_port_get_object):
        mock_port_get_object.return_value = None
        self.mech.ensure_subports(self.testid, 'testdb')
        mock_get_lock.assert_not_called()

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_subports_valid(self,
                                   mock_set_state,
                                   mock_port_get_object):
        mock_port_get_object.return_value = self.mock_port_bm
        self.mech.ensure_subports(self.testid, 'testdb')
        mock_set_state.assert_called_once_with(self.mock_port_bm,
                                               'testdb',
                                               self.testhost,
                                               self.testport)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._set_port_state')
    def test_ensure_subports_invalid(self,
                                     mock_set_state,
                                     mock_port_get_object):
        mock_port_get_object.side_effect = [self.mock_port_bm, None]
        self.mech.ensure_subports(self.testid, 'testdb')
        mock_set_state.assert_not_called()


class TestSetPortState(base.NetworkingAnsibleTestCase):
    def test_set_port_state_no_port(self):
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          None,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._switch_meta_from_link_info')
    def test_set_port_state_no_switch_name(self, mock_link, mock_network):
        mock_network.return_value = None
        mock_link.return_value = ([(None, '123')], '')
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._switch_meta_from_link_info')
    def test_set_port_state_no_switch_port(self, mock_link, mock_network):
        mock_network.return_value = None
        mock_link.return_value = ([('123', None)], '')
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._switch_meta_from_link_info')
    @mock.patch('network_runner.api.NetworkRunner.has_host')
    def test_set_port_state_no_inventory_switch(self, mock_host, mock_link):
        mock_link.return_value = ([('123', '345')], '')
        mock_host.return_value = False
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch('networking_ansible.ml2.mech_driver.'
                'AnsibleMechanismDriver._switch_meta_from_link_info')
    def test_set_port_state_no_switch_port_or_name(self,
                                                   mock_link,
                                                   mock_network):
        mock_link.return_value = ([(None, None)], '')
        mock_network.return_value = None
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    def test_set_port_state_no_network(self, mock_network):
        mock_network.return_value = None
        self.assertRaises(ml2_exc.MechanismDriverError,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch.object(trunk.Trunk, 'get_object')
    @mock.patch.object(ports.Port, 'get_object')
    @mock.patch.object(api.NetworkRunner, 'conf_trunk_port')
    def test_set_port_state_trunk(self,
                                  mock_conf_trunk_port,
                                  mock_port_bm,
                                  mock_trunk,
                                  mock_network):
        mock_network.return_value = self.mock_net
        mock_trunk.return_value = self.mock_trunk
        self.mech._set_port_state(self.mock_port_bm, 'db',
                                  self.testhost, self.testport)
        mock_conf_trunk_port.assert_called_once_with(self.testhost,
                                                     self.testport,
                                                     self.testsegid,
                                                     [self.testsegid2])

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch.object(trunk.Trunk, 'get_object')
    @mock.patch.object(ports.Port, 'get_object')
    @mock.patch.object(api.NetworkRunner, 'conf_access_port')
    def test_set_port_state_access(self,
                                   mock_conf_access_port,
                                   mock_port_bm,
                                   mock_trunk,
                                   mock_network):
        mock_network.return_value = self.mock_net
        mock_trunk.return_value = None
        self.mech._set_port_state(self.mock_port_bm, 'db',
                                  self.testhost, self.testport)
        mock_conf_access_port.assert_called_once_with(self.testhost,
                                                      self.testport,
                                                      self.testsegid)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch.object(trunk.Trunk, 'get_object')
    @mock.patch.object(ports.Port, 'get_object')
    @mock.patch.object(api.NetworkRunner, 'conf_access_port')
    def test_set_port_state_access_failure(self,
                                           mock_conf_access_port,
                                           mock_port_bm,
                                           mock_trunk,
                                           mock_network):
        mock_network.return_value = self.mock_net
        mock_trunk.return_value = None
        self.mech._set_port_state(self.mock_port_bm, 'db',
                                  self.testhost, self.testport)
        mock_conf_access_port.side_effect = Exception()
        self.assertRaises(netans_ml2exc.NetworkingAnsibleMechException,
                          self.mech._set_port_state,
                          self.mock_port_bm,
                          'db', self.testhost, self.testport)

    @mock.patch.object(network.Network, 'get_object')
    @mock.patch.object(trunk.Trunk, 'get_object')
    @mock.patch.object(api.NetworkRunner, 'add_trunk_vlan')
    def test_set_port_state_normal(self,
                                   mock_add_trunk_vlan,
                                   mock_trunk,
                                   mock_network):

        mock_network.return_value = self.mock_net
        mock_trunk.return_value = None
        self.mech._set_port_state(self.mock_port_vm, 'db',
                                  self.testhost, self.testport)
        mock_add_trunk_vlan.assert_called_once_with(self.testhost,
                                                    self.testport,
                                                    self.testsegid)


@mock.patch.object(api.NetworkRunner, 'create_vlan')
class TestML2PluginIntegration(NetAnsibleML2Base):
    _mechanism_drivers = ['ansible']
    HOSTS = ['testinghost', 'otherhost']
    CIDR = '10.0.0.0/24'

    CONFIG_CONTENT = {
        'ansible:{:s}'.format(host): [
            'ansible_network_os=openvswitch\n',
            'ansible_host=host_ip\n',
            'ansible_user=user\n',
            'ansible_pass=password\n',
        ] for host in HOSTS
    }
    CONFIG_CONTENT['ansible:otherhost'].append('manage_vlans=False')

    LOCAL_LINK_INFORMATION = [{
        'switch_info': HOSTS[0],
        'switch_id': 'foo',
        'port_id': 'bar',
    }]

    UNBOUND_PORT_SPEC = {
        'device_owner': 'baremetal:none',
        'device_id': 'some-id',
    }

    BIND_PORT_UPDATE = {
        'port': {
            'binding:host_id': 'foo',
            portbindings.VNIC_TYPE: portbindings.VNIC_BAREMETAL,
            'binding:profile': {
                'local_link_information': LOCAL_LINK_INFORMATION,
            },
        },
    }

    def setUp(self):
        self.useFixture(TestLibTestConfigFixture())
        self.filename = tempfile.mktemp(prefix='test_anet')
        self._configure()
        super(TestML2PluginIntegration, self).setUp()
        seg_id = self.vlan_range.split(':')[0]
        self.network_spec = {
            provider_net.PHYSICAL_NETWORK: self.physnet,
            provider_net.NETWORK_TYPE: 'vlan',
            provider_net.SEGMENTATION_ID: seg_id,
            'arg_list': (
                provider_net.PHYSICAL_NETWORK,
                provider_net.NETWORK_TYPE,
                provider_net.SEGMENTATION_ID,
            ),
            'admin_state_up': True
        }
        make_res = mock.patch.object(quota.QuotaEngine, 'make_reservation')
        self.mock_quota_make_res = make_res.start()
        commit_res = mock.patch.object(quota.QuotaEngine, 'commit_reservation')
        self.mock_quota_commit_res = commit_res.start()

    def _write_config_content(self):
        with open(self.filename, 'w') as f:
            for section, content in self.CONFIG_CONTENT.items():
                f.write("[{:s}]\n".format(section))
                f.writelines(content)
                f.write("\n")

    def _configure(self):
        """Create config for the mech driver."""
        test_lib.test_config.setdefault('config_files', []).append(
            self.filename)
        self._write_config_content()

    def _create_network_with_spec(self, name, spec):
        res = self._create_network(self.fmt, name, **spec)
        network = self.deserialize(self.fmt, res)
        return res, network

    def test_create_network(self, m_create_vlan):
        res, _ = self._create_network_with_spec('tenant', self.network_spec)
        self.assertEqual(webob.exc.HTTPCreated.code, res.status_int)
        expected_calls = [
            mock.call(
                host,
                int(self.network_spec[provider_net.SEGMENTATION_ID]))
            for host in self.HOSTS if 'manage_vlans=False' not in
            self.CONFIG_CONTENT['ansible:%s' % host]]
        self.assertItemsEqual(
            expected_calls,
            m_create_vlan.call_args_list)

    @mock.patch.object(api.NetworkRunner, 'delete_vlan')
    def test_delete_network(self, m_delete_vlan, m_create_vlan):
        res, network = self._create_network_with_spec('tenant',
                                                      self.network_spec)
        m_delete_vlan.reset_mock()

        req = self.new_delete_request('networks', network['network']['id'])
        res = req.get_response(self.api)
        self.assertEqual(webob.exc.HTTPNoContent.code, res.status_int)
        expected_calls = [
            mock.call(
                host,
                int(self.network_spec[provider_net.SEGMENTATION_ID]))
            for host in self.HOSTS if 'manage_vlans=False' not in
            self.CONFIG_CONTENT['ansible:%s' % host]]
        self.assertItemsEqual(
            expected_calls,
            m_delete_vlan.call_args_list)

    @contextlib.contextmanager
    def _create_unbound_port(self):
        """Create a bound port in a network.

        Network is created using self.network_spec defined in the setUp()
        method of this class. Port attributes are defined in the
        UNBOUND_PORT_SPEC.

        """
        with self.network('tenant', **self.network_spec) as n:
            with self.subnet(network=n, cidr=self.CIDR) as s:
                with self.port(
                        subnet=s,
                        **self.UNBOUND_PORT_SPEC
                        ) as p:
                    yield p

    @mock.patch.object(api.NetworkRunner, 'conf_access_port')
    def test_update_port_unbound(self, m_conf_access_port, m_create_vlan):
        with self._create_unbound_port() as p:
            req = self.new_update_request(
                'ports',
                self.BIND_PORT_UPDATE,
                p['port']['id'])
            m_conf_access_port.reset_mock()

            port = self.deserialize(
                self.fmt, req.get_response(self.api))

            m_conf_access_port.called_once_with(
                'update_access_port',
                self.HOSTS[0],
                self.LOCAL_LINK_INFORMATION[0]['port_id'],
                self.network_spec[provider_net.SEGMENTATION_ID])
            self.assertNotEqual(
                portbindings.VIF_TYPE_BINDING_FAILED,
                port['port'][portbindings.VIF_TYPE])

    @mock.patch.object(api.NetworkRunner, 'delete_port')
    def test_delete_port(self, m_delete_port, m_create_vlan):
        with self._create_unbound_port() as p:
            req = self.new_update_request(
                'ports',
                self.BIND_PORT_UPDATE,
                p['port']['id'])
            port = self.deserialize(
                self.fmt, req.get_response(self.api))
            m_delete_port.reset_mock()

            self._delete('ports', port['port']['id'])
            m_delete_port.called_once_with(
                'delete_port',
                self.HOSTS[0],
                self.LOCAL_LINK_INFORMATION[0]['port_id'],
                self.network_spec[provider_net.SEGMENTATION_ID])

    @mock.patch.object(api.NetworkRunner, 'conf_access_port')
    def test_update_port_error(self, m_conf_access_port, m_create_vlan):
        with self._create_unbound_port() as p:
            m_conf_access_port.side_effect = Exception('foo')
            self.assertEqual(
                portbindings.VIF_TYPE_UNBOUND,
                p['port'][portbindings.VIF_TYPE])

            req = self.new_update_request(
                'ports',
                self.BIND_PORT_UPDATE,
                p['port']['id'])
            res = req.get_response(self.api)
            port = self.deserialize(self.fmt, res)
            # NOTE(jlibosva): The port update was successful as the binding was
            # changed. The way how to check the error is via its binding value
            self.assertEqual(
                portbindings.VIF_TYPE_BINDING_FAILED,
                port['port'][portbindings.VIF_TYPE])
            self.assertEqual(webob.exc.HTTPOk.code, res.status_int)
