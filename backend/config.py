"""Local defaults and explicit, fail-closed production configuration."""
from dataclasses import dataclass, field
import os
import ipaddress
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def credential_problem(value):
    """Describes a copy/paste defect in a credential without revealing any of it."""
    if not value:
        return None
    if value != value.strip():
        return "has leading or trailing whitespace (often a pasted newline)"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "contains a control character such as CR, LF or TAB"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return "is wrapped in quotes (Codespaces secrets must not include .env quoting)"
    return None


@dataclass(frozen=True)
class Settings:
    database_path: Path = ROOT / "runtime" / "activities.sqlite3"
    model_path: Path = PROJECT / "ml" / "artifacts" / "model.joblib"
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_client_id: str = "bca182-local-worker"
    mqtt_device_ids: tuple[str, ...] = ()
    mqtt_username: str | None = field(default=None, repr=False)
    mqtt_password: str | None = field(default=None, repr=False)
    mqtt_tls: bool = False
    mqtt_ca_file: Path | None = None
    allow_dataset_demo: bool = False
    api_port: int = 8000
    environment: str = "local"
    api_token: str | None = field(default=None, repr=False)
    board_model_dir: Path | None = None
    board_placement_id: str | None = None
    archive_telemetry: bool = True
    board_model_version: str = "two-class-v1"
    board_activation_file: Path | None = None

    def __post_init__(self):
        if self.board_model_version not in ("two-class-v1", "three-class-v2"):
            raise ValueError("Unsupported board model version")
        if type(self.archive_telemetry) is not bool:
            raise ValueError('ARCHIVE_TELEMETRY must be a boolean')
        if (not isinstance(self.mqtt_device_ids, tuple) or len(self.mqtt_device_ids)>16 or
                any(not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value)
                    for value in self.mqtt_device_ids) or len(set(self.mqtt_device_ids))!=len(self.mqtt_device_ids)):
            raise ValueError('MQTT_DEVICE_IDS must contain at most 16 unique exact device IDs')
        if not isinstance(self.mqtt_host, str) or not self.mqtt_host or self.mqtt_host != self.mqtt_host.strip():
            raise ValueError("MQTT_HOST must be a hostname without whitespace or a URI scheme")
        if not isinstance(self.mqtt_tls, bool):
            raise ValueError("MQTT_TLS must be a boolean")
        if not self.mqtt_is_loopback:
            try:
                ipaddress.ip_address(self.mqtt_host)
            except ValueError:
                pass
            else:
                raise ValueError("Remote MQTT requires a DNS hostname for SNI, not an IP address")
            labels = self.mqtt_host.rstrip(".").split(".")
            if (len(self.mqtt_host) > 253 or len(labels) < 2 or
                    any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                        for label in labels)):
                raise ValueError("Remote MQTT requires a valid DNS hostname for SNI")
            if not self.mqtt_tls:
                raise ValueError("Remote MQTT requires verified TLS")
        if self.mqtt_tls and (not self.mqtt_username or not self.mqtt_password):
            raise ValueError("MQTT TLS requires MQTT_USERNAME and MQTT_PASSWORD")
        if self.mqtt_ca_file is not None:
            if not self.mqtt_tls:
                raise ValueError("MQTT_CA_FILE requires MQTT_TLS=true")
            if not Path(self.mqtt_ca_file).is_file():
                raise ValueError("MQTT_CA_FILE must reference an existing CA file")
        if not 1 <= self.mqtt_port <= 65535 or not 1 <= self.api_port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if self.environment not in {"local", "production"}:
            raise ValueError("APP_ENV must be local or production")
        if self.environment == "local" and not Path(self.database_path).resolve().is_relative_to(ROOT):
            raise ValueError("Keep local databases inside backend/")
        if self.mqtt_password is not None and not self.mqtt_username:
            raise ValueError("MQTT password requires a username")
        for name, value in (("MQTT_USERNAME", self.mqtt_username), ("MQTT_PASSWORD", self.mqtt_password)):
            problem = credential_problem(value)
            if problem:  # name the defect only; never the value
                raise ValueError(f"{name} {problem}; re-enter the secret value exactly as set in the broker")
        if self.api_token is not None and (not 32<=len(self.api_token)<=512 or not self.api_token.isascii()
                                          or any(c.isspace() for c in self.api_token)):
            raise ValueError("API_TOKEN must contain 32..512 ASCII characters without whitespace")
        if self.environment == "production":
            if not self.api_token or not self.mqtt_username or not self.mqtt_password:
                raise ValueError("Production requires API_TOKEN, MQTT_USERNAME and MQTT_PASSWORD")
            if not Path(self.database_path).is_absolute() or not Path(self.model_path).is_absolute():
                raise ValueError("Production database and model paths must be absolute")
        if self.board_model_dir is not None:
            if not Path(self.board_model_dir).is_dir() or not self.board_placement_id:
                raise ValueError('BOARD_MODEL_DIR requires an existing reviewed bundle and explicit BOARD_PLACEMENT_ID')

    @property
    def mqtt_is_loopback(self):
        if self.mqtt_host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.mqtt_host).is_loopback
        except ValueError:
            return False

    @classmethod
    def from_env(cls):
        archive=os.getenv('ARCHIVE_TELEMETRY','true').lower()
        if archive not in {'true','false'}:
            raise ValueError('ARCHIVE_TELEMETRY must be true or false')
        devices=os.getenv('MQTT_DEVICE_IDS')
        if devices is not None and (not devices or any(not part.strip() for part in devices.split(','))):
            raise ValueError('MQTT_DEVICE_IDS must be unset or a comma-separated list of exact device IDs')
        enabled = os.getenv("ALLOW_DATASET_DEMO", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("ALLOW_DATASET_DEMO must be true or false")
        tls = os.getenv("MQTT_TLS", "false").lower()
        if tls not in {"true", "false"}:
            raise ValueError("MQTT_TLS must be true or false")
        ca_file = os.getenv("MQTT_CA_FILE")
        if ca_file is not None and not ca_file.strip():
            raise ValueError("MQTT_CA_FILE must be unset or name a CA file")
        database = Path(os.getenv("DATABASE_PATH", "runtime/activities.sqlite3"))
        if not database.is_absolute():
            database = ROOT / database
        model = Path(os.getenv("MODEL_PATH", str(PROJECT / "ml" / "artifacts" / "model.joblib")))
        if not model.is_absolute():
            model = PROJECT / model
        return cls(database_path=database, model_path=model, mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
                   mqtt_port=int(os.getenv("MQTT_PORT", "8883" if tls == "true" else "1883")),
                   mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "bca182-local-worker"),
                   mqtt_device_ids=tuple(part.strip() for part in devices.split(',')) if devices is not None else (),
                   mqtt_username=os.getenv("MQTT_USERNAME"), mqtt_password=os.getenv("MQTT_PASSWORD"),
                   mqtt_tls=tls == "true", mqtt_ca_file=Path(ca_file) if ca_file else None,
                   allow_dataset_demo=enabled == "true", api_port=int(os.getenv("API_PORT", "8000")),
                   environment=os.getenv("APP_ENV", "local"), api_token=os.getenv("API_TOKEN"),
                   board_model_dir=Path(os.environ['BOARD_MODEL_DIR']) if os.getenv('BOARD_MODEL_DIR') else None,
                   board_placement_id=os.getenv('BOARD_PLACEMENT_ID'),archive_telemetry=archive=='true',
                   board_model_version=os.getenv('BOARD_MODEL_VERSION','two-class-v1'),
                   board_activation_file=Path(os.environ['BOARD_ACTIVATION_FILE']) if os.getenv('BOARD_ACTIVATION_FILE') else None)
