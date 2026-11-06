"""Index-service transport: paid services always verify certificates (P3)."""

from __future__ import annotations

import ssl
import sys
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
