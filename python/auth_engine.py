"""
User Authentication & FPL Profile Management Engine.
Handles:
- User registration & login with Email and Password
- Secure password hashing with PBKDF2 HMAC SHA-256 + cryptographic salt
- FPL Manager ID linkage and profile caching (Manager Name, Team Name, Overall Rank)
- Session token generation and validation
- Persistence in data/users.json
"""

import os
import json
import uuid
import secrets
import hashlib
import re
from typing import Dict, Any, Optional, Tuple

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) if os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))) else os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")


class AuthEngine:
    def __init__(self, fpl_engine):
        self.fpl_engine = fpl_engine
        os.makedirs(DATA_DIR, exist_ok=True)
        self.users: Dict[str, Dict[str, Any]] = {}       # Keyed by user_id
        self.users_by_email: Dict[str, str] = {}         # email.lower() -> user_id
        self.sessions: Dict[str, str] = {}               # token -> user_id
        self._load_users()
        self._load_sessions()

    def _load_users(self):
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.users = data.get("users", {})
                    self.users_by_email = {u["email"].lower(): uid for uid, u in self.users.items()}
            except Exception as e:
                print(f"[AuthEngine] Error loading users: {e}")
                self.users = {}
                self.users_by_email = {}

    def _save_users(self):
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump({"users": self.users}, f, indent=2)
        except Exception as e:
            print(f"[AuthEngine] Error saving users: {e}")

    def _load_sessions(self):
        if os.path.exists(SESSIONS_FILE):
            try:
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception as e:
                self.sessions = {}

    def _save_sessions(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, indent=2)
        except Exception as e:
            print(f"[AuthEngine] Error saving sessions: {e}")

    @staticmethod
    def _hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
        """Hashes password with PBKDF2 HMAC SHA-256."""
        if salt is None:
            salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        ).hex()
        return pwd_hash, salt

    @staticmethod
    def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
        pwd_hash, _ = AuthEngine._hash_password(password, salt)
        return secrets.compare_digest(pwd_hash, expected_hash)

    def register(self, email: str, password: str, manager_id: Optional[int] = None) -> Dict[str, Any]:
        """Registers a new user account with email, password, and optional FPL Manager ID."""
        email_clean = email.strip().lower()
        if not email_clean or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_clean):
            return {"success": False, "error": "Please enter a valid email address."}

        if not password or len(password) < 6:
            return {"success": False, "error": "Password must be at least 6 characters long."}

        if email_clean in self.users_by_email:
            return {"success": False, "error": "An account with this email already exists."}

        # Fetch FPL profile details if manager_id provided
        manager_id_int = None
        fpl_profile: Dict[str, Any] = {}
        if manager_id:
            try:
                manager_id_int = int(str(manager_id).strip())
                fpl_profile = self.fpl_engine.fetch_manager_entry(manager_id_int)
            except (ValueError, TypeError):
                manager_id_int = None

        user_id = str(uuid.uuid4())
        pwd_hash, salt = self._hash_password(password)

        user_record = {
            "id": user_id,
            "email": email_clean,
            "password_hash": pwd_hash,
            "salt": salt,
            "manager_id": manager_id_int,
            "manager_name": fpl_profile.get("manager_name", f"Manager #{manager_id_int}" if manager_id_int else "FPL Manager"),
            "team_name": fpl_profile.get("team_name", f"Team #{manager_id_int}" if manager_id_int else "FPL Squad"),
            "badge_url": fpl_profile.get("badge_url"),
            "overall_points": fpl_profile.get("overall_points", 0),
            "overall_rank": fpl_profile.get("overall_rank", 0)
        }

        self.users[user_id] = user_record
        self.users_by_email[email_clean] = user_id
        self._save_users()

        # Create active session token
        token = secrets.token_hex(32)
        self.sessions[token] = user_id
        self._save_sessions()

        return {
            "success": True,
            "token": token,
            "user": self._format_user_profile(user_record)
        }

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticates user with email and password."""
        email_clean = email.strip().lower()
        user_id = self.users_by_email.get(email_clean)
        if not user_id:
            return {"success": False, "error": "Invalid email or password."}

        user = self.users.get(user_id)
        if not user:
            return {"success": False, "error": "Account not found."}

        if not self._verify_password(password, user["salt"], user["password_hash"]):
            return {"success": False, "error": "Invalid email or password."}

        # Refresh FPL details if manager_id is present
        if user.get("manager_id"):
            try:
                fpl_entry = self.fpl_engine.fetch_manager_entry(user["manager_id"])
                if fpl_entry.get("success"):
                    user["manager_name"] = fpl_entry.get("manager_name", user.get("manager_name"))
                    user["team_name"] = fpl_entry.get("team_name", user.get("team_name"))
                    user["badge_url"] = fpl_entry.get("badge_url", user.get("badge_url"))
                    user["overall_points"] = fpl_entry.get("overall_points", user.get("overall_points", 0))
                    user["overall_rank"] = fpl_entry.get("overall_rank", user.get("overall_rank", 0))
                    self._save_users()
            except Exception as e:
                print(f"[AuthEngine] Background profile refresh warning: {e}")

        token = secrets.token_hex(32)
        self.sessions[token] = user_id
        self._save_sessions()

        return {
            "success": True,
            "token": token,
            "user": self._format_user_profile(user)
        }

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validates token and returns formatted user profile."""
        if not token:
            return None
        user_id = self.sessions.get(token)
        if not user_id:
            return None
        user = self.users.get(user_id)
        if not user:
            return None
        return self._format_user_profile(user)

    def logout(self, token: str) -> bool:
        """Terminates session token."""
        if token in self.sessions:
            del self.sessions[token]
            self._save_sessions()
            return True
        return False

    def update_manager_id(self, token: str, manager_id: int) -> Dict[str, Any]:
        """Updates or links an FPL Manager ID for the logged in user."""
        user_id = self.sessions.get(token)
        if not user_id or user_id not in self.users:
            return {"success": False, "error": "Unauthorized."}

        user = self.users[user_id]
        fpl_profile = self.fpl_engine.fetch_manager_entry(manager_id)

        user["manager_id"] = manager_id
        user["manager_name"] = fpl_profile.get("manager_name", f"Manager #{manager_id}")
        user["team_name"] = fpl_profile.get("team_name", f"Team #{manager_id}")
        user["badge_url"] = fpl_profile.get("badge_url")
        user["overall_points"] = fpl_profile.get("overall_points", 0)
        user["overall_rank"] = fpl_profile.get("overall_rank", 0)
        self._save_users()

        return {
            "success": True,
            "user": self._format_user_profile(user)
        }

    @staticmethod
    def _format_user_profile(user: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": user["id"],
            "email": user["email"],
            "manager_id": user.get("manager_id"),
            "manager_name": user.get("manager_name", "FPL Manager"),
            "team_name": user.get("team_name", "FPL Squad"),
            "badge_url": user.get("badge_url"),
            "overall_points": user.get("overall_points", 0),
            "overall_rank": user.get("overall_rank", 0)
        }
