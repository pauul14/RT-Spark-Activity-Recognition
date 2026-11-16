"""Explicit foreground launcher; --check performs no network/service operation.

Prepared for a separately authorized Codespace. Never changes port visibility.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path: sys.path.insert(0,str(PROJECT))
from backend.config import Settings

def preflight(settings):
    if settings.environment!='production' or not settings.mqtt_tls or settings.mqtt_is_loopback:
        raise ValueError('Codespaces launch requires production settings and an authenticated remote TLS broker')
    if settings.mqtt_port!=8883 or not settings.mqtt_device_ids:
        raise ValueError('EMQX Serverless requires port 8883 and exact MQTT_DEVICE_IDS matching the broker ACL')
    host=settings.mqtt_host.lower()
    if host.endswith(('.example','.invalid','example.com')) or 'replace' in host:
        raise ValueError('Replace example host privately before startup')
    if not settings.database_path.resolve().is_relative_to(PROJECT):
        raise ValueError('Keep Codespaces SQLite in persistent workspace storage')
    expected='189e20e5fd67dd9a76223ba6d738896f000f42e8b0a4cabd956794864685252b'  # unchanged trusted phone baseline SHA-256
    if hashlib.sha256(settings.model_path.read_bytes()).hexdigest()!=expected:
        raise ValueError('Phone baseline model hash mismatch; do not substitute a different pickle')
    if settings.board_model_version=='three-class-v2' and not settings.board_model_dir:
        raise ValueError('Three-class launch requires the frozen board model directory')
    if settings.board_model_dir:
        from backend.mqtt_worker import load_board_model
        reviewed=load_board_model(settings)  # includes the explicit v2 release gate
        if settings.board_model_version=='three-class-v2':
            from backend.three_class_model import DEVICE
            reviewed_devices=(DEVICE,)
        else:
            reviewed_devices=reviewed.bundle['device_ids']
        if not set(settings.mqtt_device_ids)<=set(reviewed_devices):
            raise ValueError('MQTT_DEVICE_IDS must be represented in the reviewed board evidence')
    if settings.allow_dataset_demo:
        raise ValueError('The genuine Codespaces path requires ALLOW_DATASET_DEMO=false')
    return {'configuration':'valid','model_hash':'verified','hardware_inference':'reviewed_bundle' if settings.board_model_dir else 'disabled',
            'network_operations':0,'port_visibility_changed':False}

def supervise(*, popen=subprocess.Popen, sleep=time.sleep):
    """Stop both owned foreground children if either exits; never orphan a worker."""
    children=[]
    try:
        for module in ('backend.api','backend.mqtt_worker'):
            children.append(popen([sys.executable,'-B','-m',module],cwd=PROJECT))
        while True:
            for child in children:
                code=child.poll()
                if code is not None:
                    return code or 1  # early clean exit is still a failed combined service
            sleep(.25)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in reversed(children):
            if child.poll() is None: child.terminate()
        for child in reversed(children):
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--component',choices=['api','worker','all'])
    args=parser.parse_args(); settings=Settings.from_env()
    preflight(settings)
    print('Configuration/model checks passed; no connection made by preflight.',flush=True)
    if args.check: return 0
    if not args.component: parser.error('Choose --check or an explicitly authorized --component api|worker')
    if os.getenv('CODESPACE_NAME') is None:
        parser.error('Codespaces launcher requires a Codespace; use normal local modules for loopback testing')
    if args.component=='all': return supervise()
    return subprocess.call([sys.executable,'-B','-m','backend.'+('api' if args.component=='api' else 'mqtt_worker')],cwd=PROJECT)

if __name__=='__main__': raise SystemExit(main())
