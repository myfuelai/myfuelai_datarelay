from cryptography.fernet import Fernet
import json

# generate key ONCE and store safely
key = Fernet.generate_key()
print("ENCRYPTION_KEY =", key.decode())

fernet = Fernet(key)
#fastapi_listener\src\pdi\installer\secrets.json
with open("fastapi_listener\src\pdi\installer\secrets.json", "rb") as f:
    encrypted = fernet.encrypt(f.read())

print("ENCRYPTED_BLOB =", encrypted.decode())
