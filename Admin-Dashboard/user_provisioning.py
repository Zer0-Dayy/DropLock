from admin_init import init_firebase
from firebase_admin import auth, db
import time

def create_auth_user(email: str, temp_password: str) -> str:
	init_firebase()
	user_record = auth.create_user(email=email, password=temp_password)
	return user_record.uid

def provision_device(email: str, temp_password: str, sector_id: str, display_name: str) -> str:
	init_firebase()
	uid = create_auth_user(email, temp_password)
	now_ms = int(time.time() * 1000)
	profile_ref = db.reference(f"profiles/{uid}")
	profile_ref.set({
		"role": "device",
		"email": email,
		"displayName": display_name,
		"sectorId": sector_id,
		"status": "active",
		"createdAt": now_ms
	})
	db.reference(f"sectors/{sector_id}/deviceUids/{uid}").set(True)
	return uid
def provision_admin(email: str, temp_password: str, sector_id: str, display_name: str) -> str:
    init_firebase()
    uid = create_auth_user(email, temp_password)
    now_ms = int(time.time() * 1000)

    # Profile
    db.reference(f"profiles/{uid}").set({
        "role": "admin",
        "email": email,
        "displayName": display_name,
        "sectorId": sector_id,
        "status": "active",
        "createdAt": now_ms,
    })

    # Sector membership cross-check
    db.reference(f"sectors/{sector_id}/adminUids/{uid}").set(True)

    return uid
