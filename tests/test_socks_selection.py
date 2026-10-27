"""Offline handshake, protocol dispatch and conservative selector tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from system_proxy.socks import establish, recv_exact
from system_proxy.verification import Check, check_one, verify_candidates
from system_proxy.history import History
from system_proxy.destination import DestinationHealth, Observation
from system_proxy.select import select


def now():
    return datetime.now(timezone.utc).isoformat()


class FakeSocket:
    def __init__(self, chunks):
        self.buffer = bytearray(b"".join(chunks))
        self.sent = []
    def sendall(self, data):
        self.sent.append(data)
    def recv(self, n):
        result = bytes(self.buffer[:n])
        del self.buffer[:n]
        return result


class HandshakeTests(unittest.TestCase):
    def test_socks5_no_auth_and_domain_connect(self):
        s=FakeSocket([b"\x05\x00",b"\x05\x00\x00\x01",b"\x7f\x00\x00\x01\x01\xbb"])
        establish(s,"socks5")
        self.assertEqual(s.sent[0],b"\x05\x01\x00")
        self.assertTrue(s.sent[1].startswith(b"\x05\x01\x00\x03\x0bexample.com"))
        self.assertTrue(s.sent[1].endswith(b"\x01\xbb"))

    def test_socks4a_connect(self):
        s=FakeSocket([b"\x00\x5a\x00\x00\x00\x00\x00\x00"])
        establish(s,"socks4")
        self.assertTrue(s.sent[0].startswith(b"\x04\x01\x01\xbb\x00\x00\x00\x01\x00"))
        self.assertTrue(s.sent[0].endswith(b"example.com\x00"))

    def test_rejections_and_short_reads(self):
        for protocol,data in (("socks5",b"\x05\xff"),("socks4",b"\x00\x5b\x00\x00\x00\x00\x00\x00")):
            with self.subTest(protocol=protocol),self.assertRaises(ConnectionError):
                establish(FakeSocket([data]),protocol)
        with self.assertRaises(ConnectionError):
            recv_exact(FakeSocket([b"\x05"]),2)

    def test_protocol_dispatch_without_network(self):
        called=[]
        def mock_socks(ip,port,timeout,protocol):
            called.append(protocol)
            return True,"verified"
        result=check_one("8.8.8.8",1080,"socks5",1,socks_probe=mock_socks)
        self.assertEqual(result.status,"passed")
        self.assertEqual(called,["socks5"])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=History(Path(self.tmp.name)/"db.sqlite3")
        self.db.import_candidates([
            {"ip":"8.8.8.8","port":1080,"protocols":["socks5"],"sources":["hproxy"]},
            {"ip":"1.1.1.1","port":1080,"protocols":["socks5"],"sources":["geonode"]},
        ])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_fail_closed_until_recent_global_success(self):
        self.assertIsNone(select(self.db,protocol="socks5")["selected"])
        self.db.add_checks([Check("8.8.8.8",1080,"socks5","passed",30,"ok",now())])
        self.assertEqual(select(self.db,protocol="socks5")["selected"]["ip"],"8.8.8.8")
        self.assertIsNone(select(self.db,protocol="socks4")["selected"])
        self.assertIsNone(select(self.db,protocol="socks5",min_score=100)["selected"])

    def test_domain_requires_independent_success(self):
        self.db.add_checks([
            Check("8.8.8.8",1080,"socks5","passed",30,"ok",now()),
            Check("1.1.1.1",1080,"socks5","passed",40,"ok",now()),
        ])
        self.assertIsNone(select(self.db,protocol="socks5",domain="example.com")["selected"])
        observations=DestinationHealth(self.db)
        observations.record(Observation("8.8.8.8",1080,"socks5","example.com",
                                        "target_denied",now(),403))
        observations.record(Observation("1.1.1.1",1080,"socks5","example.com",
                                        "success",now(),200))
        self.assertEqual(select(self.db,protocol="socks5",domain="example.com")["selected"]["ip"],
                         "1.1.1.1")
        self.assertIsNone(select(self.db,protocol="socks5",domain="elsewhere.com")["selected"])
        self.db.add_checks([Check("1.1.1.1",1080,"socks5","failed",None,"timeout",now())])
        self.assertIsNone(select(self.db,protocol="socks5",domain="example.com")["selected"])

    def test_freshness_expired(self):
        old=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        self.db.add_checks([Check("8.8.8.8",1080,"socks5","passed",30,"ok",old)])
        self.assertIsNone(select(self.db,protocol="socks5")["selected"])


if __name__ == "__main__":
    unittest.main()
