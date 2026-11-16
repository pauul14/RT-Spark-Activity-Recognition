"""One bounded diagnosis of the worker's MQTT connection, for a Codespace shell.

Uses the worker's own Settings, TLS context, credentials, client ID and protocol, and
makes exactly ONE connection attempt (Paho reconnect and protocol downgrade disabled),
optionally one SUBSCRIBE, then a clean DISCONNECT. Prints only non-secret facts: never
the password, token, raw packets or certificate fingerprints.

  python -B backend/codespaces/mqtt_diagnose.py --subscribe
  python -B backend/codespaces/mqtt_diagnose.py --protocol 5     # separates Banned from Not authorized
  python -B backend/codespaces/mqtt_diagnose.py --expect-username NAME  # compares, prints only true/false
"""
import argparse
from pathlib import Path
import socket
import ssl
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import paho.mqtt.client as mqtt  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.mqtt_transport import classify_connack, configure_client  # noqa: E402

EXIT = {'accepted': 0, 'bad_username_or_password': 20, 'not_authorized': 21, 'banned': 21,
        'bad_authentication_method': 21, 'invalid_client_id': 22, 'protocol_mismatch': 23,
        'server_unavailable': 24, 'quota_exceeded': 24, 'rate_limited': 24, 'subscription_rejected': 25,
        'tls_certificate_or_hostname': 30, 'tls_handshake': 30, 'dns': 31, 'tcp_refused': 32,
        'tcp_timeout': 32, 'network': 32, 'no_connack': 33, 'subscription_timeout': 34,
        'configuration': 10, 'unexpected': 40}
NEXT = {
    'accepted': 'Broker accepted the worker identity. Start the worker and wait for mqtt_subscription_ready.',
    'bad_username_or_password': 'The broker rejected the username/password this shell holds. Since Windows '
        'succeeded, the Codespace value differs: re-enter MQTT_USERNAME/MQTT_PASSWORD privately and restart '
        'the Codespace (or re-export in this shell), then run this check once more.',
    'not_authorized': 'The broker refused this identity. With EMQX this usually means the username is not '
        'in the authentication list, or the client is banned. Check MQTT_USERNAME, then run once with '
        '--protocol 5, which reports Banned separately.',
    'banned': 'EMQX banned this client (often after repeated reconnects). Remove it under Access Control > '
        'Banned/Blacklist or wait for the ban to expire; do not start the retrying worker meanwhile.',
    'bad_authentication_method': 'The broker expects a different authentication method; use plain username/password.',
    'invalid_client_id': 'Set MQTT_CLIENT_ID to 1..23 characters [A-Za-z0-9_-], unique per connection.',
    'protocol_mismatch': 'Run once with --protocol 5 (or 311) to find the version the broker accepts.',
    'server_unavailable': 'Broker-side condition (stopped, overloaded or quota). Check the deployment status and quota.',
    'quota_exceeded': 'EMQX quota reached; the free tier suspends rather than charging. Check the deployment usage.',
    'rate_limited': 'Connection rate limit reached; wait a minute before a single new attempt.',
    'tls_certificate_or_hostname': 'TLS verification failed: check MQTT_HOST is the exact console hostname and '
        'leave MQTT_CA_FILE unset for the public EMQX certificate.',
    'tls_handshake': 'TLS handshake failed before MQTT: check port 8883 and MQTT_TLS=true.',
    'dns': 'MQTT_HOST does not resolve from this Codespace: re-copy the exact hostname.',
    'tcp_refused': 'TCP 8883 refused: check the deployment is running and the port is 8883.',
    'tcp_timeout': 'TCP 8883 timed out from this Codespace: check the deployment is running.',
    'network': 'Network error before TLS completed.',
    'no_connack': 'Broker closed the connection or stayed silent after CONNECT without a CONNACK.',
    'subscription_rejected': 'Connected, but the ACL denied subscribing to the motion topic(s): add worker '
        'subscribe permission for each exact bca182/v1/devices/<device>/motion topic.',
    'subscription_timeout': 'No SUBACK within the time limit.',
}


def say(stage, **values):
    print('stage=' + stage + ''.join(' %s=%s' % item for item in values.items()), flush=True)


def topics(settings):
    found = [('bca182/v1/devices/%s/motion' % device, 1) for device in settings.mqtt_device_ids]
    if not found:
        found = [('bca182/v1/devices/+/motion', 1)]
    if settings.allow_dataset_demo:
        found.append(('bca182/demo/v1/devices/dataset-demo/motion', 1))
    return found


