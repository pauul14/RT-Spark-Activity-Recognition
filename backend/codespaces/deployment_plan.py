"""Create a non-secret EMQX/Codespaces plan offline. Creates no cloud resource."""
import argparse,json,re
from pathlib import Path

def plan(device):
    if not re.fullmatch('[A-Za-z0-9_-]{1,64}',device):raise ValueError('Use an exact simple device ID, no MQTT wildcards')
    root='bca182/v1/devices/'+device
    return {'device_id':device,'mqtt_device_ids':device,'mqtt_port':8883,'TLS_minimum':'1.2','SNI':'exact deployment DNS hostname',
            'motion_topic':root+'/motion','prediction_topic':root+'/prediction','QoS':1,'retain':False,
            'broker_spend_limit':0,'cloud_operations_performed':0,'hardware_validated':False,
            'ACL':[
                {'identity':'worker-unique-user','publish':[root+'/prediction'],'subscribe':[root+'/motion']},
                {'identity':'device-unique-user','publish':[root+'/motion'],'subscribe':[root+'/prediction']}],
            'environment':{'APP_ENV':'production','MQTT_TLS':'true','MQTT_PORT':'8883','MQTT_DEVICE_IDS':device,
                'ALLOW_DATASET_DEMO':'false','MQTT_HOST':'SET_PRIVATELY_FROM_EMQX_CONSOLE',
                'MQTT_USERNAME':'SET_PRIVATELY_IN_CODESPACES_SECRETS','MQTT_PASSWORD':'SET_PRIVATELY_IN_CODESPACES_SECRETS',
                'API_TOKEN':'SET_PRIVATELY_IN_CODESPACES_SECRETS'},
            'private_transfers':['pinned phone model','approved board model only after real held-out review'],
            'board_model':'hardware_unvalidated until genuine recordings and explicit review',
            'next_actions':['Owner authorizes cloud resource creation and any repository transfer separately',
                            'Use Serverless plan, spend limit zero, no card or marketplace subscription',
                            'Create exact per-user ACL and final deny-all; remove any broad allow-all rule',
                            'Set secrets privately; never use a GitHub token as API token',
                            'Run launch.py --check, then explicitly start one worker and API',
                            'Port 8000 stays private; use a separately authorized owner-authenticated loopback tunnel',
                            'Verify HTTPS/auth using healthcheck.py only after endpoint-access authorization']}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--device-id',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=plan(args.device_id)
    with args.output.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2);f.write('\n')
    print('Offline plan saved. No credentials, account or network operations.')
if __name__=='__main__':main()
