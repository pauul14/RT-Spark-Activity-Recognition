"""Export an immutable local transport-evidence snapshot, not labelled training data."""
import argparse
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def export(database, output, device):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',device): raise ValueError('Exact device ID required')
    database=Path(database); output=Path(output)
    if not database.is_file(): raise ValueError('Existing database required')
    # read-only SQLite transaction gives one consistent view, including committed WAL.
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('BEGIN')
        rows=conn.execute('SELECT boot_id,seq,topic,payload,received_at FROM telemetry_archive '
                          'WHERE device_id=? ORDER BY received_at,boot_id,seq',(device,))
        with output.open('x',encoding='utf-8',newline='\n') as stream:
            stream.write(json.dumps({'schema_version':1,'kind':'transport_evidence_only',
                'device_id':device,'hardware_validated':False,'training_admission':False,
                'limitations':'MQTT alone lacks UART register/startup/trace and genuine activity label proof'})+'\n')
            count=0
            for boot,seq,topic,payload,received in rows:
                stream.write(json.dumps({'boot_id':boot,'seq':seq,'topic':topic,'received_at':received,
                    'payload_base64':base64.b64encode(payload).decode('ascii'),
                    'payload_sha256':hashlib.sha256(payload).hexdigest()},allow_nan=False)+'\n')
                count+=1
    return count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device-id',required=True)
    args=parser.parse_args(); count=export(args.database,args.output,args.device_id)
    print(f'Exported {count} admitted transport records locally; not labelled training evidence.')

if __name__=='__main__': main()
