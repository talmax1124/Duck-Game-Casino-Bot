import secrets

def get_secure_hazard():
    try:
        hazard = secrets.randbelow(5)  # 0 to 4 inclusive
        print(f"[DEBUG] Secure hazard generated (Python): {hazard}")
        return hazard
    except Exception as e:
        print(f"[ERROR] Failed to generate secure hazard: {e}")
        return 0  # Safe fallback