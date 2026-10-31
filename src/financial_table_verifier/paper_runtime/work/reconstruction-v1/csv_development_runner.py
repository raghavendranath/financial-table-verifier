"""Finite excluded live development, explicitly invoked stages only.

No invocation starts main evaluation. No unknown-outcome auto recovery exists.
"""
from pathlib import Path
import argparse, copy, hashlib, json, sys
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(BASE/'csv-first-v1/src'))
sys.path.insert(0,str(BASE/'development/src'))
from ftv_csv_reconstruction.contracts import CSVConfig
from ftv_csv_reconstruction.artifacts import procedure_hash, default_renderer
from ftv_csv_reconstruction.preparation import prepare_document
from ftv_csv_reconstruction.pipeline import extract_document
from ftv_csv_reconstruction.auxiliary import extract_html
from budget_runtime import SafeRetryTransport

DEST=BASE/'csv-development-v1'
PLAN=DEST/'execution-plan-v3.json'
CONFIGS=DEST/'configs-v3'
PREPARED=DEST/'prepared-v3'
RUNS=DEST/'runs-v3'
HTML=DEST/'html-v3'
HTML_CONFIGS=DEST/'html-configs-v3'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def save(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(d,ensure_ascii=False,indent=2)+'\n').encode()
    if p.exists() and p.read_bytes()!=raw:raise RuntimeError('Versioned artifact changed: '+str(p))
    p.write_bytes(raw)

class StopAfterSource(RuntimeError):pass
class SourceOnlyTransport:
    def __init__(self,inner):self.inner=inner
    def request(self,method,path,payload=None):
        if method!='POST' or payload.get('response_format',{}).get('json_schema',{}).get('name') not in {'document_survey_chunk','document_structure'}:
            raise StopAfterSource('Only full-document source survey and structure are authorized in this wrapper')
        return self.inner.request(method,path,payload)

def synthetic_source_config():
    from ftv_reconstruction.pipeline import ReconstructionConfig
    d=read(BASE/'development-viability-v6-sourcepin/gemini-config.json')
    p=DEST/'synthetic-continuation.pdf';declaration=DEST/'source-only-development-declaration.json'
    d.update(document_id='SYNTHETIC_CSV_CONTINUATION_V1',issuer_id='SYNTHETIC_CSV_CONTINUATION_V1',
             pdf=str(p),pdf_sha256=sha(p),selected_pages=[1,2,3],structural_pages=[1,2,3],accounting_pages=[],
             selected_images={},preparation_illustrative_pages=[1,2,3],development_examples=[],reuse_preparation_from=None,
             prepared_dir=str(DEST/'synthetic-source-preparation'),output=str(DEST/'unused-v6-output'),
             protocol_sha256=sha(declaration),selection_freeze_sha256=sha(declaration))
    config=ReconstructionConfig.model_validate_json(json.dumps(d))
    save(DEST/'synthetic-source-config.json',config.model_dump(mode='json'))
    return config

def prepare_synthetic_source():
    from ftv_reconstruction.pipeline import prepare_document as old_prepare, procedure_hash as old_hash
    if old_hash()!='f255fad79eccfc9f941457a9e1821ff0e326ad8b890cb2319a7ac6c80513be7b':
        raise RuntimeError('Archived source procedure mismatch')
    config=synthetic_source_config()
    if (config.prepared_dir/'survey.json').exists() and (config.prepared_dir/'structure.json').exists():
        return
    try:old_prepare(config,SourceOnlyTransport(SafeRetryTransport(config.prepared_dir)))
    except StopAfterSource:pass
    if not all((config.prepared_dir/n).exists() for n in ['snapshot.json','survey.json','structure.json']):
        raise RuntimeError('Source-only preparation did not complete')
    save(DEST/'synthetic-source-completion.json',{'source_only':True,'obsolete_schema_stage_sent':False,
         'files':{n:sha(config.prepared_dir/n) for n in ['snapshot.json','survey.json','structure.json']}})

