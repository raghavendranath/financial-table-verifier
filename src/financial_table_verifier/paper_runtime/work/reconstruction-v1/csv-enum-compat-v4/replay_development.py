"""Explicit offline DEV-only request-equivalence and exact-response replay.
No network transport exists here; actual old outputs are used once in original
job/page order, never selected by value or request hash. Old trees are read only.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
ACTIVE = BASE / 'csv-first-v1/src'
OLD = BASE / 'csv-first-v3-retired/src'
sys.path.insert(0, str(ACTIVE))
from ftv_csv_reconstruction.artifacts import (digest, file_hash, json_bytes, read_json,
    write_json, replay_call, procedure_hash)
from ftv_csv_reconstruction.contracts import CSVConfig
from ftv_csv_reconstruction.preparation import prepare_document
from ftv_csv_reconstruction.pipeline import extract_document
from ftv_csv_reconstruction.auxiliary import extract_html


def tree(root):
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name != '.lock' and '__pycache__' not in p.parts}


def contract_snapshot(source):
    code = '''import json
from ftv_csv_reconstruction import contracts as c, prompts as p
from ftv_csv_reconstruction.artifacts import procedure_hash
print(json.dumps({'schemas':{n:c.strict_schema(getattr(c,n)) for n in ('SchemaPlan','GeneratedPrompt','PageEnvelope','HTMLEnvelope')},'prompts':{n:getattr(p,n) for n in ('SOURCE_ONLY','SCHEMA','GENERATE_PROMPT','EXTRACT','HTML','ROLE_POLICY')},'procedure':procedure_hash()},sort_keys=True))
'''
    return json.loads(subprocess.check_output([sys.executable, '-B', '-c', code],
        env={'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'}))


class SavedOnlyTransport:
    def __init__(self, origins):
        self.origins = list(map(Path, origins))
        self.index = 0
        self.receipts = []
    def request(self, method, path, payload=None):
        if method != 'POST' or path != 'chat/completions' or self.index >= len(self.origins):
            raise ValueError('NoNetwork: unexpected stage/request; replay stops')
        origin = self.origins[self.index]
        # Replay checks old actual request/response/content hashes before use.
        replay_call(origin, payload)
        request_bytes = (origin / 'request.json').read_bytes()
        if request_bytes != json_bytes(payload) + b'\n':
            raise ValueError('Old/new actual request bytes differ; no cache reuse')
        response = read_json(origin / 'response.json')
        self.receipts.append({'origin':str(origin), 'request_file_sha256':file_hash(origin/'request.json'),
            'request_body_sha256':digest(payload),'response_file_sha256':file_hash(origin/'response.json'),
            'original_call_sha256':file_hash(origin/'call.json'), 'sequence':self.index,
            'new_api_call':False})
        self.index += 1
        return response
    def complete(self):
        if self.index != len(self.origins):
            raise ValueError('Not every original stage was replayed exactly once')
        return self.receipts


def no_renderer(*args):
    raise ValueError('No undeclared image rendering in DEV request proof')


def same_files(old, new, names):
    compared = {}
    for name in names:
        a,b = old/name,new/name
        if a.exists() != b.exists() or (a.exists() and a.read_bytes() != b.read_bytes()):
            raise ValueError('Derived replay differs: '+str(a))
        if a.exists(): compared[name] = file_hash(a)
    return compared


def run():
    dev = BASE/'csv-development-v1'
    output = Path(__file__).resolve().parent
    proof_path = output/'development-wire-proof.json'
    if proof_path.exists() or (dev/'execution-plan-v4.json').exists():
        raise ValueError('New DEV4 proof must start once in empty versioned directories')
    old_contract,new_contract = contract_snapshot(OLD),contract_snapshot(ACTIVE)
    if old_contract['schemas'] != new_contract['schemas'] or old_contract['prompts'] != new_contract['prompts']:
        raise ValueError('Wire schemas or instructions changed; fresh DEV required')
    old_sources = {p.name:p.read_bytes() for p in (OLD/'ftv_csv_reconstruction').glob('*.py')}
    new_sources = {p.name:p.read_bytes() for p in (ACTIVE/'ftv_csv_reconstruction').glob('*.py')}
    if set(old_sources) != set(new_sources): raise ValueError('Unexpected source-set change')
    expected = old_sources['contracts.py'].replace(b'"scale": {"string", "integer_or_blank"}',b'"scale": {"string", "integer_or_blank", "enum_string"}').replace(b'"currency": {"string"}',b'"currency": {"string", "enum_string"}')
    # Compare exact source with only explicitly reviewed two sets removed.
    changed = [name for name in old_sources if old_sources[name] != new_sources[name]]
    if changed != ['contracts.py'] or expected != new_sources['contracts.py']:
        raise ValueError('Source diff exceeds reviewed two ROLE_TYPES sets')
    old_plan_path = dev/'execution-plan-v3.json'
    old_plan = read_json(old_plan_path)
    before = {str(root):tree(root) for root in (dev/'prepared-v3',dev/'runs-v3',dev/'html-v3',dev/'configs-v3',dev/'html-configs-v3')}
    amendment = {'change':'currency/scale textual enum_string post-validation compatibility only',
        'old_procedure_sha256':old_contract['procedure'],'procedure_sha256':procedure_hash(),
        'schemas_sha256':digest(new_contract['schemas']), 'prompts_sha256':digest(new_contract['prompts']),
        'original_development_plan_sha256':file_hash(old_plan_path),
        'no_new_network_or_model_calls':True,'additional_independent_observations':0}
    write_json(output/'development-amendment.json',amendment)
    records=[]; jobs=[]; prepared={}; cfgs={}
    for job in old_plan['jobs']:
        old_path=Path(job['config'])
        if file_hash(old_path)!=job['config_sha256']: raise ValueError('Old config differs')
        value=read_json(old_path)
        value['output']=str(dev/'runs-v4'/job['id'])
        value['prepared_dir']=str(dev/'prepared-v4'/job['source_tag'])
        value['protocol_sha256']=file_hash(output/'development-amendment.json')
        path=dev/'configs-v4'/(job['id']+'.json')
        write_json(path,value)
        config=CSVConfig.model_validate_json(path.read_bytes())
        cfgs[job['id']]=config
        jobs.append({**job,'config':str(path),'config_sha256':file_hash(path)})
        if job['source_tag'] not in prepared:
            old_prepared=Path(read_json(old_path)['prepared_dir'])
            transport=SavedOnlyTransport([old_prepared/'schema',old_prepared/'generated-prompt'])
            prepared[job['source_tag']]=prepare_document(config,transport,renderer=no_renderer)
            records += [{'category':'preparation','source':job['source_tag'],**r} for r in transport.complete()]
            same_files(old_prepared,config.prepared_dir,['generated-schema-plan.json','generated-prompt.json'])
        old_run=Path(read_json(old_path)['output'])
        origins=[old_run/'pages'/f'p{p:04d}'/'call' for p in config.selected_pages]
        transport=SavedOnlyTransport(origins)
        extract_document(config,prepared[job['source_tag']],transport,renderer=no_renderer)
        records += [{'category':'csv','job':job['id'],**r} for r in transport.complete()]
        for p in config.selected_pages:
            names=['raw-envelope.json','raw-parsed.json','clean-envelope.json','clean-parsed.json','cleaning-edits.json','cleaning-audit.json']
            same_files(old_run/'pages'/f'p{p:04d}',config.output/'pages'/f'p{p:04d}',names)
        old_result,new_result=read_json(old_run/'results.json'),read_json(config.output/'results.json')
        for key in ('raw_records','clean_records','attempted_pages','model_response_pages','rejected_pages'):
            if old_result.get(key)!=new_result.get(key): raise ValueError('CSV outcomes differ: '+job['id']+' '+key)
        if [{k:v for k,v in p.items() if k!='directory'} for p in old_result['pages']] != [{k:v for k,v in p.items() if k!='directory'} for p in new_result['pages']]:
            raise ValueError('CSV page outcomes differ except versioned directory')
    for job in old_plan['jobs']:
        if job['source_tag']!='synthetic':continue
        old_path=dev/'html-configs-v3'/(job['id']+'.json')
        value=read_json(old_path);old_run=Path(value['output'])
        value['output']=str(dev/'html-v4'/job['id'])
        value['prepared_dir']=str(dev/'prepared-v4'/job['source_tag'])
        value['protocol_sha256']=file_hash(output/'development-amendment.json')
        path=dev/'html-configs-v4'/(job['id']+'.json');write_json(path,value)
        config=CSVConfig.model_validate_json(path.read_bytes())
        transport=SavedOnlyTransport([old_run/'pages'/f'p{p:04d}'/'call' for p in config.selected_pages])
        extract_html(config,cfgs[job['id']].output,transport,renderer=no_renderer)
        records += [{'category':'html','job':job['id'],**r} for r in transport.complete()]
        for p in config.selected_pages:
            same_files(old_run/'pages'/f'p{p:04d}',config.output/'pages'/f'p{p:04d}',['raw-envelope.json','raw.html'])
    after={str(Path(root)):tree(Path(root)) for root in before}
    if before!=after:raise ValueError('Original development tree changed')
    counts={kind:sum(r['category']==kind for r in records) for kind in ('preparation','csv','html')}
    if counts!={'preparation':4,'csv':30,'html':6}:raise ValueError('Unexpected finite DEV coverage')
    write_json(dev/'execution-plan-v4.json',{**old_plan,'procedure_sha256':procedure_hash(),'jobs':jobs,
        'no_new_api_calls':True,'origin_plan':str(old_plan_path),'origin_plan_sha256':file_hash(old_plan_path)})
    proof={**amendment,'status':'passed','counts':counts,'actual_36_dev_request_bytes_identical':True,
        'all_old_source_trees_unchanged':True,'old_tree_digest':digest(before),'request_response_origins':records,
        'elapsed_times_are_local_replay_not_provider_latency':True,'historical_costs_not_counted_again':True,
        'proof_helper_sha256':file_hash(__file__)}
    write_json(proof_path,proof)
    print(json.dumps({'status':'passed','procedure_sha256':procedure_hash(),'counts':counts,
        'proof':str(proof_path),'proof_sha256':file_hash(proof_path)},indent=2))

if __name__=='__main__':run()
