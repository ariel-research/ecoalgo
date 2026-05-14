import re
from locust import HttpUser, task, between


def extract_csrf(html):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    return match.group(1) if match else ""


class LoginUser(HttpUser):
    """
    Simulates the login/logout cycle.
    Run with:  locust -f tests/locustfile.py --host=http://localhost:5032
    """
    wait_time = between(1, 3)

    @task
    def login_then_logout(self):
        # Fetch the login page to get a fresh CSRF token
        response = self.client.get("/login", name="/login [GET]")
        csrf_token = extract_csrf(response.text)

        # Submit credentials
        self.client.post(
            "/login",
            name="/login [POST]",
            data={
                "email": "admin@example.com",
                "password": "adminpassword",
                "csrf_token": csrf_token,
                "remember": "false",
            },
            allow_redirects=True,
        )

        # Log out so the next iteration starts clean
        self.client.get("/logout", name="/logout")
