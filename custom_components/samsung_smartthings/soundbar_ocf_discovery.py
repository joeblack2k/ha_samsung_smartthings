"""One bounded, known-host OCF multicast discovery; no credentials or writes.

Protocol shapes reviewed against QuiteYellow/SmartThings-Local commit
7f96fdfbc89c63fb188bb378526018637221c74d, protocol/ocf_multicast.py.
Only correlates response ports; no DTLS session or ownership transfer.
"""
import ipaddress
import secrets
import socket
import struct
import time


def option(number, value, previous):
    def extended(n):
        if n < 13:
            return n, b''
        if n < 269:
            return 13, bytes([n - 13])
        return 14, struct.pack('!H', n - 269)
    delta, d = extended(number - previous)
    length, v = extended(len(value))
    return bytes([(delta << 4) | length]) + d + v + value


def request(token, mid, versioned=False, filtered=False):
    options = [(11, b'oic'), (11, b'res')]
    if filtered:
        options.append((15, b'rt=oic.r.doxm'))
    options.append((17, b'\x27\x10' if versioned else b'\x3c'))
    if versioned:
        options.append((2049, b'\x08\x00'))
    packet = bytes([0x50 | len(token), 1]) + struct.pack('!H', mid) + token
    previous = 0
    for number, value in options:
        packet += option(number, value, previous)
        previous = number
    return packet


def matched_reply(data, source, target, tokens):
    """Accept only complete bounded CoAP Content responses for this host/token."""
    if source[0] != target or not 1 <= source[1] <= 65535 or len(data) > 8192:
        return None
    if len(data) < 4 or data[0] >> 6 != 1:
        return None
    kind, tkl = (data[0] >> 4) & 3, data[0] & 15
    if kind not in (0, 1) or tkl > 8 or len(data) < 4 + tkl or data[1] != 0x45:
        return None
    token = data[4:4+tkl]
    if token not in tokens:
        return None
    pos = 4 + tkl
    try:
        while pos < len(data) and data[pos] != 255:
            head = data[pos]
            pos += 1
            lengths = []
            for nibble in (head >> 4, head & 15):
                if nibble == 15:
                    return None
                if nibble == 13:
                    lengths.append(13 + data[pos]); pos += 1
                elif nibble == 14:
                    lengths.append(269 + struct.unpack_from('!H', data, pos)[0]); pos += 2
                else:
                    lengths.append(nibble)
            pos += lengths[1]
            if pos > len(data):
                return None
        if pos >= len(data) - 1 or data[pos] != 255:
            return None
    except (IndexError, struct.error):
        return None
    return kind, struct.unpack_from('!H', data, 2)[0], source[1]


def discover(target, interface=None, timeout=3):
    address = ipaddress.IPv4Address(target)
    if not address.is_private or address.is_multicast or address.is_unspecified or address.is_loopback:
        raise ValueError('Private unicast target required')
    target = str(address)
    if not 0 < timeout <= 5:
        raise ValueError('Timeout must be at most five seconds')
    if interface is None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            # UDP connect resolves the route; no datagram is sent here.
            route.connect((target, 5683))
            interface = route.getsockname()[0]
    tokens, ports = set(), set()
    sent = received = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((interface, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        deadline = time.monotonic() + timeout
        for modern, filtered in ((True, False), (False, False), (False, True)):
            token = secrets.token_bytes(8)
            tokens.add(token)
            sock.sendto(request(token, secrets.randbits(16), modern, filtered), ('224.0.1.187', 5683))
            sent += 1
        # This receives only replies to our socket, not a network capture.
        for _ in range(64):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, source = sock.recvfrom(8193)
            except socket.timeout:
                break
            match = matched_reply(data, source, target, tokens)
            if match is None:
                continue
            kind, mid, port = match
            if kind == 0:
                sock.sendto(b'\x60\x00' + struct.pack('!H', mid), source)
            ports.add(port)
            received += 1
    return {'target': target, 'interface': interface, 'plaintext_ports': sorted(ports),
            'requests_sent': sent, 'matched_responses': received,
            'status': 'candidate_found' if ports else 'no_response',
            'authenticated': False}



