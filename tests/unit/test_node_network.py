"""Mac LAN addresses must advertise the actual selected connection port."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ascendant.distributed.network import connection_urls, connection_addresses, thunderbolt_address


class NodeNetworkTests(unittest.TestCase):
    def test_mac_interface_ip_survives_failed_hostname_resolution(self):
        with patch('ascendant.distributed.network.sys.platform', 'darwin'), \
             patch('ascendant.distributed.network.socket.getaddrinfo', side_effect=OSError), \
             patch('ascendant.distributed.network.socket.gethostname', return_value='Main.local'), \
             patch('ascendant.distributed.network.subprocess.run', return_value=SimpleNamespace(stdout='lo0:\n inet 127.0.0.1\nen0:\n inet 192.168.3.149\nen1:\n inet 169.254.1.1\n')):
            urls = connection_urls(7988)
        self.assertEqual(urls[0], 'http://192.168.3.149:7988')
        self.assertNotIn('http://169.254.1.1:7988', urls)
        self.assertIn('http://127.0.0.1:7988', urls)

    def test_loopback_listener_does_not_advertise_remote_connections(self):
        self.assertEqual(connection_urls(7988, '127.0.0.1'), ['http://127.0.0.1:7988'])

    def test_active_thunderbolt_self_assigned_ip_is_first_and_labeled(self):
        hardware = 'Hardware Port: Thunderbolt Bridge\nDevice: bridge0\n\nHardware Port: Wi-Fi\nDevice: en0\n'
        interfaces = ('lo0:\n inet 127.0.0.1\nbridge0:\n inet 169.254.57.204\n status: active\n'
                      'en0:\n inet 192.168.3.150\n status: active\nbridge1:\n inet 169.254.1.5\n status: inactive\n')
        with patch('ascendant.distributed.network.sys.platform', 'darwin'), \
             patch('ascendant.distributed.network.socket.getaddrinfo', side_effect=OSError), \
             patch('ascendant.distributed.network.socket.gethostname', return_value='Main.local'), \
             patch('ascendant.distributed.network.subprocess.run', side_effect=lambda cmd, **kwargs:
                   SimpleNamespace(stdout=hardware if 'networksetup' in cmd[0] else interfaces)):
            addresses = connection_addresses(8010)
        self.assertEqual(addresses[0], {'url': 'http://169.254.57.204:8010', 'interface': 'bridge0',
                                       'kind': 'thunderbolt', 'label': 'Thunderbolt Bridge'})
        self.assertEqual(addresses[1]['label'], 'Wi-Fi')
        self.assertNotIn('http://169.254.1.5:8010', [entry['url'] for entry in addresses])

    def test_bridge_fallback_and_specific_listener_binding(self):
        with patch('ascendant.distributed.network.sys.platform', 'darwin'), \
             patch('ascendant.distributed.network.socket.getaddrinfo', return_value=[]), \
             patch('ascendant.distributed.network.subprocess.run', side_effect=lambda cmd, **kwargs:
                   SimpleNamespace(stdout='' if 'networksetup' in cmd[0] else
                       'bridge0:\n inet 169.254.57.204\n status: active\nen0:\n inet 192.168.3.150\n status: active\n')):
            addresses = connection_addresses(8010, '169.254.57.204')
        self.assertEqual(addresses, [{'url': 'http://169.254.57.204:8010', 'interface': 'bridge0',
                                     'kind': 'direct', 'label': 'Direct bridge'}])

    def test_thunderbolt_binding_has_no_wifi_fallback(self):
        with patch('ascendant.distributed.network.connection_addresses', return_value=[
                {'url': 'http://192.168.3.150:0', 'kind': 'wifi'}]):
            with self.assertRaisesRegex(ValueError, 'No active Thunderbolt Bridge'):
                thunderbolt_address()
        with patch('ascendant.distributed.network.connection_addresses', return_value=[
                {'url': 'http://169.254.57.204:0', 'kind': 'thunderbolt'}]):
            self.assertEqual(thunderbolt_address(), '169.254.57.204')
