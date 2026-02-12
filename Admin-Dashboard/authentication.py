import os
import requests

AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"

def sign_in(email: str, password: str) -> dict:
	api_key = os.getenv("FIREBASE_WEB_API_KEY")
	if not api_key:
		raise RuntimeError("Missing FIREBASE_WEB_API_KEY environment variable.")
	payload = {
		"email": email,
		"password": password,
		"returnSecureToken": True,
	}

	r = requests.post(f"{AUTH_URL}?key={api_key}", json=payload, timeout=15)
	ct = r.headers.get("content-type", "")
	print("HTTP:", r.status_code, "CT:", ct)
	if "application/json" in ct:
		data = r.json()
	else:
		print("Body prefix:", r.text[:200])
		raise RuntimeError("Non-JSON Response Received")
	if r.status_code != 200:
		msg = data.get("error", {}).get("message", f"HTTP {r.status_code}")
		raise RuntimeError(f"Firebase sign-in failed: {msg}")

	return data
