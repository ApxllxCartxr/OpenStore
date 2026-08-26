"""Generate the merchant's Ed25519 signing keypair. Run ONCE, not on every app startup."""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

private_key = Ed25519PrivateKey.generate()
pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
with open("merchant_signing_key.pem", "wb") as f:
    f.write(pem)
print("Key written to merchant_signing_key.pem — add this filename to .gitignore NOW")
