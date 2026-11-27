"""Saved public-address settings. These tests do not open a tunnel."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from public_tunnel import QUICK_TUNNEL_RE, StableTunnel, load_stable_tunnel, save_stable_tunnel


class QuickTunnelTests(unittest.TestCase):
    def test_ignores_the_cloudflare_api_host(self) -> None:
        self.assertIsNone(QUICK_TUNNEL_RE.search("see https://api.trycloudflare.com/"))
        match = QUICK_TUNNEL_RE.search("https://flash-smart-instead-barrel.trycloudflare.com")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), "https://flash-smart-instead-barrel.trycloudflare.com")


class StableTunnelTests(unittest.TestCase):
    def test_missing_files_mean_a_temporary_address(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"EDUBRIDGE_PUBLIC_URL": "", "EDUBRIDGE_TUNNEL_TOKEN": ""}):
                self.assertIsNone(load_stable_tunnel(Path(folder)))

    def test_token_and_url_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "public_url.txt").write_text("https://school.example.com\n", encoding="utf-8")
            (root / "tunnel.token").write_text("a" * 24, encoding="utf-8")
            with mock.patch.dict(os.environ, {"EDUBRIDGE_PUBLIC_URL": "", "EDUBRIDGE_TUNNEL_TOKEN": ""}):
                loaded = load_stable_tunnel(root)
            self.assertEqual(loaded, StableTunnel("https://school.example.com", "a" * 24))

    def test_token_without_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tunnel.token").write_text("a" * 24, encoding="utf-8")
            with mock.patch.dict(os.environ, {"EDUBRIDGE_PUBLIC_URL": "", "EDUBRIDGE_TUNNEL_TOKEN": ""}):
                with self.assertRaises(ValueError):
                    load_stable_tunnel(root)

    def test_save_then_load(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch("public_tunnel._project_dir", return_value=root):
                save_stable_tunnel(root, "https://school.example.com", "d" * 24)
            self.assertTrue((root / "public_url.txt").is_file())
            self.assertTrue((root / "tunnel.token").is_file())
            with mock.patch.dict(os.environ, {"EDUBRIDGE_PUBLIC_URL": "", "EDUBRIDGE_TUNNEL_TOKEN": ""}):
                loaded = load_stable_tunnel(root)
            self.assertEqual(loaded, StableTunnel("https://school.example.com", "d" * 24))

    def test_environment_overrides_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "public_url.txt").write_text("https://file.example.com", encoding="utf-8")
            (root / "tunnel.token").write_text("b" * 24, encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"EDUBRIDGE_PUBLIC_URL": "https://env.example.com", "EDUBRIDGE_TUNNEL_TOKEN": "c" * 24},
            ):
                loaded = load_stable_tunnel(root)
            self.assertEqual(loaded, StableTunnel("https://env.example.com", "c" * 24))


if __name__ == "__main__":
    unittest.main()
