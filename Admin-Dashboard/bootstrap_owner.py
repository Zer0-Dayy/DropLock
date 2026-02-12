from authentication import sign_in
from admin_init import verify_id_token, init_firebase
from firebase_admin import db
import time


email = input("Email:").strip()
password = input("Password:").strip()


data = sign_in(email, password)
id_token = data["idToken"]
uid_client = data["localId"]

claims = verify_id_token(id_token)
uid = claims["uid"]


print("uid_client:", uid_client)
print("uid_verified:", uid)


now_ms = int(time.time() * 1000)
init_firebase()
profile_ref = db.reference(f"profiles/{uid}")
profile_ref.set({
	"role": "superAdmin",
	"email": email,
	"displayName": "DropLock Owner",
	"sectorId": None,
	"status": "active",
	"createdAt": now_ms
})
print("Welcome New Owner")
