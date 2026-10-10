"""python -m app.hashpw  ->  prints the value for ADMIN_PASSWORD_HASH"""
import getpass, sys
from .security import hash_password

pw = getpass.getpass("Choose an admin password (min 12 chars): ")
if len(pw) < 12:
    sys.exit("Too short - use at least 12 characters.")
if pw != getpass.getpass("Repeat: "):
    sys.exit("Passwords do not match.")
print("\nADMIN_PASSWORD_HASH=" + hash_password(pw))
