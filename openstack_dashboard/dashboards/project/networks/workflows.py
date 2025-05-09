# Copyright 2012 NEC Corporation
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


import logging

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
import netaddr

from horizon import exceptions
from horizon import forms
from horizon import messages
from horizon import workflows

from openstack_dashboard import api
from openstack_dashboard.dashboards.project.networks.subnets import utils
from openstack_dashboard import policy
from openstack_dashboard.utils import settings as setting_utils


LOG = logging.getLogger(__name__)


class CreateNetworkInfoAction(workflows.Action):
    net_name = forms.CharField(max_length=255,
                               label=_("Network Name"),
                               required=False)
    admin_state = forms.BooleanField(
        label=_("Enable Admin State"),
        initial=True,
        required=False,
        help_text=_("If checked, the network will be enabled."))
    shared = forms.BooleanField(label=_("Shared"), initial=False,
                                required=False,
                                help_text=_("Share the network between "
                                            "projects"))
    with_subnet = forms.BooleanField(label=_("Create Subnet"),
                                     widget=forms.CheckboxInput(attrs={
                                         'class': 'switchable',
                                         'data-slug': 'with_subnet',
                                         'data-hide-tab': 'create_network__'
                                                          'createsubnetinfo'
                                                          'action,'
                                                          'create_network__'
                                                          'createsubnetdetail'
                                                          'action',
                                         'data-hide-on-checked': 'false'
                                     }),
                                     initial=True,
                                     required=False)
    az_hints = forms.MultipleChoiceField(
        label=_("Availability Zone Hints"),
        required=False,
        help_text=_("Availability zones where the DHCP agents may be "
                    "scheduled. Leaving this unset is equivalent to "
                    "selecting all availability zones"))
    mtu = forms.IntegerField(
        label=_("MTU"), required=False, min_value=68,
        help_text=_("Maximum Transmission Unit. "
                    "Minimum is 68 bytes for the IPv4 subnet "
                    "and 1280 bytes for the IPv6 subnet."))

    def __init__(self, request, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        if not policy.check((("network", "create_network:shared"),), request):
            self.fields['shared'].widget = forms.HiddenInput()
        try:
            if api.neutron.is_extension_supported(request,
                                                  'network_availability_zone'):
                zones = api.neutron.list_availability_zones(
                    self.request, 'network', 'available')
                self.fields['az_hints'].choices = [(zone['name'], zone['name'])
                                                   for zone in zones]
            else:
                del self.fields['az_hints']
        except Exception:
            msg = _('Failed to get availability zone list.')
            messages.warning(request, msg)
            del self.fields['az_hints']

    class Meta(object):
        name = _("Network")
        help_text = _('Create a new network. '
                      'In addition, a subnet associated with the network '
                      'can be created in the following steps of this wizard.')


class CreateNetworkInfo(workflows.Step):
    action_class = CreateNetworkInfoAction
    contributes = ("net_name", "admin_state", "with_subnet", "shared",
                   "az_hints", "mtu")


class CreateSubnetInfoAction(workflows.Action):
    subnet_name = forms.CharField(max_length=255,
                                  widget=forms.TextInput(attrs={
                                  }),
                                  label=_("Subnet Name"),
                                  required=False)

    address_source = forms.ChoiceField(
        required=False,
        label=_('Network Address Source'),
        choices=[('manual', _('Enter Network Address manually')),
                 ('subnetpool', _('Allocate Network Address from a pool'))],
        widget=forms.ThemableSelectWidget(attrs={
            'class': 'switchable',
            'data-slug': 'source',
        }))

    subnetpool = forms.ChoiceField(
        label=_("Address pool"),
        widget=forms.ThemableSelectWidget(attrs={
            'class': 'switched switchable',
            'data-required-when-shown': 'true',
            'data-slug': 'subnetpool',
            'data-switch-on': 'source',
            'data-source-subnetpool': _('Address pool')},
            data_attrs=('name', 'prefixes',
                        'ip_version',
                        'min_prefixlen',
                        'max_prefixlen',
                        'default_prefixlen'),
            transform=lambda x: "%s (%s)" % (x.name, ", ".join(x.prefixes))
                                if 'prefixes' in x else "%s" % (x.name)),
        required=False)

    prefixlen = forms.ChoiceField(widget=forms.ThemableSelectWidget(attrs={
                                  'class': 'switched',
                                  'data-switch-on': 'subnetpool',
                                  }),
                                  label=_('Network Mask'),
                                  required=False)

    cidr = forms.IPField(label=_("Network Address"),
                         required=False,
                         initial="",
                         error_messages={
                             'required': _('Specify "Network Address" or '
                                           'clear "Create Subnet" checkbox '
                                           'in previous step.')},
                         widget=forms.TextInput(attrs={
                             'class': 'switched',
                             'data-switch-on': 'source',
                             'data-source-manual': _("Network Address"),
                         }),
                         help_text=_("Network address in CIDR format "
                                     "(e.g. 192.168.0.0/24, 2001:DB8::/48)"),
                         version=forms.IPv4 | forms.IPv6,
                         mask=True)
    ip_version = forms.ChoiceField(choices=[(4, 'IPv4'), (6, 'IPv6')],
                                   widget=forms.ThemableSelectWidget(attrs={
                                       'class': 'switchable',
                                       'data-slug': 'ipversion',
                                   }),
                                   label=_("IP Version"),
                                   required=False)
    gateway_ip = forms.IPField(
        label=_("Gateway IP"),
        widget=forms.TextInput(attrs={
            'class': 'switched',
            'data-switch-on': 'gateway_ip',
            'data-source-manual': _("Gateway IP")
        }),
        required=False,
        initial="",
        help_text=_("IP address of Gateway (e.g. 192.168.0.254) "
                    "The default value is the first IP of the "
                    "network address "
                    "(e.g. 192.168.0.1 for 192.168.0.0/24, "
                    "2001:DB8::1 for 2001:DB8::/48). "
                    "If you use the default, leave blank. "
                    "If you do not want to use a gateway, "
                    "check 'Disable Gateway' below."),
        version=forms.IPv4 | forms.IPv6,
        mask=False)
    no_gateway = forms.BooleanField(label=_("Disable Gateway"),
                                    widget=forms.CheckboxInput(attrs={
                                        'class': 'switchable',
                                        'data-slug': 'gateway_ip',
                                        'data-hide-on-checked': 'true'
                                    }),
                                    initial=False,
                                    required=False)

    check_subnet_range = True

    class Meta(object):
        name = _("Subnet")
        help_text = _('Creates a subnet associated with the network.'
                      ' You need to enter a valid "Network Address"'
                      ' and "Gateway IP". If you did not enter the'
                      ' "Gateway IP", the first value of a network'
                      ' will be assigned by default. If you do not want'
                      ' gateway please check the "Disable Gateway" checkbox.'
                      ' Advanced configuration is available by clicking on'
                      ' the "Subnet Details" tab.')

    def __init__(self, request, context, *args, **kwargs):
        super().__init__(request, context, *args, **kwargs)
        if not setting_utils.get_dict_config('OPENSTACK_NEUTRON_NETWORK',
                                             'enable_ipv6'):
            self.fields['ip_version'].widget = forms.HiddenInput()
            self.fields['ip_version'].initial = 4

        try:
            if api.neutron.is_extension_supported(request,
                                                  'subnet_allocation'):
                self.fields['subnetpool'].choices = \
                    self.get_subnetpool_choices(request)
            else:
                self.hide_subnetpool_choices()
        except Exception:
            self.hide_subnetpool_choices()
            msg = _('Unable to initialize subnetpools')
            exceptions.handle(request, msg)
        if len(self.fields['subnetpool'].choices) > 1:
            # Pre-populate prefixlen choices to satisfy Django
            # ChoiceField Validation. This is overridden w/data from
            # subnetpool on select.
            self.fields['prefixlen'].choices = \
                zip(list(range(0, 128 + 1)),
                    list(range(0, 128 + 1)))
            # Populate data-fields for switching the prefixlen field
            # when user selects a subnetpool other than
            # "Provider default pool"
            for (id_, name) in self.fields['subnetpool'].choices:
                if not id_:
                    continue
                key = 'data-subnetpool-' + id_
                self.fields['prefixlen'].widget.attrs[key] = \
                    _('Network Mask')
        else:
            self.hide_subnetpool_choices()

    def get_subnetpool_choices(self, request):
        subnetpool_choices = [('', _('Select a pool'))]

        for subnetpool in api.neutron.subnetpool_list(request):
            subnetpool_choices.append((subnetpool.id, subnetpool))
        return subnetpool_choices

    def hide_subnetpool_choices(self):
        self.fields['address_source'].widget = forms.HiddenInput()
        self.fields['subnetpool'].choices = []
        self.fields['subnetpool'].widget = forms.HiddenInput()
        self.fields['prefixlen'].widget = forms.HiddenInput()

    def _check_subnet_range(self, subnet, allow_cidr):
        allowed_net = netaddr.IPNetwork(allow_cidr)
        return subnet in allowed_net

    def _check_cidr_allowed(self, ip_version, subnet):
        if not self.check_subnet_range:
            return

        allowed_cidr = settings.ALLOWED_PRIVATE_SUBNET_CIDR
        version_str = 'ipv%s' % ip_version
        allowed_ranges = allowed_cidr.get(version_str, [])
        if allowed_ranges:
            under_range = any(self._check_subnet_range(subnet, allowed_range)
                              for allowed_range in allowed_ranges)
            if not under_range:
                range_str = ', '.join(allowed_ranges)
                msg = (_("CIDRs allowed for user private %(ip_ver)s "
                         "networks are %(allowed)s.") %
                       {'ip_ver': '%s' % version_str,
                        'allowed': range_str})
                raise forms.ValidationError(msg)

    def _check_subnet_data(self, cleaned_data):
        cidr = cleaned_data.get('cidr')
        ip_version = int(cleaned_data.get('ip_version'))
        gateway_ip = cleaned_data.get('gateway_ip')
        no_gateway = cleaned_data.get('no_gateway')
        address_source = cleaned_data.get('address_source')
        subnetpool = cleaned_data.get('subnetpool')

        if not subnetpool and address_source == 'subnetpool':
            msg = _('Specify "Address pool" or select '
                    '"Enter Network Address manually" and specify '
                    '"Network Address".')
            raise forms.ValidationError(msg)
        if not cidr and address_source != 'subnetpool':
            msg = _('Specify "Network Address" or '
                    'clear "Create Subnet" checkbox in previous step.')
            raise forms.ValidationError(msg)
        if address_source == 'subnetpool' and 'cidr' in self._errors:
            del self._errors['cidr']
        elif cidr:
            subnet = netaddr.IPNetwork(cidr)
            if subnet.version != ip_version:
                msg = _('Network Address and IP version are inconsistent.')
                raise forms.ValidationError(msg)
            if (ip_version == 4 and subnet.prefixlen == 32) or \
                    (ip_version == 6 and subnet.prefixlen == 128):
                msg = _("The subnet in the Network Address is "
                        "too small (/%s).") % subnet.prefixlen
                self._errors['cidr'] = self.error_class([msg])
            self._check_cidr_allowed(ip_version, subnet)

        if not no_gateway and gateway_ip:
            if netaddr.IPAddress(gateway_ip).version is not ip_version:
                msg = _('Gateway IP and IP version are inconsistent.')
                raise forms.ValidationError(msg)
        if no_gateway and 'gateway_ip' in self._errors:
            del self._errors['gateway_ip']

    def _remove_fields_errors(self):
        self._errors = {}

    def clean(self):
        with_subnet = self.initial.get('with_subnet')
        if not with_subnet:
            self._remove_fields_errors()
            return None
        cleaned_data = super().clean()
        self._check_subnet_data(cleaned_data)
        return cleaned_data


class CreateSubnetInfo(workflows.Step):
    action_class = CreateSubnetInfoAction
    contributes = ("subnet_name", "cidr", "ip_version",
                   "gateway_ip", "no_gateway", "subnetpool",
                   "prefixlen", "address_source")


class CreateSubnetDetailAction(workflows.Action):
    enable_dhcp = forms.BooleanField(label=_("Enable DHCP"),
                                     initial=True, required=False)
    ipv6_modes = forms.ChoiceField(
        label=_("IPv6 Address Configuration Mode"),
        widget=forms.ThemableSelectWidget(attrs={
            'class': 'switched',
            'data-switch-on': 'ipversion',
            'data-ipversion-6': _("IPv6 Address Configuration Mode"),
        }),
        initial=utils.IPV6_DEFAULT_MODE,
        required=False,
        help_text=_("Specifies how IPv6 addresses and additional information "
                    "are configured. We can specify SLAAC/DHCPv6 stateful/"
                    "DHCPv6 stateless provided by OpenStack, "
                    "or specify no option. "
                    "'No options specified' means addresses are configured "
                    "manually or configured by a non-OpenStack system."))
    allocation_pools = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        label=_("Allocation Pools"),
        help_text=_("IP address allocation pools. Each entry is: "
                    "start_ip_address,end_ip_address "
                    "(e.g., 192.168.1.100,192.168.1.120) "
                    "and one entry per line."),
        required=False)
    dns_nameservers = forms.CharField(
        widget=forms.widgets.Textarea(attrs={'rows': 4}),
        label=_("DNS Name Servers"),
        help_text=_("IP address list of DNS name servers for this subnet. "
                    "One entry per line."),
        required=False)
    host_routes = forms.CharField(
        widget=forms.widgets.Textarea(attrs={'rows': 4}),
        label=_("Host Routes"),
        help_text=_("Additional routes announced to the hosts. "
                    "Each entry is: destination_cidr,nexthop "
                    "(e.g., 192.168.200.0/24,10.56.1.254) "
                    "and one entry per line."),
        required=False)

    class Meta(object):
        name = _("Subnet Details")
        help_text = _('Specify additional attributes for the subnet.')

    def __init__(self, request, context, *args, **kwargs):
        super().__init__(request, context, *args, **kwargs)
        if not setting_utils.get_dict_config('OPENSTACK_NEUTRON_NETWORK',
                                             'enable_ipv6'):
            self.fields['ipv6_modes'].widget = forms.HiddenInput()

    def populate_ipv6_modes_choices(self, request, context):
        return [(value, _("%s (Default)") % label)
                if value == utils.IPV6_DEFAULT_MODE
                else (value, label)
                for value, label in utils.IPV6_MODE_CHOICES]

    def _convert_ip_address(self, ip, field_name):
        try:
            return netaddr.IPAddress(ip)
        except (netaddr.AddrFormatError, ValueError):
            msg = (_('%(field_name)s: Invalid IP address (value=%(ip)s)')
                   % {'field_name': field_name, 'ip': ip})
            raise forms.ValidationError(msg)

    def _convert_ip_network(self, network, field_name):
        try:
            return netaddr.IPNetwork(network)
        except (netaddr.AddrFormatError, ValueError):
            msg = (_('%(field_name)s: Invalid IP address (value=%(network)s)')
                   % {'field_name': field_name, 'network': network})
            raise forms.ValidationError(msg)

    def _check_allocation_pools(self, allocation_pools):
        for p in allocation_pools.splitlines():
            p = p.strip()
            if not p:
                continue
            pool = p.split(',')
            if len(pool) != 2:
                msg = _('Start and end addresses must be specified '
                        '(value=%s)') % p
                raise forms.ValidationError(msg)
            start, end = [self._convert_ip_address(ip, "allocation_pools")
                          for ip in pool]
            if start > end:
                msg = _('Start address is larger than end address '
                        '(value=%s)') % p
                raise forms.ValidationError(msg)

    def _check_dns_nameservers(self, dns_nameservers):
        for ns in dns_nameservers.splitlines():
            ns = ns.strip()
            if not ns:
                continue
            self._convert_ip_address(ns, "dns_nameservers")

    def _check_host_routes(self, host_routes):
        for r in host_routes.splitlines():
            r = r.strip()
            if not r:
                continue
            route = r.split(',')
            if len(route) != 2:
                msg = _('Host Routes format error: '
                        'Destination CIDR and nexthop must be specified '
                        '(value=%s)') % r
                raise forms.ValidationError(msg)
            self._convert_ip_network(route[0], "host_routes")
            self._convert_ip_address(route[1], "host_routes")

    def clean(self):
        cleaned_data = super().clean()
        self._check_allocation_pools(cleaned_data.get('allocation_pools'))
        self._check_host_routes(cleaned_data.get('host_routes'))
        self._check_dns_nameservers(cleaned_data.get('dns_nameservers'))
        return cleaned_data