def connect_error(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return 'tls_certificate_or_hostname', getattr(error, 'verify_message', None) or type(error).__name__
    if isinstance(error, ssl.SSLError):
        return 'tls_handshake', error.reason or type(error).__name__
    if isinstance(error, socket.gaierror):
        return 'dns', type(error).__name__
    if isinstance(error, ConnectionRefusedError):
        return 'tcp_refused', type(error).__name__
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'tcp_timeout', type(error).__name__
    if isinstance(error, OSError):
        return 'network', type(error).__name__
    return 'unexpected', type(error).__name__


def diagnose(settings, *, protocol=mqtt.MQTTv311, subscribe=False, expect_username=None,
             client_factory=mqtt.Client, clock=time.monotonic, wait=10.0):
    say('config', host=settings.mqtt_host, port=settings.mqtt_port, tls=settings.mqtt_tls,
        client_id=settings.mqtt_client_id, protocol='5' if protocol == mqtt.MQTTv5 else '3.1.1',
        username_set=bool(settings.mqtt_username), password_set=bool(settings.mqtt_password),
        device_topics=len(settings.mqtt_device_ids))
    if expect_username is not None:
        say('config', username_matches_expected=settings.mqtt_username == expect_username)
    state = {}
    client = client_factory(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.mqtt_client_id,
                            clean_session=None if protocol == mqtt.MQTTv5 else True, protocol=protocol,
                            reconnect_on_failure=False)
    configure_client(client, settings)
    client.on_connect = lambda c, u, flags, reason, properties: state.setdefault('connack', reason)
    client.on_subscribe = lambda c, u, mid, codes, properties: state.setdefault('suback', list(codes))
    client.on_disconnect = lambda c, u, flags, reason, properties: state.setdefault('disconnected', True)
    client.connect_timeout = wait
    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
    except Exception as error:  # noqa: BLE001 - classified, nothing secret in these types
        category, detail = connect_error(error)
        say('transport', result='failed', category=category, detail=repr(str(detail))[:120])
        return category
    sock = client.socket()
    if settings.mqtt_tls and isinstance(sock, ssl.SSLSocket):
        say('tls', result='ok', version=sock.version(), cipher=sock.cipher()[0],
            certificate_and_hostname_verified=True, sni=settings.mqtt_host)
    try:
        deadline = clock() + wait
        while 'connack' not in state and clock() < deadline:
            if client.loop(timeout=0.5) != mqtt.MQTT_ERR_SUCCESS and 'connack' not in state:
                break
        if 'connack' not in state:
            say('mqtt', result='no_connack')
            return 'no_connack'
        category, permanent, name, value = classify_connack(state['connack'])
        say('mqtt', connack=repr(name), code=value, category=category, permanent=permanent)
        if category != 'accepted' or not subscribe:
            return category
        client.subscribe(topics(settings))
        deadline = clock() + wait
        while 'suback' not in state and clock() < deadline:
            if client.loop(timeout=0.5) != mqtt.MQTT_ERR_SUCCESS:
                break
        if 'suback' not in state:
            say('subscribe', result='timeout')
            return 'subscription_timeout'
        failed = [code for code in state['suback'] if getattr(code, 'is_failure', False)]
        say('subscribe', topics=len(state['suback']), rejected=len(failed),
            result='rejected' if failed else 'mqtt_subscription_ready_equivalent')
        return 'subscription_rejected' if failed else 'accepted'
    finally:
        client.disconnect()
        deadline = clock() + 3
        while 'disconnected' not in state and clock() < deadline:
            if client.loop(timeout=0.2) != mqtt.MQTT_ERR_SUCCESS:
                break
        say('disconnect', clean=bool(state.get('disconnected')), attempts=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--protocol', choices=['311', '5'], default='311')
    parser.add_argument('--subscribe', action='store_true', help='after CONNACK success, test the worker subscriptions once')
    parser.add_argument('--expect-username', help='compared in memory; only true/false is printed')
    arguments = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as error:  # config messages name defects, never values
        say('config', result='invalid', detail=repr(str(error)))
        category = 'configuration'
        print('diagnosis=%s exit=%d next=%s' % (category, EXIT[category], 'Fix the named variable privately.'))
        return EXIT[category]
    category = diagnose(settings, protocol=mqtt.MQTTv5 if arguments.protocol == '5' else mqtt.MQTTv311,
                        subscribe=arguments.subscribe, expect_username=arguments.expect_username)
    print('diagnosis=%s exit=%d next=%s' % (category, EXIT[category], NEXT.get(category, 'Review the stage lines above.')))
    return EXIT[category]


if __name__ == '__main__':
    raise SystemExit(main())
