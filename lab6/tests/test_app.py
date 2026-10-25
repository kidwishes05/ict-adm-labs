import unittest
from unittest.mock import patch

import psycopg

from app import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_index(self):
        with patch.dict(
            "os.environ",
            {
                "WRITE_HOST": "primary",
                "READ_HOST": "replica",
            },
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

    def test_empty_note(self):
        response = self.client.post(
            "/api/notes",
            json={"body": "   "},
        )

        self.assertEqual(response.status_code, 400)

    def test_load_limit(self):
        response = self.client.post(
            "/api/load",
            json={"count": 2001},
        )

        self.assertEqual(response.status_code, 400)

    def test_database_unavailable(self):
        with patch.dict(
            "os.environ",
            {"READ_HOST": "replica"},
        ):
            with patch(
                "app.connect",
                side_effect=psycopg.OperationalError(
                    "test outage"
                ),
            ):
                response = self.client.get("/api/notes")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()