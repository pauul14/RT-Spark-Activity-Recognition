"""Synthetic offline integration; no UART, broker, cloud or production approval."""
from pathlib import Path
from contextlib import closing
from copy import deepcopy
import json,sqlite3,tempfile,time,unittest,shutil,subprocess,os
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.config import ROOT,PROJECT,Settings
from backend.database import Database
from backend.downlink import THREE_CLASS_MODEL_ID,encode_prediction
from backend.three_class_model import ThreeClassBoardModel,DEVICE,PLACEMENT,require_activation
from backend.mqtt_worker import load_board_model
from backend.publisher import fixture_messages
from backend.service import IngestionService
from backend.api import create_app

class ThreeClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'runtime').mkdir(exist_ok=True)
        cls.real=ThreeClassBoardModel(PROJECT/'models/three-class-v2',PLACEMENT)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'runtime');self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/'three.sqlite3')
        self.topic,self.messages=fixture_messages(5,device_id=DEVICE,mode='hardware')
        for m in self.messages:m.update(sensor_model='ICM-20608-G',frame_id='sensor_native_unmapped',calibration_id=None,acceleration_unit='g',gyro_unit='rad/s',gravity_handling='included')
    def ingest(self,model=None):
        service=IngestionService(self.db,None,board_model=model or self.real)
        return [service.ingest(self.topic,json.dumps(m).encode()) for m in self.messages]
    def test_exact_frozen_model_through_ingestion_database_api_and_outbox(self):
        with patch('sklearn.pipeline.Pipeline.fit',side_effect=AssertionError('No fit')):
            result=self.ingest()
        self.assertEqual(result[-1]['status'],'prediction_stored')
        row=self.db.latest();self.assertEqual(row['model_id'],THREE_CLASS_MODEL_ID)
        self.assertEqual(row['prediction_schema_version'],2)
        self.assertAlmostEqual(sum(row[k+'_probability'] for k in ('walking','running','stationary')),1)
        self.assertEqual(self.db.current(DEVICE)['prediction_schema_version'],2)
        payload=json.loads(self.db.pending_downlinks()[0]['payload']);self.assertEqual(payload['schema_version'],2)
        token='isolated-three-class-test-token-0123456789'
        with TestClient(create_app(self.db,Settings(database_path=self.db.path,api_token=token))) as client:
            self.assertEqual(client.get('/api/activities/current',params={'device_id':DEVICE}).status_code,401)
            headers={'Authorization':'Bearer '+token}
            for endpoint in ('current','latest','history'):
                response=client.get('/api/activities/'+endpoint,params={'device_id':DEVICE},headers=headers)
                self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
                item=response.json()['items'][0] if endpoint=='history' else response.json()
                self.assertEqual(item['prediction_schema_version'],2)
        self.assertFalse(self.ingest()[0]['status']=='prediction_stored') # persisted deduplication
    def test_stationary_all_layers_and_stale_boot_guards(self):
        class SyntheticStationary:
            model_id=THREE_CLASS_MODEL_ID
            accepts=staticmethod(lambda m: m.device_id==DEVICE)
            predict=staticmethod(lambda samples:{'activity':'stationary','probabilities':{'walking':.01,'running':.02,'stationary':.97}})
        self.ingest(SyntheticStationary());self.assertEqual(self.db.current(DEVICE)['state'],'STATIONARY')
        row=self.db.latest();self.assertEqual(json.loads(encode_prediction(row)[1])['activity'],'stationary')
        with self.db.transaction() as conn:conn.execute("UPDATE device_status SET boot_id='different-boot'")
        self.assertEqual(self.db.current(DEVICE)['state'],'UNVALIDATED')
        with self.db.transaction() as conn:conn.execute("UPDATE device_status SET server_timestamp='2000-01-01T00:00:00Z'")
        self.assertEqual(self.db.current(DEVICE)['state'],'OFFLINE')
    def test_default_worker_keeps_two_class_and_v2_requires_release_approval(self):
        self.assertEqual(Settings().board_model_version,'two-class-v1')
        self.assertIsNone(load_board_model(Settings()))
        with self.assertRaises(ValueError):require_activation(None)
        with self.assertRaises(ValueError):load_board_model(Settings(board_model_dir=PROJECT/'models/three-class-v2',board_placement_id=PLACEMENT,board_model_version='three-class-v2'))
    def test_wrong_model_hash_manifest_and_feature_sources_rejected(self):
        folder=Path(self.tmp.name)/'model';shutil.copytree(PROJECT/'models/three-class-v2',folder)
        with (folder/'research-model.joblib').open('ab') as f:f.write(b'changed')
        with self.assertRaises(ValueError),patch('joblib.load',side_effect=AssertionError('Must reject before deserialize')):ThreeClassBoardModel(folder,PLACEMENT)
        with self.assertRaises(ValueError):ThreeClassBoardModel(PROJECT/'models/three-class-v2','old-placement')
        from feature_set import FeatureVector,contract_sha256,FEATURE_NAMES
        with self.assertRaises(ValueError):self.real.model.predict([0.]*41)
        with self.assertRaises(ValueError):FeatureVector(tuple([0.]*41),feature_names=tuple(reversed(FEATURE_NAMES)),definition_sha256=contract_sha256())
    def test_wrong_source_cannot_reach_v2_inference(self):
        from backend.validation import validate
        m=deepcopy(self.messages[0]);m['frame_id']='wrong-frame'
        self.assertFalse(self.real.accepts(validate(self.topic,json.dumps(m).encode())))
        m=deepcopy(self.messages[0]);m['device_id']='other-board'
        self.assertFalse(self.real.accepts(validate(self.topic.replace(DEVICE,'other-board'),json.dumps(m).encode())))
    def test_incompatible_dependency_fails_before_deserialization(self):
        with patch('backend.three_class_model.version',return_value='incompatible'),self.assertRaises(ValueError),patch('joblib.load',side_effect=AssertionError('No deserialize')):
            ThreeClassBoardModel(PROJECT/'models/three-class-v2',PLACEMENT)
    def test_versioned_downlink_rejects_misidentified_stationary_and_demo(self):
        self.ingest();row=self.db.latest();row['activity']='stationary'
        for change in ({'model_id':'legacy-model'},{'inference_mode':'dataset_demo','hardware_validated':False},{'device_id':'wrong-board'}):
            with self.subTest(change=change),self.assertRaises(ValueError):encode_prediction(row|change)
    def test_old_database_migration_preserves_history_and_outbox(self):
        # Materialize the exact old schema and a legacy row before opening v3.
        from backend.database import SCHEMA
        old=SCHEMA.replace("'walking','running','stationary'","'walking','running'")
        old=old.replace(' stationary_probability REAL NOT NULL DEFAULT 0 CHECK(stationary_probability BETWEEN 0 AND 1),\n','')
        path=Path(self.tmp.name)/'old.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(old)
            conn.execute("INSERT INTO predictions VALUES (1,'device','boot',4,'walking',.7,.3,0,3800,'2026-11-07T00:00:00Z','legacy','board_validated',1)")
            conn.execute("INSERT INTO prediction_outbox VALUES (1,'topic',?, ?,NULL)",(b'legacy-payload',time.time()+1000))
            conn.commit()
        migrated=Database(path);row=migrated.latest();self.assertEqual(row['walking_probability'],.7);self.assertEqual(row['stationary_probability'],0);self.assertEqual(row['prediction_schema_version'],1)
        self.assertEqual(migrated.pending_downlinks()[0]['payload'],b'legacy-payload')
    def test_generated_v2_packet_crosses_real_C_receiver_and_rejects_bad_identity_replay(self):
        self.ingest();row=self.db.latest();row['activity']='stationary';topic,body=encode_prediction(row)
        # Compile the production receiver, with all schema/freshness guards intact.
        source=Path(self.tmp.name)/'receiver.c';binary=source.with_suffix('.exe')
        source.write_text('''#include "prediction.h"
#include <stdio.h>
#include <string.h>
int main(void){char b[513];size_t n=fread(b,1,512,stdin);struct pred_state s={0};struct pred_identity id={"'''+DEVICE+'''","'''+row['boot_id']+'''",'''+str(row['device_timestamp_ms'])+''','''+str(row['sequence_end'])+''',1};
if(pred_process(&s,&id,b,n)!=PRED_ACCEPTED)return 1;
if(strcmp(pred_display_text(&s,id.now_ms,1),"STATIONARY"))return 2;
if(pred_process(&s,&id,b,n)!=PRED_REJECT_REPLAY)return 3;
if(strcmp(pred_display_text(&s,id.now_ms+BCA_PRED_HOLD_MS+1,1),"WAITING"))return 4;
return 0;}''')
        app=PROJECT/'firmware/rtspark_integrated_v1/applications';gcc=Path.home()/'.platformio/packages/toolchain-gccmingw32/bin/gcc.exe'
        env=dict(os.environ,PATH=str(gcc.parent)+os.pathsep+os.environ['PATH'])
        subprocess.run([str(gcc),'-std=c99','-Wall','-Werror','-I'+str(app),str(source),str(app/'prediction.c'),'-o',str(binary)],check=True,env=env,capture_output=True)
        p=subprocess.run([str(binary)],input=body,capture_output=True,env=env);self.assertEqual(p.returncode,0,p.stderr)
        for field,value in [('model_id','wrong'),('schema_version',1),('boot_id','old-boot'),('device_id','other-device'),('device_timestamp_ms',row['device_timestamp_ms']+1)]:
            changed=json.loads(body);changed[field]=value;p=subprocess.run([str(binary)],input=json.dumps(changed).encode(),capture_output=True,env=env);self.assertNotEqual(p.returncode,0)

if __name__=='__main__':unittest.main()
