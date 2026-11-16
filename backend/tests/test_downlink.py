"""Synthetic contract/queue fixtures; never evidence of hardware classification."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json,tempfile,time,unittest
from fastapi.testclient import TestClient
from backend.api import create_app
from backend.config import ROOT,Settings
from backend.database import Database
from backend.downlink import encode_prediction
from backend.mqtt_worker import MQTTWorker
from backend.publisher import fixture_messages
from backend.service import IngestionService

class FixtureModel:
    model_id='sha256:synthetic-fixture:not-hardware'
    def predict(self,samples):
        return {'activity':'walking','probabilities':{'walking':0.7,'running':0.3}}

class DownlinkTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'runtime').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'runtime'); self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/'db.sqlite3')
        self.topic,self.messages=fixture_messages(4)
        self.service=IngestionService(self.db,FixtureModel(),allow_demo=True)
        self.settings=Settings(database_path=self.db.path,allow_dataset_demo=True)
    def populate(self):
        for message in self.messages:
            self.service.ingest(self.topic,json.dumps(message).encode())
    def test_atomic_outbox_and_demo_isolation(self):
        self.populate(); rows=self.db.pending_downlinks(); self.assertEqual(len(rows),1)
        body=json.loads(rows[0]['payload'])
        self.assertEqual(rows[0]['topic'],self.topic.replace('/motion','/prediction'))
        self.assertIn('/demo/',rows[0]['topic']); self.assertFalse(body['hardware_validated'])
        self.assertEqual(body['prediction_seq'],self.db.latest()['id'])
        self.assertEqual(set(body),{'schema_version','device_id','boot_id','prediction_seq','sequence_end',
            'device_timestamp_ms','activity','model_id','hardware_validated'})
        self.assertLessEqual(len(rows[0]['payload']),512)
        self.service.ingest(self.topic,json.dumps(self.messages[-1]).encode())
        self.assertEqual(len(self.db.pending_downlinks()),1)
    def test_ack_and_expiry_preserve_prediction_history(self):
        self.populate(); row=self.db.pending_downlinks()[0]
        self.db.acknowledge_downlink(row['prediction_id']); self.assertEqual(self.db.pending_downlinks(),[])
        self.assertIsNotNone(self.db.latest())
    def test_expired_record_never_enter_new_publish_queue(self):
        self.populate()
        with self.db.transaction() as conn: conn.execute('UPDATE prediction_outbox SET expires_at=?',(time.time()-1,))
        self.assertEqual(self.db.pending_downlinks(),[]); self.assertIsNotNone(self.db.latest())
    def test_callback_publish_qos_and_puback_cleanup(self):
        self.populate(); client=Mock(); client.publish.return_value=SimpleNamespace(rc=0,mid=12)
        worker=MQTTWorker(self.settings,self.service,client=client)
        worker.on_subscribe(client,None,1,[],None)
        self.assertEqual(client.publish.call_args.kwargs,{'qos':1,'retain':False})
        self.assertEqual(len(worker.pending_mids),1)
        worker.flush_downlinks(); self.assertEqual(client.publish.call_count,1)
        worker.on_publish(client,None,12,0,None)
        self.assertEqual(self.db.pending_downlinks(),[])
    def test_disconnect_and_queue_failure_preserve_pending(self):
        self.populate(); client=Mock(); client.publish.return_value=SimpleNamespace(rc=4,mid=1)
        worker=MQTTWorker(self.settings,self.service,client=client); worker.on_subscribe(client,None,1,[],None)
        self.assertEqual(worker.pending_mids,{}); self.assertEqual(len(self.db.pending_downlinks()),1)
        client.publish.return_value=SimpleNamespace(rc=0,mid=2)
        worker.flush_downlinks(); worker.on_disconnect(client,None,None,1,None)
        self.assertEqual(worker.pending_mids,{}); self.assertFalse(worker.subscribed)
        self.assertEqual(len(self.db.pending_downlinks()),1)
    def test_current_waiting_unvalidated_offline_and_authentication(self):
        token='synthetic-test-token-'+'x'*32
        settings=Settings(database_path=self.db.path,api_token=token)
        with TestClient(create_app(self.db,settings)) as client:
            path='/api/activities/current?device_id=dataset-demo'
            self.assertEqual(client.get(path).status_code,401)
            headers={'Authorization':'Bearer '+token}
            self.assertEqual(client.get(path,headers=headers).json()['state'],'WAITING')
            self.populate(); result=client.get(path,headers=headers)
            self.assertEqual(result.json()['state'],'UNVALIDATED'); self.assertIsNone(result.json()['activity'])
            self.assertEqual(result.headers['cache-control'],'no-store')
            with self.db.transaction() as conn: conn.execute("UPDATE device_status SET server_timestamp='2000-01-01T00:00:00Z'")
            self.assertEqual(client.get(path,headers=headers).json()['state'],'OFFLINE')
    def test_hardware_unvalidated_never_produces_downlink(self):
        topic=self.topic.replace('/demo/','/')
        for source in self.messages:
            body=deepcopy(source); body.update(sensor_model='ICM-20608-G',frame_id='rtspark_board_v1',
                calibration_id=None,acceleration_unit='g',gyro_unit='rad/s',gravity_handling='included')
            result=self.service.ingest(topic,json.dumps(body).encode())
            self.assertEqual(result['status'],'hardware_unvalidated')
        self.assertEqual(self.db.pending_downlinks(),[]); self.assertIsNone(self.db.latest())
        self.assertEqual(self.db.current('dataset-demo')['state'],'UNVALIDATED')
    def test_contract_rejects_false_validation_and_escaping(self):
        self.populate(); row=self.db.latest()
        for field,value in [('hardware_validated','false'),('hardware_validated',True),('inference_mode','hardware_unvalidated'),
                            ('model_id','bad\nvalue'),('activity','unknown'),('sequence_end',True),('boot_id','old/boot')]:
            bad=deepcopy(row); bad[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError): encode_prediction(bad)

if __name__=='__main__': unittest.main()
