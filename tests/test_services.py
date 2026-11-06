"""Index-service transport: paid services always verify certificates (P3)."""

from __future__ import annotations

import ssl
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import services  # noqa: E402
from gsa_seo_indexer.httpclient import tls_context  # noqa: E402
from gsa_seo_indexer.services import IndexService  # noqa: E402


class _Reply:
    status = 200

    def read(self, _limit=None):
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _service(**kwargs) -> IndexService:
    base = dict(
        name="Test",
        path=Path("test.ini"),
        url="https://service.example/index",
        post_data="urls=%arg1%",
        ok_reply="ok",
    )
    base.update(kwargs)
    return IndexService(**base)


class ServiceTlsTest(unittest.TestCase):
    def test_submission_pins_a_verifying_context(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None, context=None):
            captured["context"] = context
            captured["timeout"] = timeout
            return _Reply()

        svc = _service()
        with mock.patch.object(services.urllib.request, "urlopen", fake_urlopen):
            ok, message = svc.submit(["https://target.example/"])

        self.assertTrue(ok, message)
        self.assertIs(captured["context"], tls_context(True))
        self.assertTrue(captured["context"].check_hostname)
        self.assertEqual(captured["context"].verify_mode, ssl.CERT_REQUIRED)

    def test_submission_sends_the_api_key_header(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None, context=None):
            captured["headers"] = dict(req.header_items())
            return _Reply()

        svc = _service(
            header="Authorization: Token %api-key%",
            variables={"api-key": {"default": "secret"}},
        )
        with mock.patch.object(services.urllib.request, "urlopen", fake_urlopen):
            ok, _message = svc.submit(["https://target.example/"])

        self.assertTrue(ok)
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["authorization"], "Token secret")


if __name__ == "__main__":
    unittest.main()


class _FakeService:
    """Minimal stand-in: ``submit_many`` only needs a name and ``submit``."""

    def __init__(self, name: str, delay: float = 0.0, boom: bool = False) -> None:
        self.name = name
        self.delay = delay
        self.boom = boom
        self.calls = 0

    def submit(self, urls, args=None, timeout=20.0):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("service exploded")
        return True, f"HTTP 200 ({self.name})"


class SubmitManyTest(unittest.TestCase):
    """Services are independent endpoints, so they are submitted in parallel."""

    def test_parallel_is_much_faster_than_sequential(self) -> None:
        batch = [_FakeService(f"s{i}", delay=0.10) for i in range(8)]
        started = time.perf_counter()
        results = services.submit_many(batch, ["http://example.com/"], workers=8)
        parallel = time.perf_counter() - started

        started = time.perf_counter()
        services.submit_many(batch, ["http://example.com/"], workers=1)
        sequential = time.perf_counter() - started

        self.assertEqual(len(results), 8)
        self.assertTrue(all(ok for _svc, ok, _msg in results))
        self.assertLess(parallel, 0.40, "8 x 0.10 s harus tumpang tindih")
        self.assertGreater(sequential, parallel * 2.5)

    def test_results_keep_the_service_order(self) -> None:
        batch = [_FakeService(f"s{i}", delay=0.05 * (8 - i)) for i in range(8)]
        results = services.submit_many(batch, ["http://example.com/"], workers=8)
        self.assertEqual([svc.name for svc, _ok, _msg in results], [s.name for s in batch])

    def test_one_broken_service_does_not_stop_the_rest(self) -> None:
        batch = [_FakeService("a"), _FakeService("b", boom=True), _FakeService("c")]
        results = services.submit_many(batch, ["http://example.com/"], workers=4)
        self.assertEqual([ok for _svc, ok, _msg in results], [True, False, True])
        self.assertIn("exploded", results[1][2])

    def test_stop_skips_the_remaining_queue(self) -> None:
        batch = [_FakeService(f"s{i}", delay=0.02) for i in range(4)]
        results = services.submit_many(
            batch, ["http://example.com/"], workers=1, should_stop=lambda: True
        )
        self.assertEqual(results, [])
        self.assertEqual(sum(svc.calls for svc in batch), 0)

    def test_progress_callback_fires_for_every_service(self) -> None:
        batch = [_FakeService(f"s{i}", delay=0.02) for i in range(3)]
        seen: list[str] = []
        services.submit_many(
            batch,
            ["http://example.com/"],
            workers=3,
            on_result=lambda svc, _ok, _msg: seen.append(svc.name),
        )
        self.assertEqual(sorted(seen), ["s0", "s1", "s2"])


class SubmitManyAgainstEmulatorTest(unittest.TestCase):
    """End-to-end: real HTTP, three services, one parallel call."""

    def test_three_services_are_submitted_over_http(self) -> None:
        from gsa_seo_indexer import emulator

        config = emulator.EmulatorConfig(port=0)
        server = emulator.IndexServiceEmulator(config)
        _host, port = server.start()
        try:
            batch = [
                _service(name=f"Svc{i}", url=f"http://127.0.0.1:{port}/svc{i}?url=%arg1%")
                for i in range(3)
            ]
            config.services = batch
            results = services.submit_many(batch, ["http://example.com/"], workers=3)
        finally:
            server.stop()

        self.assertEqual([svc.name for svc, _ok, _msg in results], ["Svc0", "Svc1", "Svc2"])
        for _svc, ok, message in results:
            self.assertTrue(ok, message)
