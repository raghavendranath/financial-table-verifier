from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
import unittest
import tempfile,fcntl
import report_full50 as r

class FinalReportTests(unittest.TestCase):
 def test_active_or_missing_terminal_coverage_rejected(self):
  job={'id':'J','document_id':'D','config_ref':{'path':'CONFIG','sha256':'SHA'}};base={'schema':'CSV50-closed-final-coverage-v1','approved_for_final_scoring':True,'paid_calls_in_progress':False,'global_inflight':0,'jobs':{'J':{'config':job['config_ref'],'status':'complete','results':{'path':'RESULT','sha256':'R'}}}}
  with patch.object(r.provenance,'bound',return_value={'status':'complete'}):self.assertEqual(r.validate_closed_inventory(base,[job]),base['jobs'])
  for field,value in [('paid_calls_in_progress',True),('global_inflight',1),('approved_for_final_scoring',False),('jobs',{})]:
   bad=deepcopy(base);bad[field]=value
   with self.assertRaises(ValueError):r.validate_closed_inventory(bad,[job])
  bad=deepcopy(base);bad['jobs']['J']['status']='running'
  with self.assertRaises(ValueError):r.validate_closed_inventory(bad,[job])
 def test_preparation_block_requires_positive_bound_terminal_failure(self):
  job={'id':'J','document_id':'D','config_ref':{'path':'CONFIG','sha256':'SHA'}};data={'schema':'CSV50-closed-final-coverage-v1','approved_for_final_scoring':True,'paid_calls_in_progress':False,'global_inflight':0,'jobs':{'J':{'config':job['config_ref'],'status':'blocked_preparation','terminal_preparation_receipt':{'path':'PROOF','sha256':'P'}}}}
  for evidence in [{},{'status':'pending'},{'status':'terminal_model_preparation_failure','document_id':'OTHER','no_response_resampling':True}]:
   with patch.object(r.provenance,'bound',return_value=evidence),self.assertRaises(ValueError):r.validate_closed_inventory(data,[job])
 def test_raw_clean_binding_or_numeric_change_rejects(self):
  raw={'physical_union_bindings':{'P':{'input_index':1}},'populations':{'BSIS':{'correct':1},'EPSRE':{'correct':1}}};self.assertTrue(r.verify_group_invariance(raw,deepcopy(raw))['population_numeric_arithmetic_metadata_unchanged'])
  for field,value in [('physical_union_bindings',{'P':{'input_index':2}}),('populations',{'BSIS':{'correct':2},'EPSRE':{'correct':1}})]:
   bad=deepcopy(raw);bad[field]=value
   with self.assertRaises(ValueError):r.verify_group_invariance(raw,bad)
 def test_cluster_adapter_retains_all50reports_and45CIKs_for_each_raw_clean_population(self):
  docs={f'D{i}':{'source_CIK':i%45+1} for i in range(50)};groups=[]
  for model in sorted(r.TAGS):
   for condition in sorted(r.CONDITIONS):
    for variant in ('raw','clean'):groups.append({'model_tag':model,'condition':condition,'variant':variant,'per_report_counts':[{'document_id':doc,'populations':{p:{'correct':int(condition=='sequential'),'total':1} for p in ['BSIS','EPSRE']}} for doc in docs]})
  calls=[]
  def analyze(rows,ciks,**kw):calls.append((rows,ciks,kw));return {'synthetic':True}
  with patch.object(r.paired_clusters,'analyze',side_effect=analyze):out=r.paired_results(groups,docs)
  self.assertEqual(len(out),12);self.assertEqual(len(calls),12)
  for rows,ciks,kw in calls:self.assertEqual(len(rows),50);self.assertEqual(len(set(ciks.values())),45);self.assertEqual(sum(x['independent_total'] for x in rows),50)
 def test_old10_comparison_is_literal_policy_delta_not_new_inference(self):
  docs={f'D{i}':{'old10':True} for i in range(10)};counts=[]
  for i,doc in enumerate(docs):counts.append({'document_id':doc,'populations':{'BSIS':{'correct':0,'total':63 if i<7 else 62},'EPSRE':{'correct':0,'total':11 if i<6 else 10}}})
  group={'model_tag':'gemini','condition':'independent','variant':'raw','per_report_counts':counts};old={'groups':[{'model_tag':'gemini','condition':'independent','variant':'raw','bs_is':{'numeric_transcription':{'correct_signed_printed_values':1,'fixed_denominator':627}},'eps_re':{'printed_numeric_correct':2,'physical_target_count':106}}]};before=deepcopy(old);delta=r.old10_deltas([group],old,docs)[0]
  self.assertEqual(delta['correct_count_deltas'],{'BSIS':-1,'EPSRE':-2});self.assertTrue(delta['not_new_model_inference']);self.assertEqual(old,before)

