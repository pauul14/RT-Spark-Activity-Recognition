"""Shared Paho connection security. Host is used unchanged for SNI and verification."""
import ssl


class HandshakeSocket(ssl.SSLSocket):
    """Close failed blocking handshakes that Paho 2.1.0 otherwise leaves open."""

    def do_handshake(self, block=False):
        try:
            return super().do_handshake(block)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            raise
        except BaseException:
            self.close()
            raise


def tls_context(settings):
    context = ssl.create_default_context(cafile=str(settings.mqtt_ca_file) if settings.mqtt_ca_file else None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Public Python SSL hook; no third-party code or private Paho API changes.
    context.sslsocket_class = HandshakeSocket
    # create_default_context enables CERT_REQUIRED and hostname checks.
    # No insecure override, alternate server name, or plaintext fallback exists.
    return context


def configure_client(client, settings):
    if settings.mqtt_tls:
        client.tls_set_context(tls_context(settings))
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    return client


# Paho 2.x passes a ReasonCode to on_connect for both MQTT 3.1.1 and 5. For 3.1.1 it
# converts the CONNACK return code (1..5) to the MQTT 5 value (132..136), so names, not
# numbers, are the stable key. Permanent causes must not be retried automatically.
CONNACK_CATEGORIES = {
    "Bad user name or password": ("bad_username_or_password", True),
    "Not authorized": ("not_authorized", True),
    "Banned": ("banned", True),
    "Bad authentication method": ("bad_authentication_method", True),
    "Client identifier not valid": ("invalid_client_id", True),
    "Unsupported protocol version": ("protocol_mismatch", True),
    "Malformed packet": ("protocol_mismatch", True),
    "Protocol error": ("protocol_mismatch", True),
    "Server unavailable": ("server_unavailable", False),
    "Server busy": ("server_unavailable", False),
    "Quota exceeded": ("quota_exceeded", False),
    "Connection rate exceeded": ("rate_limited", False),
}


def classify_connack(reason_code):
    """Returns (category, permanent, name, value). Never contains connection secrets."""
    if isinstance(reason_code, int) and 1 <= reason_code <= 5:  # raw MQTT 3.1.1 CONNACK code
        from paho.mqtt.client import convert_connack_rc_to_reason_code
        reason_code = convert_connack_rc_to_reason_code(reason_code)
    name, value = str(getattr(reason_code, "getName", lambda: reason_code)()), getattr(reason_code, "value", reason_code)
    if name == "Success" or reason_code == 0:
        return "accepted", False, "Success", 0
    category, permanent = CONNACK_CATEGORIES.get(name, ("unexpected", True))
    return category, permanent, name, value
