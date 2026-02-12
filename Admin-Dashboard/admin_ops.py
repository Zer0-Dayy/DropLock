from admin_init import init_firebase
from firebase_admin import db


def get_profile(uid: str):
	init_firebase()
	ref = f"profiles/{uid}"
	data = db.reference(ref).get()
	if data is None:
    		print("No profile at:", ref)
	return data

def assert_can_admin(uid: str, sector_id: str | None = None):
	p = get_profile(uid)
	if not p:
		raise PermissionError("Invalid Admin UID!")
	if (p["status"] != "active"):
		raise PermissionError("inactive Profile!")
	role= p.get("role")
	if (role == "superAdmin"):
		return p
	if (role == "admin"):
		if sector_id is None:
			raise permissionError("Sector ID is required for admins!")
		if (p["sectorId"] != sector_id):
			raise permissionError("Admin not assigned to this sector!")
		return p
	raise PermissionError("Not an admin!")
def assert_super_admin(uid: str) -> dict:
    p = get_profile(uid)
    if not p:
        raise PermissionError("No profile")
    if p.get("status") != "active":
        raise PermissionError("Profile disabled")
    if p.get("role") != "superAdmin":
        raise PermissionError("SuperAdmin required")
    return p