class RegistrationTests(unittest.TestCase):
 def fixture(self):
  values={};hashes={}
  def add(name,value,h=None):values[name]=value;hashes[name]=h or ('SYNTHETIC-'+name);return {'path':name,'sha256':hashes[name]}
  old={'procedure_sha256':r.provenance.CORE,'jobs':[]};new={'schema':'CSV50-primary-plan-v1','procedure_sha256':r.provenance.CORE,'jobs':[]};docs=[]
  oldref=add('OLDPLAN',old);newref=add('NEWPLAN',new);primary=add('OLDPRIMARY',{});canonical=add('OLDCANONICAL',{'source_reports':{'primary':primary}},'d61df3304da495b921bdeb60d076c00119acd7b28d08d02ed542ee4aed6c195e')
  for i in range(50):
   pages=list(range(1,(5 if i<2 else 4 if i<18 else 3)+1));doc={'document_id':f'D{i}','source_CIK':i%45+1,'old10':i<10,'primary_pages':pages};docs.append(doc)
   for model in r.TAGS:
    for condition in r.CONDITIONS:
     cfg={'document_id':doc['document_id'],'issuer_id':str(doc['source_CIK']),'condition':condition,'model':r.MODELS[model],'selected_pages':pages};ref=add(f'C{i}-{model}-{condition}',cfg);job={'id':f'{i:02d}-{model}-{condition}','document_id':doc['document_id'],'model_tag':model,'condition':condition,'config':ref}
     if i<10:job.update(config=ref['path'],config_sha256=ref['sha256']);old['jobs'].append(job)
     else:new['jobs'].append(job)
  selection=add('SELECTION',{'documents':docs});source=add('SOURCE',{'selection':selection});freeze={'procedure_sha256':r.provenance.CORE,'files_sha256':dict(hashes),'registered_configs':{j['id']:j['config'] for j in new['jobs']}}
  for name in ['report_full50.py','provenance50.py','source_compiler.py','structural_binding.py','accounting_screens.py','score_population.py','paired_clusters.py']:
   path=str(Path(r.__file__).parent/name);freeze['files_sha256'][path]=r.provenance.sha(path)
  fr=add('FREEZE',freeze);review=add('REVIEW',{'approved':True,'scientific_freeze':fr,'scope':'CSV50_source_reference_and_scoring_before_primary','new_primary_outputs_read':False});new.update(scientific_freeze=fr,independent_freeze_review=review)
  manifest=add('INPUTS',{'schema':'CSV50-final-evaluation-inputs-v1','new_primary_plan':newref,'old_primary_plan':oldref,'old_primary_report':primary,'old_canonical':canonical,'source_contract':source});return manifest,values,hashes
 def invoke(self,manifest,values,hashes):
  actual=r.provenance.sha
  with patch.object(r.provenance,'bound',side_effect=lambda ref:values[ref['path']]),patch.object(r.provenance,'sha',side_effect=lambda path:hashes[str(path)] if str(path) in hashes else actual(path)):
   return r.verified_inputs(manifest)
 def test_exact300configs50reports45CIKs_and170pages_validated_without_output_reads(self):
  manifest,values,hashes=self.fixture();_,jobs,docs=self.invoke(manifest,values,hashes);self.assertEqual(len(jobs),300);self.assertEqual(len(docs),50)
 def test_mutated_model_CIK_configpin_or_missing_arm_rejects(self):
  for mutation in ('model','CIK','pin','arm'):
   manifest,values,hashes=self.fixture();key='C10-gemini-independent'
   if mutation=='model':values[key]['model']='OTHER'
   elif mutation=='CIK':values[key]['issuer_id']='99999'
   elif mutation=='pin':del values['FREEZE']['files_sha256'][key]
   else:values['NEWPLAN']['jobs'].pop()
   with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.invoke(manifest,values,hashes)

class ClosedLeaseTests(unittest.TestCase):
 def test_active_finite_batch_refuses_before_any_output_read_then_closed_lease_allows(self):
  with tempfile.TemporaryDirectory() as td:
   old=Path(td);lock=old/'.finite-csv-batch.lock';lock.touch();lookup={'INPUT':{'new_primary_plan':{'path':'PLAN'}},'PLAN':{'owner_registry':{'path':'REGISTRY'}},'REGISTRY':{'global_batch_lock':str(lock)}}
   with patch.object(r.provenance,'OLD',old),patch.object(r.provenance,'bound',side_effect=lambda ref:lookup[ref['path']]),patch.object(r,'_score_under_closed_lease',return_value={'synthetic':True}) as score:
    with lock.open('rb') as active:
     fcntl.flock(active,fcntl.LOCK_EX|fcntl.LOCK_NB)
     with self.assertRaisesRegex(ValueError,'batch is active'):r.score({'path':'INPUT'})
     score.assert_not_called()
    self.assertEqual(r.score({'path':'INPUT'}),{'synthetic':True});score.assert_called_once()

if __name__=='__main__':unittest.main()
