"""Strict STM32 prediction contract. Broker ACLs, not this unsigned JSON, authorize writers."""
import json
import re

THREE_CLASS_MODEL_ID='board-sha256:134446e8e056d45cb5cce01da0343e0d88e443aa55b2dc7843a15dfc06fa63eb:board_4s_v2_gravity_free'

def prediction_schema(model_id):
    return 2 if model_id==THREE_CLASS_MODEL_ID else 1

ID=re.compile(r'^[A-Za-z0-9_-]{1,128}$')
def encode_prediction(row):
    for field in ('device_id','boot_id','model_id'):
        value=row[field]
        if not isinstance(value,str) or not value or any(c in value for c in '\\"') or any(not 32<=ord(c)<=126 for c in value):
            raise ValueError('unsafe_downlink_string')
    if not ID.fullmatch(row['device_id']) or not ID.fullmatch(row['boot_id']):
        raise ValueError('invalid_downlink_identity')
    for field in ('id','sequence_end','device_timestamp_ms'):
        if type(row[field]) is not int or not 0<=row[field]<2**53: raise ValueError('invalid_downlink_integer')
    version=prediction_schema(row['model_id'])
    allowed=('walking','running','stationary') if version==2 else ('walking','running')
    if row['activity'] not in allowed: raise ValueError('invalid_downlink_activity')
    if version==2 and (row['inference_mode']!='board_validated' or row['device_id']!='rtspark-003000423433511539303938'):
        raise ValueError('three_class_source_mismatch')
    if type(row['hardware_validated']) is not bool: raise ValueError('invalid_validation_flag')
    if row['inference_mode'] not in ('dataset_demo','board_validated'): raise ValueError('unsupported_prediction_origin')
    if row['hardware_validated']!=(row['inference_mode']=='board_validated'): raise ValueError('inconsistent_prediction_validation')
    body={'schema_version':version,'device_id':row['device_id'],'boot_id':row['boot_id'],
          'prediction_seq':row['id'],'sequence_end':row['sequence_end'],
          'device_timestamp_ms':row['device_timestamp_ms'],'activity':row['activity'],
          'model_id':row['model_id'],'hardware_validated':bool(row['hardware_validated'])}
    raw=json.dumps(body,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
    if len(raw)>512: raise ValueError('oversized_downlink')
    # Dataset replay can never address a real device's prediction subscription.
    prefix='bca182/demo/v1' if row['inference_mode']=='dataset_demo' else 'bca182/v1'
    return f"{prefix}/devices/{row['device_id']}/prediction",raw
