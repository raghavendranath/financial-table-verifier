"""Read-only adapter to the unchanged actual request/response CSV replay loader.
No API/transport/key/billing imports or source expected-value inputs.
Caller must bind each config to the final registered scientific freeze.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,importlib.util,json,sys
OLD=Path(__file__).resolve().parents[2]/'reconstruction-v1'
sys.path[:0]=[str(OLD/'csv-first-v1/src'),str(OLD/'csv-evaluation-v1')]
from ftv_csv_reconstruction.artifacts import procedure_hash,read_json
from ftv_csv_reconstruction.contracts import CSVConfig
from ftv_csv_reconstruction.pipeline import load_preparation
CORE='59d762e497c652a2bc0cc770fa6a463bfe25ea86080fffec99f466b8e375cac4'
LOADER=OLD/'csv-evaluation-v1/provenance.py'
LOADER_SHA='22ad38b23f8ab38dd8050dfbedc6e1fbebf30e26df69f9c7c4da7f58c3871161'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def bound(ref):
 raw=Path(ref['path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Registered config/dependency bytes changed')
 return json.loads(raw)
def old_loader():
 if sha(LOADER)!=LOADER_SHA or procedure_hash()!=CORE:raise ValueError('Exact unchanged CSV core/provenance loader required')
 spec=importlib.util.spec_from_file_location('_CSV50_frozen_request_replay',LOADER);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def protected_records(records,role_indices):
 output=[]
 for record in records:
  row=deepcopy(record);index=role_indices[row['schema_id']]
  fields=row.pop('raw_fields');roles=row.pop('roles')
  if not 0<=index<len(fields) or fields[index]!=roles['row_role']:raise ValueError('Actual generated row-role field position differs')
  row['raw_fields_except_actual_row_role']=fields[:index]+fields[index+1:];row['roles_except_row_role']={k:v for k,v in roles.items() if k!='row_role'};output.append(row)
 return output

def load_pair(config_ref):
 data=bound(config_ref);config=CSVConfig.model_validate_json(json.dumps(data));module=old_loader()
 # Every registered selected PNG must exist and be pinned. Thus the unchanged
 # replay loader cannot fall back to native rendering from host PDF libraries.
 if not set(config.selected_pages)<=set(config.selected_images):raise ValueError('Every selected page requires a pinned source image for read-only replay')
 pairs={};diagnostics={}
 for variant in ('raw','clean'):
  records,diag=module.load_candidates(config_ref['path'],variant)
  if diag['config_sha256']!=config_ref['sha256'] or diag['procedure_sha256']!=CORE:raise ValueError('Config/procedure changed during actual payload replay')
  pairs[variant]=records;diagnostics[variant]=diag
 if (config.output/'run.json').exists():
  plan,_=load_preparation(config,read_json(config.prepared_dir/'prepared.json'));indices={a.schema_id:next(i for i,c in enumerate(a.columns) if c.role=='row_role') for a in plan.archetypes}
  if protected_records(pairs['raw'],indices)!=protected_records(pairs['clean'],indices):raise ValueError('RAW/CLEAN protected fields or actual candidate identity changed')
 elif pairs['raw'] or pairs['clean']:raise ValueError('Candidates without actual run provenance')
 if sha(config_ref['path'])!=config_ref['sha256']:raise ValueError('Config changed after payload replay')
 return {'config':config,'records':pairs,'diagnostics':diagnostics,'raw_clean_protected_fields_unchanged':True,'exact_frozen_provenance_loader_sha256':LOADER_SHA,'source_references_not_used_in_candidate_loading':True}