def build_configs():
    declaration=DEST/'source-only-development-declaration.json'
    examples=read(DEST/'examples.json')
    frozen=read(BASE/'execution-plan-v6.json')
    models={j['model_tag']:read(j['config']) for j in frozen['jobs'] if j['ordinal']==0 and j['condition']=='independent'}
    clene=read(BASE/'development-viability-v6-sourcepin/gemini-config.json')
    sources=[('clene',clene,BASE/'development-viability-v6-sourcepin/prepared',[3,4]),
             ('synthetic',read(DEST/'synthetic-source-config.json'),DEST/'synthetic-source-preparation',[1,2,3])]
    jobs=[]
    for tag,old,source_prepared,pages in sources:
        images={}
        for page in pages:
            p=DEST/'images'/f'{tag}-{page:03d}.png';p.parent.mkdir(exist_ok=True)
            if not p.exists():p.write_bytes(default_renderer(Path(old['pdf']),page,300,25000000))
            images[page]={'path':str(p),'sha256':sha(p)}
        for model,m in sorted(models.items()):
            for condition in ['independent','sequential']:
                d=dict(experiment_id='csv-first-development-v1',document_id=old['document_id'],issuer_id=old['issuer_id'],
                    pdf=old['pdf'],pdf_sha256=old['pdf_sha256'],selected_pages=pages,selected_images=images,
                    illustrative_pages=pages,examples=[e for e in examples if e['issuer_id']!=old['issuer_id']],
                    condition=condition,model=m['model'],provider=m['provider'],temperature=0.0,seed=42,
                    extraction_reasoning_max_tokens=m['extraction_reasoning_max_tokens'],
                    source_prepared_dir=str(source_prepared),
                    source_snapshot_sha256=sha(source_prepared/'snapshot.json'),
                    source_survey_sha256=sha(source_prepared/'survey.json'),source_structure_sha256=sha(source_prepared/'structure.json'),
                    prepared_dir=str(PREPARED/tag),output=str(RUNS/f'{tag}-{model}-{condition}'),
                    protocol_sha256=sha(declaration),selection_sha256=sha(declaration))
                cfg=CSVConfig.model_validate_json(json.dumps(d)); path=CONFIGS/f'{tag}-{model}-{condition}.json'
                save(path,cfg.model_dump(mode='json'))
                jobs.append({'id':f'{tag}-{model}-{condition}','source_tag':tag,'model_tag':model,'condition':condition,
                             'config':str(path),'config_sha256':sha(path)})
    save(PLAN,{'procedure_sha256':procedure_hash(),'declaration_sha256':sha(declaration),
        'jobs':jobs,'primary_calls':30,'all_attempts_retained':True,'no_main_calls':True})

def run_one(job_id,stage):
    plan=read(PLAN)
    if plan['procedure_sha256']!=procedure_hash():raise RuntimeError('Development procedure changed')
    jobs=[j for j in plan['jobs'] if j['id']==job_id]
    if len(jobs)!=1:raise RuntimeError('Unknown finite development job')
    job=jobs[0]
    if sha(job['config'])!=job['config_sha256']:raise RuntimeError('Development config changed')
    cfg=CSVConfig.model_validate_json(Path(job['config']).read_bytes())
    if stage=='prepare':
        result=prepare_document(cfg,SafeRetryTransport(cfg.prepared_dir))
    elif stage=='csv':
        prepared=read(cfg.prepared_dir/'prepared.json')
        result=extract_document(cfg,prepared,SafeRetryTransport(cfg.output))
    else:
        if job['source_tag']!='synthetic':raise RuntimeError('Only synthetic page2 is registered HTML development')
        hd=cfg.model_dump(mode='json');hd['selected_pages']=[2];hd['output']=str(HTML/job_id)
        html=CSVConfig.model_validate_json(json.dumps(hd))
        save(HTML_CONFIGS/f'{job_id}.json',html.model_dump(mode='json'))
        result=extract_html(html,cfg.output,SafeRetryTransport(html.output))
    print(json.dumps({'job':job_id,'stage':stage,'status':result['status']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['synthetic-source','build','prepare','csv','html']);p.add_argument('--job')
    a=p.parse_args()
    if a.stage=='synthetic-source':prepare_synthetic_source()
    elif a.stage=='build':build_configs()
    elif not a.job:p.error('--job required')
    else:run_one(a.job,a.stage)
