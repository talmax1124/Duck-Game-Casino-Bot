import json
import os

BANK_FILE = "data/bank.json"

def load_bank():
    if not os.path.exists(BANK_FILE):
        with open(BANK_FILE, "w") as f:
            json.dump({}, f)
    with open(BANK_FILE, "r") as f:
        return json.load(f)

def save_bank(bank):
    with open(BANK_FILE, "w") as f:
        json.dump(bank, f, indent=4)

def get_balance(user_id):
    bank = load_bank()
    balance = bank.get(str(user_id), 1000.0)
    print(f"[DEBUG] get_balance: User {user_id} has ${balance}")
    return balance

def update_balance(user_id, amount):
    bank = load_bank()
    user_id = str(user_id)
    bank[user_id] = bank.get(user_id, 1000.0) + amount
    save_bank(bank)
    print(f"[DEBUG] update_balance: User {user_id} new balance is ${bank[user_id]}")