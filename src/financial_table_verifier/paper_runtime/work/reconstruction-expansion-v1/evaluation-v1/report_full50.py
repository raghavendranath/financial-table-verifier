"""Read-only final50 orchestrator. No default run, transport or billing access.
Requires prospective scientific pins AND an explicit closed300-job inventory.
The latter authorizes final scoring with all unavailable/failure denominators;
it never authorizes retry or reclassification of model responses.
"""
from pathlib import Path
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import datetime,timezone
import argparse,json,fcntl
import source_compiler as compiler
import provenance50 as provenance
import paired_clusters

TAGS={'gemini','llama','mistral'};CONDITIONS={'independent','sequential'}
MODELS={'gemini':'google/gemini-2.5-pro','llama':'meta-llama/llama-4-maverick','mistral':'mistralai/mistral-large-2512'}
def require(ok,message):
 if not ok:raise ValueError(message)
def verified_inputs(input_ref):
 manifest=provenance.bound(input_ref)
 require(manifest.get('schema')=='CSV50-final-evaluation-inputs-v1','Explicit final50 inputs required')
 new=provenance.bound(manifest['new_primary_plan']);old=provenance.bound(manifest['old_primary_plan']);freeze=provenance.bound(new['scientific_freeze']);review=provenance.bound(new['independent_freeze_review'])
 require(new.get('schema')=='CSV50-primary-plan-v1' and new.get('procedure_sha256')==provenance.CORE and freeze.get('procedure_sha256')==provenance.CORE,'Exact unchanged scientific procedure required')
 require(review.get('approved') is True and review.get('scientific_freeze')==new['scientific_freeze'] and review.get('scope')=='CSV50_source_reference_and_scoring_before_primary' and review.get('new_primary_outputs_read') is False,'Prospective independent science review required')
 for path,h in freeze['files_sha256'].items():require(provenance.sha(path)==h,'Frozen science/reference/config file changed')
 for name in ['report_full50.py','provenance50.py','source_compiler.py','structural_binding.py','accounting_screens.py','score_population.py','paired_clusters.py']:
  path=Path(__file__).resolve().parent/name;require(freeze['files_sha256'].get(str(path))==provenance.sha(path),'Final freeze must pin actual evaluation adapter '+name)
 require(freeze.get('registered_configs')=={j['id']:j['config'] for j in new['jobs']},'New240 config registration differs')
 require(freeze['files_sha256'].get(manifest['source_contract']['path'])==manifest['source_contract']['sha256'],'Source contract not fixed in science freeze')
 for field in ('old_primary_plan','old_primary_report','old_canonical'):
  require(freeze['files_sha256'].get(manifest[field]['path'])==manifest[field]['sha256'],'Final freeze must pin immutable legacy dependency '+field)
 canonical=provenance.bound(manifest['old_canonical']);require(manifest['old_canonical']['sha256']=='d61df3304da495b921bdeb60d076c00119acd7b28d08d02ed542ee4aed6c195e','Immutable original10 canonical differs')
 require(canonical['source_reports']['primary']==manifest['old_primary_report'],'Legacy primary report not bound to original canonical')
 require(old.get('procedure_sha256')==provenance.CORE,'Legacy procedure differs')
 require(len(old['jobs'])==60 and len(new['jobs'])==240,'Exact old60/new240 jobs required')
 source=provenance.bound(manifest['source_contract']);selection=provenance.bound(source['selection']);documents={d['document_id']:d for d in selection['documents']}
 require(len(documents)==50 and len({str(int(d['source_CIK'])) for d in documents.values()})==45,'Exact50reports45CIKs required')
 jobs=[]
 for origin,plan in [('old10',old),('new40',new)]:
  for j in plan['jobs']:
   row=deepcopy(j);row['origin']=origin;row['config_ref']=j['config'] if isinstance(j['config'],dict) else {'path':j['config'],'sha256':j['config_sha256']};require(freeze['files_sha256'].get(row['config_ref']['path'])==row['config_ref']['sha256'],'Final freeze must pin every old/new config');data=provenance.bound(row['config_ref']);doc=documents.get(j['document_id']);require(doc is not None,'Job outside fixed50source reports')
   require(j['model_tag'] in TAGS and j['condition'] in CONDITIONS and data['document_id']==j['document_id'] and data['condition']==j['condition'],'Config document/condition differs')
   require(data['model']==MODELS[j['model_tag']],'Config actual model differs from declared arm')
   require(str(int(data['issuer_id']))==str(int(doc['source_CIK'])) and data['selected_pages']==doc['primary_pages'],'Config source CIK/pages differ')
   require(doc['old10'] is (origin=='old10'),'Legacy and new40 groups overlap')
   jobs.append(row)
 counts=Counter((j['document_id'],j['model_tag'],j['condition']) for j in jobs)
 require(len({j['id'] for j in jobs})==300 and set(counts)=={(doc,m,c) for doc in documents for m in TAGS for c in CONDITIONS} and set(counts.values())=={1},'Exact300 unique50x6 arm matrix required')
 require(sum(len(doc['primary_pages']) for doc in documents.values())==170,'Fixed170 source pages required')
 return manifest,jobs,documents

