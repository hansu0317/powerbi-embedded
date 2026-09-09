"""Create the first portal administrator interactively; never reset existing users."""
import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bcrypt
import psycopg2
from config import DB_CONFIG, PASSWORD_MIN_LEN


def main() -> int:
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE is_admin LIMIT 1")
            if cur.fetchone():
                print("Administrator already exists; no changes made.")
                return 1
            password = getpass.getpass("New admin password: ")
            if len(password) < PASSWORD_MIN_LEN or len(password.encode("utf-8")) > 72:
                print(f"Use at least {PASSWORD_MIN_LEN} characters and at most 72 UTF-8 bytes.")
                return 1
            if password != getpass.getpass("Confirm password: "):
                print("Passwords do not match; no changes made.")
                return 1
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            # Serialize bootstrap attempts and recheck after taking the lock.
            cur.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT 1 FROM users WHERE is_admin OR username='admin' LIMIT 1")
            if cur.fetchone():
                print("Administrator or reserved username already exists; no changes made.")
                return 1
            cur.execute(
                "INSERT INTO users(username,password,display_name,pbi_username,is_admin,can_upload) "
                "VALUES('admin',%s,'Administrator','admin',TRUE,TRUE)", (hashed,),
            )
        conn.commit()
    print("Initial administrator created: admin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
