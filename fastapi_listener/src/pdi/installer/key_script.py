from cryptography.fernet import Fernet
import json

# generate key ONCE and store safely
key = Fernet.generate_key()
print("ENCRYPTION_KEY =", key.decode())

fernet = Fernet(key)

with open("secrets.json", "rb") as f:
    encrypted = fernet.encrypt(f.read())

print("ENCRYPTED_BLOB =", encrypted.decode())