def validate_closed_inventory(closure,jobs):
 require(closure.get('schema')=='CSV50-closed-final-coverage-v1' and closure.get('approved_for_final_scoring') is True and closure.get('paid_calls_in_progress') is False and closure.get('global_inflight')==0,'Explicit closed final inventory required')
 rows=closure.get('jobs',{});require(set(rows)=={j['id'] for j in jobs},'Closed inventory must retain all300 jobs')
 for j in jobs:
  r=rows[j['id']];require(r['config']==j['config_ref'],'Closed inventory config binding differs')
  require(r.get('status') in {'complete','terminal_partial_failure','blocked_preparation'},'Running/pending jobs cannot be final-scored')
  if r['status']=='blocked_preparation':
   evidence=provenance.bound(r['terminal_preparation_receipt'])
   require(evidence.get('status')=='terminal_model_preparation_failure' and evidence.get('document_id')==j['document_id'] and evidence.get('no_response_resampling') is True,'Positive immutable received preparation failure required')
  else:
   result=provenance.bound(r['results']);expected='complete' if r['status']=='complete' else 'failed';require(result.get('status')==expected,'Final owner status differs')
 return rows

def report_counts(group,compiled,documents):
 output={doc:{'document_id':doc,'source_CIK':str(int(meta['source_CIK'])),'populations':{}} for doc,meta in documents.items()}
 for pop,data in compiled['populations'].items():
  targets={t['target_id']:t for t in data['targets']}
  for cell in group['populations'][pop]['cell_results']:
   if not cell['numeric_target']:continue
   row=output[targets[cell['target_id']]['document_id']]['populations'].setdefault(pop,{'correct':0,'total':0});row['total']+=1;row['correct']+=cell['printed_numeric_equal'] is True
 for doc,row in output.items():require(set(row['populations'])=={'BSIS','EPSRE'},'Each report must retain both fixed populations')
 return [output[doc] for doc in sorted(output)]

def paired_results(groups,documents):
 output=[];CIKs={doc:meta['source_CIK'] for doc,meta in documents.items()}
 for model in sorted(TAGS):
  for variant in ('raw','clean'):
   arms={g['condition']:{r['document_id']:r for r in g['per_report_counts']} for g in groups if g['model_tag']==model and g['variant']==variant}
   require(set(arms)==CONDITIONS,'Both fixed paired arms required')
   for pop in ('BSIS','EPSRE'):
    rows=[{'document_id':doc,'independent_correct':arms['independent'][doc]['populations'][pop]['correct'],'independent_total':arms['independent'][doc]['populations'][pop]['total'],'sequential_correct':arms['sequential'][doc]['populations'][pop]['correct'],'sequential_total':arms['sequential'][doc]['populations'][pop]['total']} for doc in sorted(documents)]
    output.append({'model_tag':model,'variant':variant,'population':pop,**paired_clusters.analyze(rows,CIKs,variant=variant)})
 return output

def verify_group_invariance(raw,clean):
 # Row roles never select a target or supply arithmetic/metadata. Therefore
 # all source scores and binding dispositions must be EXACTLY unchanged.
 require(raw['physical_union_bindings']==clean['physical_union_bindings'],'RAW/CLEAN source bindings differ')
 require(raw['populations']==clean['populations'],'RAW/CLEAN numeric/accounting/metadata outputs differ')
 return {'protected_fields_verified_by_actual_response_replay':True,'physical_union_bindings_unchanged':True,'population_numeric_arithmetic_metadata_unchanged':True}

def old10_deltas(groups,old_primary,documents):
 olddocs={doc for doc,m in documents.items() if m['old10']};original={(g['model_tag'],g['condition'],g['variant']):g for g in old_primary['groups']};out=[]
 for g in groups:
  key=(g['model_tag'],g['condition'],g['variant']);before=original[key];now={pop:{'correct':sum(r['populations'][pop]['correct'] for r in g['per_report_counts'] if r['document_id'] in olddocs),'total':sum(r['populations'][pop]['total'] for r in g['per_report_counts'] if r['document_id'] in olddocs)} for pop in ('BSIS','EPSRE')}
  previous={'BSIS':{'correct':before['bs_is']['numeric_transcription']['correct_signed_printed_values'],'total':before['bs_is']['numeric_transcription']['fixed_denominator']},'EPSRE':{'correct':before['eps_re']['printed_numeric_correct'],'total':before['eps_re']['physical_target_count']}}
  require(previous['BSIS']['total']==now['BSIS']['total']==627 and previous['EPSRE']['total']==now['EPSRE']['total']==106,'Legacy10 fixed denominators differ')
  out.append({'model_tag':key[0],'condition':key[1],'variant':key[2],'immutable_original':previous,'same_raw_outputs_uniform_source_association':now,'correct_count_deltas':{p:now[p]['correct']-previous[p]['correct'] for p in previous},'not_new_model_inference':True,'metadata_policy_deltas_reported_in_population_details':True})
 return out

