from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PAGES = ("index.html", "acerca.html", "servicios.html", "equipo.html", "contacto.html")


class EmployeeAccessTests(unittest.TestCase):
    def test_worker_access_is_nested_below_team_on_every_public_page(self) -> None:
        for filename in PUBLIC_PAGES:
            with self.subTest(filename=filename):
                html = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn('class="worker-submenu"', html)
                self.assertEqual(html.count('href="/trabajadores"'), 1)

        apache_rules = (ROOT / ".htaccess").read_text(encoding="utf-8")
        self.assertIn("^trabajadores(?:\\.html)?/?$ https://", apache_rules)
        self.assertIn("[R=302,L,NE]", apache_rules)

    def test_worker_page_has_secure_direct_login_contract(self) -> None:
        html = (ROOT / "trabajadores.html").read_text(encoding="utf-8")
        self.assertIn('name="username"', html)
        self.assertIn('autocomplete="username"', html)
        self.assertIn('name="password" type="password"', html)
        self.assertIn('autocomplete="current-password"', html)
        self.assertIn("/api/auth/login", html)
        self.assertIn("credentials: 'include'", html)
        self.assertIn("No existe restablecimiento automático", html)
        self.assertNotIn("localStorage.setItem('password'", html)
        self.assertNotIn("localStorage.setItem(\"password\"", html)

    def test_worker_menu_and_form_styles_exist(self) -> None:
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".sidebar nav ul.worker-submenu", css)
        self.assertIn(".employee-login__form", css)
        self.assertIn(".employee-login__field input", css)


if __name__ == "__main__":
    unittest.main()
