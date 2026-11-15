"""Test package setup: no test reaches the network unless it opts in.

Loopback stays allowed (dashboard tests serve on 127.0.0.1). A deliberate live check sets COFINANCE_NETWORK_TESTS=1.
Run the suite as `python -m unittest discover -s tests -t .` so this package file is imported.
"""
import os
import socket

LOOPBACK = {'127.0.0.1', '::1', 'localhost', ''}


class NetworkBlocked(RuntimeError):
    pass


def _local(host):
    host = str(host or '').strip('[]').lower()
    return host in LOOPBACK or host.startswith('127.')


def _blocked(host):
    return NetworkBlocked(f'A test tried to reach {host}. Pass a fake fetcher instead, or opt in with '
                          'COFINANCE_NETWORK_TESTS=1 for a deliberate live check.')


def install():
    if getattr(socket, '_cofinance_guard', False):
        return
    real_getaddrinfo, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if not _local(host.decode() if isinstance(host, bytes) else host):
            raise _blocked(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def check(address):
        if isinstance(address, tuple) and address and not _local(address[0]):
            raise _blocked(address[0])

    def connect(self, address):
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = getaddrinfo, connect, connect_ex
    socket._cofinance_guard = True


if os.environ.get('COFINANCE_NETWORK_TESTS') != '1':
    install()
