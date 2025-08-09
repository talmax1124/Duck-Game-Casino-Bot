# RESTORED JSON-BACKED BANK WITH PERSISTENT WINS/LOSSES + COOLDOWNS
import json
import os
import threading
import time
from typing import Dict, Tuple, Optional

BANK_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "bank.json"))

# Default per-user record
_DEFAULT_USER: Dict[str, float | bool | int] = {
    "wallet": 1000.0,
    "bank": 0.0,
    "game_active": False,
    "last_earn_ts": 0.0,
    "last_rob_ts": 0.0,
    "wins": 0,
    "losses": 0,
}

# Thread-safe lock for concurrent access
_LOCK = threading.RLock()


# ----------------------------- low-level I/O ---------------------------------

def _ensure_dir_and_file():
    os.makedirs(os.path.dirname(BANK_FILE) or ".", exist_ok=True)
    if not os.path.exists(BANK_FILE):
        with open(BANK_FILE, "w") as f:
            json.dump({}, f)


def _atomic_write_json(path: str, data: dict):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp_path, path)


def load_bank() -> dict:
    _ensure_dir_and_file()
    with _LOCK:
        try:
            with open(BANK_FILE, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save_bank(bank: dict) -> None:
    _ensure_dir_and_file()
    with _LOCK:
        _atomic_write_json(BANK_FILE, bank)


# ----------------------------- schema helpers --------------------------------

def _ensure_user(bank: dict, uid: str) -> dict:
    if uid not in bank or not isinstance(bank[uid], dict):
        bank[uid] = dict(_DEFAULT_USER)
    # Fill any missing keys (forward compatible)
    for k, v in _DEFAULT_USER.items():
        bank[uid].setdefault(k, v)
    return bank[uid]


# ----------------------------- public API ------------------------------------

def get_balances(user_id) -> Tuple[float, float]:
    """Return (wallet, bank) for the user, creating a default record if needed."""
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        save_bank(data)
        return float(rec["wallet"]), float(rec["bank"])


def set_balances(user_id, *, wallet: Optional[float] = None, bank: Optional[float] = None) -> Tuple[float, float]:
    """Set wallet and/or bank (clamped to >= 0). Returns (wallet, bank)."""
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        if wallet is not None:
            rec["wallet"] = max(0.0, float(wallet))
        if bank is not None:
            rec["bank"] = max(0.0, float(bank))
        save_bank(data)
        return float(rec["wallet"]), float(rec["bank"])


def adjust_wallet(user_id, delta: float, *, floor: float = 0.0) -> Tuple[bool, float]:
    """Add `delta` to wallet. If result < floor, abort and return (False, current)."""
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        cur = float(rec["wallet"])
        new_val = cur + float(delta)
        if new_val < float(floor):
            return False, cur
        rec["wallet"] = new_val
        save_bank(data)
        return True, new_val


def adjust_bank(user_id, delta: float, *, floor: float = 0.0) -> Tuple[bool, float]:
    """Add `delta` to bank. If result < floor, abort and return (False, current)."""
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        cur = float(rec["bank"])
        new_val = cur + float(delta)
        if new_val < float(floor):
            return False, cur
        rec["bank"] = new_val
        save_bank(data)
        return True, new_val


def move_wallet_to_bank(user_id, amount: float) -> bool:
    """Transfer `amount` from wallet to bank. Returns True on success."""
    if amount <= 0:
        return False
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        if rec["wallet"] < amount:
            return False
        rec["wallet"] -= amount
        rec["bank"] += amount
        save_bank(data)
        return True


def move_bank_to_wallet(user_id, amount: float) -> bool:
    """Transfer `amount` from bank to wallet. Returns True on success."""
    if amount <= 0:
        return False
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        if rec["bank"] < amount:
            return False
        rec["bank"] -= amount
        rec["wallet"] += amount
        save_bank(data)
        return True


# ----------------------------- game flags / cooldowns -------------------------

def set_game_active(user_id, active: bool) -> None:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        rec["game_active"] = bool(active)
        save_bank(data)


def get_game_active(user_id) -> bool:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        save_bank(data)
        return bool(rec.get("game_active", False))


def get_last_earn_ts(user_id) -> float:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        save_bank(data)
        return float(rec.get("last_earn_ts", 0.0))


def set_last_earn_ts(user_id, ts: Optional[float] = None) -> None:
    uid = str(user_id)
    ts = time.time() if ts is None else float(ts)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        rec["last_earn_ts"] = ts
        save_bank(data)


def get_last_rob_ts(user_id) -> float:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        save_bank(data)
        return float(rec.get("last_rob_ts", 0.0))


def set_last_rob_ts(user_id, ts: Optional[float] = None) -> None:
    uid = str(user_id)
    ts = time.time() if ts is None else float(ts)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        rec["last_rob_ts"] = ts
        save_bank(data)


# ----------------------------- win/loss stats ---------------------------------

def get_stats(user_id) -> Tuple[int, int]:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        save_bank(data)
        return int(rec.get("wins", 0)), int(rec.get("losses", 0))


def increment_win(user_id) -> Tuple[int, int]:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        rec["wins"] = int(rec.get("wins", 0)) + 1
        save_bank(data)
        return int(rec["wins"]), int(rec.get("losses", 0))


def increment_loss(user_id) -> Tuple[int, int]:
    uid = str(user_id)
    with _LOCK:
        data = load_bank()
        rec = _ensure_user(data, uid)
        rec["losses"] = int(rec.get("losses", 0)) + 1
        save_bank(data)
        return int(rec.get("wins", 0)), int(rec["losses"])


def get_all_stats() -> Dict[str, Dict[str, int]]:
    """Return mapping of user_id -> {wins, losses}."""
    with _LOCK:
        data = load_bank()
        for uid in list(data.keys()):
            _ensure_user(data, uid)
        save_bank(data)
        out: Dict[str, Dict[str, int]] = {}
        for uid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            out[uid] = {
                "wins": int(rec.get("wins", 0)),
                "losses": int(rec.get("losses", 0)),
            }
        return out


# ----------------------------- legacy shim -----------------------------------
# These keep older imports working by mapping single-balance calls to the wallet.

def get_balance(user_id) -> float:
    wallet, _ = get_balances(user_id)
    return wallet


def set_balance(user_id, new_amount: float) -> None:
    set_balances(user_id, wallet=new_amount)


def update_balance(user_id, amount: float, *, allow_negative: bool = False, floor: float = 0.0) -> Tuple[bool, float]:
    floor_val = float(floor) if not allow_negative else float("-inf")
    ok, new_wallet = adjust_wallet(user_id, amount, floor=floor_val)
    return ok, new_wallet