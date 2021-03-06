.. _configure:

ML2 Configuriation
~~~~~~~~~~~~~~~~~~

This section decribes how to configure Neutron configuration files to enable
the networking-ansible ML2 driver and configure switch devices that will be
managed by networking-ansible.

#. Configure type_drivers and mechanism_drivers and network_vlan_ranges.

    Add ``vlan`` to ``type_drivers``, ``ansible`` to ``mechanism_drivers``, and
    define the ``network_vlan_range`` that will be used by the openstack networks that use
    this driver.

    .. code-block:: ini

      [ml2]
      type_drivers = vlan
      mechanism_drivers = ansible

      [ml2_type_vlan]
      network_vlan_ranges = physnet1:1000:1099

    * Do not remove other values from these comma delimited lists if there are
      are already values present. Append a comma and your value to the list.
    * physnet1 is an identifier configured in the bridge_mapping
      TODO(radez) doc ref to bridge_mapping
    * the VLAN range (1000:1099 in this example) is provided by the switch
      administor. This range is the VLAN IDs assigned by Neutron when a network is created.


#. Point the ML2 driver at a coordination service

    In the ML2 configuration file, ``/etc/neutron/plugins/ml2/ml2_conf.ini``,
    add a URI for a coordination service to ``coordination_uri`` under the section
    ``ml2_ansible``. Most commonly, this will be an etcd or redis uri, but any
    `Tooz <https://docs.openstack.org/tooz/>`_ backend that supports locks can be used.

    .. code-block:: ini

    [ml2_ansible]
    coordination_uri = etcd://127.0.0.1:2379

#. Add a section for each switch device that will be managed by networking-ansible.

    Create a section for each host with a section name prefixed by ``ansible:``
    in ``/etc/neutron/plugins/ml2/ml2_conf.ini``.

    .. code-block:: ini

      [ansible:myhostname]
      ansible_network_os=junos
      ansible_host=10.10.2.250
      ansible_user=ansible
      ansible_ssh_pass=password

    * myhostname is an arbitrary internal identifier used only in ironic's link_local_information.
    * ansible_network_os is a valid Ansible Networking value to indicate switch type.
      Tested with networking-ansible: openvswitch, junos
      Untested but valid with networking-ansible: eos, nxos
      See contributor/provider for more information.
    * ansible_host is the IP address or hostname used to connect to the switch.
    * ansible_user username of the credentials used to connect to the switch.
    * ansible_pass password of the credentials used to connect to the switch.

    OpenStack Specific parameters:

    .. code-block:: ini

      mac=01:23:45:67:89:AB
      manage_vlans=True

    * mac is the MAC address of the switch as provided by lldp. This is optional to provide and
      specific to OpenStack ML2 use cases. It is used for zero touch provisioning using Ironic
      introspection. Introspection gathers the switch's MAC and node's port provided by lldp
      and populates the baremetal node's local_link_information. If this parameter is provided in
      the ML2 ini configuration it will be used to match against the lldp provided MAC to
      populate internally generated ansible playbooks with the appropriate host name for the switch.
    * manage_vlans is optional and defaults to True. Set this to False for a
      switch if networking-ansible should not create and delete VLANs on the device.

    Additional parameters and examples:

    .. code-block:: ini

      ansible_ssh_private_key_file=/path/to/ansible-ssh
      ansible_ssh_common_args='-o ProxyCommand="ssh -W %h:%p -q user@hostname"'

    * ansible_ssh_private_key_file can be used as an alternative to ansible_pass
      to use ssh key authentication instead of password authentication.
    * ansible_ssh_common_args is passed to the ssh command Ansible uses.
      In the example above the ProxyCommand is used to connect to a switch through a proxy.

    Parameters pass through automatically:

    * All parameters not mentioned here are passed from neutron to ansible through inventory.
      Any inventory parameter that is supported by the version of Ansible should be able to
      be defined in the switch's configuration stanza and will passed to Ansible.

#. If trunk ports are expected to be used, ensure the trunk service plugin is loaded.
   Its enabled by including "trunk" in the service_plugins list in /etc/neutron/neutron.conf.

     .. code-block:: console

       service_plugins=router,segments,trunk

#. Restart the Neutron API service

     .. code-block:: ini

       $ service neutron-server restart