class CreateSubnetDetail(workflows.Step):
    action_class = CreateSubnetDetailAction
    contributes = ("enable_dhcp", "ipv6_modes", "allocation_pools",
                   "dns_nameservers", "host_routes")


class CreateNetwork(workflows.Workflow):
    slug = "create_network"
    name = _("Create Network")
    finalize_button_name = _("Create")
    success_message = _('Created network "%s".')
    failure_message = _('Unable to create network "%s".')
    default_steps = (CreateNetworkInfo,
                     CreateSubnetInfo,
                     CreateSubnetDetail)
    wizard = True

    def get_success_url(self):
        return reverse("horizon:project:networks:index")

    def get_failure_url(self):
        return reverse("horizon:project:networks:index")

    def format_status_message(self, message):
        name = self.context.get('net_name') or self.context.get('net_id', '')
        return message % name

    def _create_network(self, request, data):
        try:
            params = {'name': data['net_name'],
                      'admin_state_up': data['admin_state'],
                      'shared': data['shared']}
            if 'az_hints' in data and data['az_hints']:
                params['availability_zone_hints'] = data['az_hints']
            if data['mtu']:
                params['mtu'] = data['mtu']
            network = api.neutron.network_create(request, **params)
            self.context['net_id'] = network.id
            LOG.debug('Network "%s" was successfully created.',
                      network.name_or_id)
            return network
        except Exception as e:
            LOG.info('Failed to create network: %s', e)
            msg = _('Failed to create network "%s".') % data['net_name']
            redirect = self.get_failure_url()
            exceptions.handle(request, msg, redirect=redirect)
            return False

    def _setup_subnet_parameters(self, params, data, is_create=True):
        """Setup subnet parameters

        This methods setups subnet parameters which are available
        in both create and update.
        """
        is_update = not is_create
        params['enable_dhcp'] = data['enable_dhcp']
        if int(data['ip_version']) == 6:
            ipv6_modes = utils.get_ipv6_modes_attrs_from_menu(
                data['ipv6_modes'])
            if ipv6_modes[0] and is_create:
                params['ipv6_ra_mode'] = ipv6_modes[0]
            if ipv6_modes[1] and is_create:
                params['ipv6_address_mode'] = ipv6_modes[1]
        if data['allocation_pools']:
            pools = [dict(zip(['start', 'end'], pool.strip().split(',')))
                     for pool in data['allocation_pools'].splitlines()
                     if pool.strip()]
            params['allocation_pools'] = pools
        if data['host_routes'] or is_update:
            routes = [dict(zip(['destination', 'nexthop'],
                               route.strip().split(',')))
                      for route in data['host_routes'].splitlines()
                      if route.strip()]
            params['host_routes'] = routes
        if data['dns_nameservers'] or is_update:
            nameservers = [ns.strip()
                           for ns in data['dns_nameservers'].splitlines()
                           if ns.strip()]
            params['dns_nameservers'] = nameservers

    def _create_subnet(self, request, data, network=None, tenant_id=None,
                       no_redirect=False):
        if network:
            network_id = network.id
            network_name = network.name
        else:
            network_id = self.context.get('network_id')
            network_name = self.context.get('network_name')
        try:
            params = {'network_id': network_id,
                      'name': data['subnet_name']}
            if 'cidr' in data and data['cidr']:
                params['cidr'] = data['cidr']
            if 'ip_version' in data and data['ip_version']:
                params['ip_version'] = int(data['ip_version'])
            if tenant_id:
                params['tenant_id'] = tenant_id
            if data['no_gateway']:
                params['gateway_ip'] = None
            elif data['gateway_ip']:
                params['gateway_ip'] = data['gateway_ip']
            if 'subnetpool' in data and data['subnetpool']:
                params['subnetpool_id'] = data['subnetpool']
                if 'prefixlen' in data and data['prefixlen']:
                    params['prefixlen'] = data['prefixlen']

            self._setup_subnet_parameters(params, data)

            subnet = api.neutron.subnet_create(request, **params)
            self.context['subnet_id'] = subnet.id
            LOG.debug('Subnet "%s" was successfully created.', data['cidr'])
            return subnet
        except Exception:
            if network_name:
                msg = _('Failed to create subnet "%(sub)s" for network '
                        '"%(net)s".')
            else:
                msg = _('Failed to create subnet "%(sub)s".')
            if no_redirect:
                redirect = None
            else:
                redirect = self.get_failure_url()
            exceptions.handle(request,
                              msg % {"sub": data['cidr'], "net": network_name},
                              redirect=redirect)
            return False

    def _delete_network(self, request, network):
        """Delete the created network when subnet creation failed."""
        try:
            api.neutron.network_delete(request, network.id)
            LOG.debug('Delete the created network %s '
                      'due to subnet creation failure.', network.id)
            msg = _('Delete the created network "%s" '
                    'due to subnet creation failure.') % network.name
            redirect = self.get_failure_url()
            messages.info(request, msg)
            raise exceptions.Http302(redirect)
        except Exception as e:
            LOG.info('Failed to delete network %(id)s: %(exc)s',
                     {'id': network.id, 'exc': e})
            msg = _('Failed to delete network "%s"') % network.name
            redirect = self.get_failure_url()
            exceptions.handle(request, msg, redirect=redirect)

    def handle(self, request, data):
        network = self._create_network(request, data)
        if not network:
            return False
        # If we do not need to create a subnet, return here.
        if not data['with_subnet']:
            return True
        subnet = self._create_subnet(request, data, network, no_redirect=True,
                                     tenant_id=network.tenant_id)
        if subnet:
            return True

        self._delete_network(request, network)
        return False