def _score_under_closed_lease(input_ref):
 manifest,jobs,documents=verified_inputs(input_ref);closure=validate_closed_inventory(provenance.bound(manifest['closed_final_coverage']),jobs);compiled,inventory,anomalies=compiler.load_source_contract(manifest['source_contract']);collected=defaultdict(list);diagnostics=[]
 for job in jobs:
  pair=provenance.load_pair(job['config_ref']);cfg=pair['config'];status=closure[job['id']]['status']
  if status=='blocked_preparation':require(all(d['status']=='not_started' for d in pair['diagnostics'].values()) and not (cfg.output/'run.json').exists(),'Blocked preparation cannot hide started inference')
  else:require(provenance.sha(cfg.output/'results.json')==closure[job['id']]['results']['sha256'],'Actual final results differ from closed inventory')
  for variant in ('raw','clean'):
   collected[(job['model_tag'],job['condition'],variant)].extend(pair['records'][variant]);diagnostics.append({'job_id':job['id'],'origin':job['origin'],'closure_status':status,**pair['diagnostics'][variant]})
 groups=[]
 for key,records in sorted(collected.items()):
  require({r['document_id'] for r in records}<=set(documents),'Candidate from outside registered50source documents');result=compiler.score_union(records,compiled,inventory,source_anomalies=anomalies);counts=report_counts(result,compiled,documents);groups.append({'model_tag':key[0],'condition':key[1],'variant':key[2],'candidate_records':len(records),**result,'per_report_counts':counts})
 require(len(groups)==12,'Exact12 RAW/CLEAN arm groups required');invariance=[]
 for m in sorted(TAGS):
  for condition in sorted(CONDITIONS):
   pair={g['variant']:g for g in groups if g['model_tag']==m and g['condition']==condition};invariance.append({'model_tag':m,'condition':condition,**verify_group_invariance(pair['raw'],pair['clean'])})
 old=provenance.bound(manifest['old_primary_report']);deltas=old10_deltas(groups,old,documents)
 # Recheck all actual source/scientific/config fingerprints after computation.
 verified_inputs(input_ref);provenance.bound(manifest['closed_final_coverage'])
 return {'schema':'CSV50-final-primary-report-v1','status':'closed_registered_actual_CSV_replayed','scored_at_utc':datetime.now(timezone.utc).isoformat(),'input_manifest':input_ref,'source_contract':manifest['source_contract'],'planned_reports':50,'source_CIK_clusters':45,'planned_primary_pages':1020,'numeric_populations':{'BSIS':2938,'EPSRE':508,'overlap':64,'unique_numeric':3382,'markers':96},'old10_inference_dates_and_exposure_retained':True,'groups':groups,'paired_condition_effects':paired_results(groups,documents),'raw_clean_invariance':invariance,'old10_scoring_policy_deltas':deltas,'run_provenance':diagnostics,'nested_record_disposition_scope':'Physical union across3478targets, not perpopulation precision or F1','no_new_hypothesis_family_from_CLEAN_or_old10_deltas':True,'structural_HTML_subset_unchanged':{'reports':10,'fragments':22,'physical_numeric_slots':1300},'limitations':['Fixed physical-order/label association is not entity/year proof.','Conditional arithmetic and internal metadata commensurability are not source joint-fact accuracy.','50 convenience reports from45CIKs, prior source exposure and one model run per condition.','Unverifiable metadata remains N/A; source-agent review is not independent-human gold.']}

def score(input_ref):
 manifest=provenance.bound(input_ref);plan=provenance.bound(manifest['new_primary_plan']);registry=provenance.bound(plan['owner_registry']);lock=Path(registry['global_batch_lock'])
 require(lock==provenance.OLD/'.finite-csv-batch.lock','Exact shared finite batch lease required')
 with lock.open('rb') as stream:
  try:fcntl.flock(stream,fcntl.LOCK_SH|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('A paid/recovery batch is active; final scoring refused') from None
  # No actual billing ledger is read. ROOT's hash-bound closed inventory is
  # revalidated while this lease excludes every registered batch/recovery.
  result=_score_under_closed_lease(input_ref)
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--inputs-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():p.error('New report output required; preserve all prior reports')
 result=score({'path':str(a.inputs.resolve()),'sha256':a.inputs_sha256});a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
