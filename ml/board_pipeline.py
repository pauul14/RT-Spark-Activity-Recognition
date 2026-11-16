"""Offline RT-Spark recording admission and session-held-out model pipeline.

Never opens UART. Original phone model/data are read-only. Operator attestation
is recorded as a claim; a trace file alone cannot prove the person was walking.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score,balanced_accuracy_score,classification_report,confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from baseline import AXES,FEATURE_NAMES,extract_features

CONTRACT={'sensor_model':'ICM-20608-G','frame_id':'sensor_native_unmapped','calibration_id':None,
          'acceleration_unit':'g','gyro_unit':'rad/s','gravity_handling':'included'}
LABELS=('walking','running')
IDENTIFIER=re.compile(r'^[A-Za-z0-9_-]{1,64}$')
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as stream: json.dump(value,stream,indent=2,allow_nan=False); stream.write('\n')

def import_trace(path,*,session_id,subject,label,placement,start_ms,end_ms,fixture=False,
                 startup_reference=None,startup_reference_sha256=None,capture_sidecar_sha256=None):
    from sensor_evidence import analyze, read_bytes, require, validate_capture
    if label not in LABELS or any(not IDENTIFIER.fullmatch(x) for x in (session_id,subject,placement)):
        raise ValueError('Explicit valid session, subject, placement and walking/running label required')
    require(type(start_ms) is int and type(end_ms) is int and 0<=start_ms<end_ms,
            'Explicit steady-activity device timestamp interval required')
    source=Path(path); data=read_bytes(source)
    require(not source.with_suffix(source.suffix+'.invalid.json').exists(),'Invalidated capture cannot be imported')
    require(len(data)<=16*1024*1024,'Trace exceeds admission size bound')
    sidecar=source.with_suffix(source.suffix+'.json')
    proof_hash=None
    if fixture and not sidecar.exists():
        # Explicit software-only fixtures are never eligible for hardware approval.
        checked=analyze(data,fixture=True,startup_reference=startup_reference,
                        startup_reference_sha256=startup_reference_sha256)
    else:
        checked,proof_hash=validate_capture(source,fixture=fixture,
                                           expected_sidecar_sha256=capture_sidecar_sha256)
        if startup_reference is not None or startup_reference_sha256 is not None:
            ref=checked['reference']
            require(ref is not None and str(Path(startup_reference).resolve())==ref['reference_path']
                    and startup_reference_sha256==ref['reference_sha256'],'Import/reference proof mismatch')
    require(capture_sidecar_sha256 is None or proof_hash==capture_sidecar_sha256,'Changed capture proof')
    samples=[]
    for stamp,seq,center,epoch,values in checked['readings']:
        if start_ms+2000<=stamp<=end_ms-2000:
            samples.append({'t_ms':stamp,'seq':seq,'center_raw_seq':center,'epoch':epoch,**dict(zip(AXES,values))})
    require(len(samples)>=20,'Insufficient steady filtered data after FIR-edge exclusion')
    ident=checked['identity']
    return {'schema_version':1,'session_id':session_id,'subject':subject,'label':label,'placement':placement,
        'device_id':ident['device_id'],'boot_id':ident['boot_id'],'contract':CONTRACT,
        'source_path':str(source.resolve()),'source_sha256':hashlib.sha256(data).hexdigest(),
        'provenance':'synthetic_fixture' if fixture else 'operator_attested_board',
        'hardware_validated':False,'label_start_ms':start_ms,'label_end_ms':end_ms,'samples':samples,
        'sensor_metadata':checked['metadata'],'startup_reference':checked['reference'],
        'capture_sidecar_sha256':proof_hash,'quality_delta':checked['quality_delta']}

def features_for_samples(block):
    if len(block)!=20: raise ValueError('Exactly 20 filtered readings required')
    if any(type(sample['t_ms']) is not int or sample['t_ms']<0 for sample in block): raise ValueError('Invalid device timestamp')
    t=np.asarray([sample['t_ms']-block[0]['t_ms'] for sample in block],dtype=float)
    x=np.asarray([[sample[axis] for axis in AXES] for sample in block],dtype=float)
    if not np.isfinite(x).all() or not all(100<=gap<=300 for gap in np.diff(t)) or not 3700<=t[-1]<=4100:
        raise ValueError('Invalid finite samples or window timing')
    target=np.arange(20)*200
    if t[-1]<target[-1]: target=np.linspace(0,t[-1],20) # bounded tick quantization, no extrapolation
    resampled=np.column_stack([np.interp(target,t,x[:,axis]) for axis in range(6)])
    return extract_features(resampled)

def windows(recording):
    samples=recording['samples']; result=[]; discarded=0
    for offset in range(0,len(samples)-19,5):
        block=samples[offset:offset+20]
        intervals=[b['t_ms']-a['t_ms'] for a,b in zip(block,block[1:])]
        contiguous=all(b['seq']==a['seq']+1 and b['center_raw_seq']==a['center_raw_seq']+10 and b['epoch']==a['epoch'] for a,b in zip(block,block[1:]))
        if not contiguous or not all(100<=gap<=300 for gap in intervals) or not 3700<=block[-1]['t_ms']-block[0]['t_ms']<=4100:
            discarded+=1; continue
        result.append(features_for_samples(block))
    return result,discarded

def train(paths,output,test_sessions,*,allow_fixtures=False):
    output=Path(output)
    if output.exists(): raise ValueError('New model output directory required; never overwrite artifacts')
    records=[json.loads(Path(p).read_text()) for p in paths]
    if not records or len({r['session_id'] for r in records})!=len(records): raise ValueError('Distinct labeled sessions required')
    if any(r['contract']!=CONTRACT for r in records): raise ValueError('Mixed sensor contracts')
    for record in records:
        rebuilt=import_trace(record['source_path'],session_id=record['session_id'],subject=record['subject'],label=record['label'],
            placement=record['placement'],start_ms=record['label_start_ms'],end_ms=record['label_end_ms'],
            fixture=record['provenance']=='synthetic_fixture',
            capture_sidecar_sha256=record.get('capture_sidecar_sha256'),
            startup_reference=record.get('startup_reference',{}).get('reference_path') if record.get('startup_reference') else None,
            startup_reference_sha256=record.get('startup_reference',{}).get('reference_sha256') if record.get('startup_reference') else None)
        if rebuilt!=record: raise ValueError('Recording was altered after import; source and all metadata must match')
    if len({r['placement'] for r in records})!=1: raise ValueError('Separate models required for different placements')
    if any(r['provenance']!='operator_attested_board' for r in records) and not allow_fixtures: raise ValueError('Fixtures cannot train a hardware candidate')
    held=set(test_sessions)
    if not held or not held<{r['session_id'] for r in records}: raise ValueError('Explicit nonempty held-out sessions required')
    train_records=[r for r in records if r['session_id'] not in held]; test_records=[r for r in records if r['session_id'] in held]
    for key in ('source_sha256','boot_id'):
        if {r[key] for r in train_records}&{r[key] for r in test_records}: raise ValueError('Recording/boot leakage across train/test split')
    xtrain=[]; ytrain=[]; xtest=[]; ytest=[]; quality=[]
    for r in records:
        features,discarded=windows(r)
        if not features: raise ValueError('A session has no complete valid windows')
        target_x,target_y=(xtest,ytest) if r['session_id'] in held else (xtrain,ytrain)
        target_x.extend(features); target_y.extend([r['label']]*len(features))
        quality.append({'session_id':r['session_id'],'subject':r['subject'],'boot_id':r['boot_id'],'device_id':r['device_id'],
            'source_sha256':r['source_sha256'],'provenance':r['provenance'],'label':r['label'],
            'windows':len(features),'discarded_windows':discarded,'split':'test' if r['session_id'] in held else 'train'})
    if set(ytrain)!=set(LABELS) or set(ytest)!=set(LABELS): raise ValueError('Both labels required in training AND held-out sessions')
    classifier=make_pipeline(StandardScaler(),LogisticRegression(class_weight='balanced',random_state=42,max_iter=2000))
    with threadpool_limits(limits=1): classifier.fit(xtrain,ytrain); predictions=classifier.predict(xtest)
    session_metrics=[]; cursor=0
    for session in quality:
        if session['split']!='test': continue
        count=session['windows']; observed=predictions[cursor:cursor+count]; cursor+=count
        session_metrics.append({'session_id':session['session_id'],'subject':session['subject'],
            'label':session['label'],'windows':count,'correct':int(np.sum(observed==session['label'])),
            'accuracy':float(np.mean(observed==session['label']))})
    fixture=any(r['provenance']!='operator_attested_board' for r in records)
    evaluation={'schema_version':2,'hardware_validated':False,'hardware_validation_eligible':not fixture,
        'contract':CONTRACT,'placement':records[0]['placement'],'device_ids':sorted({r['device_id'] for r in records}),
        'scope':'SYNTHETIC SOFTWARE TEST ONLY' if fixture else 'Held-out board sessions; operator labels are assertions, not independently observed here',
        'accuracy':accuracy_score(ytest,predictions),'balanced_accuracy':balanced_accuracy_score(ytest,predictions),
        'classification_report':classification_report(ytest,predictions,labels=LABELS,output_dict=True,zero_division=0),
        'confusion_matrix':confusion_matrix(ytest,predictions,labels=LABELS).tolist(),
        'labels':LABELS,'training_windows':len(ytrain),'test_windows':len(ytest),'sessions':quality,
        'held_out_session_metrics':session_metrics,
        'unseen_subject_test':not ({r['subject'] for r in train_records}&{r['subject'] for r in test_records}),
        'limitations':['Overlapping windows within each split are correlated; report session counts, not only window counts.',
                      'Accuracy is specific to recorded people, placement and protocol; phone benchmark is irrelevant.',
                      'No automatic hardware-validation approval; deliberate operator review remains required.']}
    output.mkdir(parents=True)
    joblib.dump({'pipeline':classifier,'contract':CONTRACT,'placement':records[0]['placement'],
                 'device_ids':evaluation['device_ids'],'feature_names':FEATURE_NAMES,'schema_version':2},output/'board-model.joblib')
    evaluation['model_sha256']=sha(output/'board-model.joblib')
    write_json(output/'evaluation.json',evaluation)
    return evaluation

def verify_review_evidence(evaluation):
    if (evaluation.get('schema_version')!=2 or evaluation.get('hardware_validated') is not False or
            evaluation.get('contract')!=CONTRACT or not isinstance(evaluation.get('placement'),str) or
            not IDENTIFIER.fullmatch(evaluation['placement'])):
        raise ValueError('Current board evaluation contract and explicit placement required')
    if evaluation.get('hardware_validation_eligible') is not True:
        raise ValueError('Real board data and explicit reviewer required; fixtures can NEVER be approved')
    sessions=evaluation.get('sessions',[])
    if not sessions or any(r.get('provenance')!='operator_attested_board' for r in sessions):
        raise ValueError('Only operator-attested board sessions are eligible')
    training=[r for r in sessions if r.get('split')=='train']; testing=[r for r in sessions if r.get('split')=='test']
    if set(r.get('label') for r in training)!=set(LABELS) or set(r.get('label') for r in testing)!=set(LABELS):
        raise ValueError('Both labels required on both sides of the review split')
    for key in ('boot_id','source_sha256'):
        if {r.get(key) for r in training}&{r.get(key) for r in testing}: raise ValueError('Held-out recording leakage')
    if (len({r.get('session_id') for r in sessions})!=len(sessions) or
            any(not isinstance(r.get('device_id'),str) or not IDENTIFIER.fullmatch(r['device_id']) for r in sessions) or
            evaluation.get('device_ids')!=sorted({r['device_id'] for r in sessions})):
        raise ValueError('Distinct sessions and evidence-bound device IDs required')
    def finite_probability(value):
        return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1
    if any(not finite_probability(evaluation.get(key)) for key in ('accuracy','balanced_accuracy')):
        raise ValueError('Finite held-out metrics required')
    for r in sessions:
        if (type(r.get('windows')) is not int or r['windows']<1 or
                not isinstance(r.get('boot_id'),str) or not r['boot_id'] or
                not isinstance(r.get('source_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',r['source_sha256'])):
            raise ValueError('Complete per-session evidence required')
    if (evaluation.get('training_windows')!=sum(r['windows'] for r in training) or
            evaluation.get('test_windows')!=sum(r['windows'] for r in testing)):
        raise ValueError('Evaluation support differs from session evidence')
    labels=evaluation['classification_report']
    if evaluation['test_windows']<40 or any(
            not finite_probability(labels[label].get('recall')) or
            labels[label].get('support')!=sum(r['windows'] for r in testing if r['label']==label) or
            labels[label]['support']<20 or labels[label]['recall']<0.8 for label in LABELS):
        raise ValueError('Insufficient held-out support/recall for this prototype review gate')
    metrics=evaluation.get('held_out_session_metrics',[])
    if {r.get('session_id') for r in metrics}!={r['session_id'] for r in testing} or len(metrics)!=len(testing):
        raise ValueError('Every independent held-out session needs its own result')
    for result in metrics:
        source=next(r for r in testing if r['session_id']==result['session_id'])
        if (any(result.get(k)!=source[k] for k in ('subject','label','windows')) or
                type(result.get('correct')) is not int or not 0<=result['correct']<=source['windows'] or
                not finite_probability(result.get('accuracy')) or
                not math.isclose(result['accuracy'],result['correct']/source['windows']) or result['accuracy']<0.8):
            raise ValueError('Each held-out session must meet the prototype recall gate')
    matrix=evaluation.get('confusion_matrix')
    if (not isinstance(matrix,list) or len(matrix)!=2 or any(not isinstance(row,list) or len(row)!=2 for row in matrix)
            or any(type(n) is not int or n<0 for row in matrix for n in row)):
        raise ValueError('Invalid confusion matrix')
    for index,label in enumerate(LABELS):
        correct=sum(r['correct'] for r in metrics if r['label']==label)
        support=labels[label]['support']
        if (sum(matrix[index])!=support or matrix[index][index]!=correct or
                not math.isclose(labels[label]['recall'],correct/support)):
            raise ValueError('Confusion matrix and independent session metrics disagree')
    if (not math.isclose(evaluation['accuracy'],sum(matrix[i][i] for i in range(2))/evaluation['test_windows']) or
            not math.isclose(evaluation['balanced_accuracy'],sum(labels[label]['recall'] for label in LABELS)/2)):
        raise ValueError('Aggregate metrics disagree with held-out evidence')

def approve(folder,reviewer):
    folder=Path(folder); evaluation=json.loads((folder/'evaluation.json').read_text())
    if not reviewer.strip(): raise ValueError('Explicit reviewer required')
    verify_review_evidence(evaluation)
    if evaluation['model_sha256']!=sha(folder/'board-model.joblib'): raise ValueError('Model changed after evaluation')
    approval={'schema_version':1,'approved_for_hardware_inference':True,'reviewer':reviewer,
        'model_sha256':evaluation['model_sha256'],'evaluation_sha256':sha(folder/'evaluation.json'),
        'scope':'Operator-reviewed prototype for the documented placement; not a general accuracy or safety guarantee'}
    write_json(folder/'approval.json',approval)
    return approval

def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('import-trace'); p.add_argument('source',type=Path); p.add_argument('output',type=Path)
    for key in ('session-id','subject','placement'): p.add_argument('--'+key,required=True)
    p.add_argument('--label',choices=LABELS,required=True); p.add_argument('--start-ms',type=int,required=True); p.add_argument('--end-ms',type=int,required=True)
    p.add_argument('--operator-attests-real-board',action='store_true'); p.add_argument('--fixture',action='store_true')
    p.add_argument('--startup-reference',type=Path); p.add_argument('--startup-reference-sha256')
    p=sub.add_parser('train'); p.add_argument('sessions',nargs='+',type=Path); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--test-session',action='append',required=True); p.add_argument('--allow-fixtures',action='store_true')
    p=sub.add_parser('approve'); p.add_argument('folder',type=Path); p.add_argument('--reviewer',required=True)
    args=parser.parse_args()
    if args.command=='import-trace':
        if args.fixture==args.operator_attests_real_board: parser.error('Choose exactly one explicit provenance flag')
        record=import_trace(args.source,session_id=args.session_id,subject=args.subject,label=args.label,placement=args.placement,
            start_ms=args.start_ms,end_ms=args.end_ms,fixture=args.fixture,
            startup_reference=args.startup_reference,startup_reference_sha256=args.startup_reference_sha256)
        write_json(args.output,record); print('Recorded labeled session; hardware_validated=false')
    elif args.command=='train':
        result=train(args.sessions,args.output,args.test_session,allow_fixtures=args.allow_fixtures)
        print(json.dumps({'scope':result['scope'],'hardware_validated':False,'evaluation':str(args.output/'evaluation.json')}))
    else: approve(args.folder,args.reviewer); print('Explicit operator review approval recorded; original phone artifacts unchanged')

if __name__=='__main__': main()
