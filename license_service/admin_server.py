from .server import root, app as public_app
from .core import Store
from .admin import create_admin_app
from cryptography.hazmat.primitives.serialization import load_pem_private_key
app=create_admin_app(Store(root/'licenses.sqlite3',load_pem_private_key((root/'signing.pem').read_bytes(),password=None)),root)
