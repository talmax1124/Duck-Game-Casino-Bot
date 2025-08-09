import json
import os
import threading
from typing import Tuple

BANK_FILE = "data/bank.json"
_DEFAULT_BAL = 1000.0

# Thread-safe lock for concurrent access from multiple commands
_LOCK = threading.RLock()


def _ensure_dir_and_file():
    os.makedirs(os.path.dirname(BANK_FILE) or ".", exist_ok=True)
    if not os.path.exists(BANK_FILE):
        with open(BANK_FILE, "w") as f:
            json.dump({}, f)


def _atomic_write_json(path: str, data: dict):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp_path, path)  # atomic on POSIX


def load_bank() -> dict:
    _ensure_dir_and_file()
    with _LOCK:
        try:
            with open(BANK_FILE, "r") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {}
                return data
        except Exception:
            # If the file is corrupt/empty, fall back to empty structure
            return {}


def save_bank(bank: dict) -> None:
    _ensure_dir_and_file()
    with _LOCK:
        _atomic_write_json(BANK_FILE, bank)


def get_balance(user_id) -> float:
    """Return the simple balance for a user (legacy single-balance schema)."""
    uid = str(user_id)
    with _LOCK:
        bank = load_bank()
        return float(bank.get(uid, _DEFAULT_BAL))


def set_balance(user_id, new_amount: float) -> None:
    """Set (overwrite) a user's balance. Never writes a negative value below 0."""
    uid = str(user_id)
    if new_amount < 0:
        new_amount = 0.0
    with _LOCK:
        bank = load_bank()
        bank[uid] = float(new_amount)
        save_bank(bank)


def update_balance(user_id, amount: float, *, allow_negative: bool = False, floor: float = 0.0) -> Tuple[bool, float]:
    """
    Increment a user's balance by `amount`.

    Returns (ok, new_balance).
      - If `allow_negative` is False and the operation would drop the balance below `floor`,
        the update is aborted and (False, current_balance) is returned.
      - Otherwise, updates the stored balance atomically and returns (True, new_balance).
    """
    uid = str(user_id)
    with _LOCK:
        bank = load_bank()
        current = float(bank.get(uid, _DEFAULT_BAL))
        new_val = current + float(amount)
        if not allow_negative and new_val < float(floor):
            return False, current
        bank[uid] = new_val
        save_bank(bank)
        return True, new_val