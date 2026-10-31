from copy import deepcopy
from pathlib import Path
from collections import Counter
import json,unittest
import source_compiler as c
import score_population as p
from test_structural_binding import target,record,inventory

def fixture():
 first=target('BS');first.update(raw_value='$ 100',parsed_numeric=True,numeric_value_printed='100',rounding_radius_printed='0.5',metadata={},metadata_verifiability={})
 second=deepcopy(first);second.update(target_id='EPS',raw_value='100')
 source={'A':{'BSIS':{'targets':[first],'candidates':[]},'EPSRE':{'targets':[second],'cases':[]}}}
 physical={'physical_cells':[{'physical_id':'PHYSICAL','parsed_numeric':True,'canonical_csv_selector':deepcopy(first['csv_selector']),'members':[{'partition':'A','population':pop,'target_id':t['target_id'],'original_raw_value':t['raw_value'],'original_selector':deepcopy(t['csv_selector'])} for pop,t in [('BSIS',first),('EPSRE',second)]]}]}
 return source,physical

class CompilerTests(unittest.TestCase):
 def test_structural_shared_cell_binds_once_then_projects_both_population_ids(self):
  source,m=fixture();compiled=c.compile_sources(source,m);self.assertEqual(len(compiled['physical_targets']),1)
  out=c.score_union([record(raw='100')],compiled,inventory())
  for pop in ('BSIS','EPSRE'):
   score=out['populations'][pop];self.assertEqual(score['numeric_transcription']['correct_signed_printed_values'],1);self.assertEqual(score['cell_results'][0]['physical_union_target_id'],'PHYSICAL')
  self.assertEqual(len(out['physical_union_bindings']),1)
 def test_distinct_source_physical_cells_still_cannot_reuse_one_model_record(self):
  source,m=fixture();split=deepcopy(m['physical_cells'][0]);split['physical_id']='OTHER';split['members']=[split['members'].pop()];m['physical_cells'][0]['members']=m['physical_cells'][0]['members'][:1];m['physical_cells'].append(split)
  out=c.score_union([record(raw='100')],c.compile_sources(source,m),inventory());self.assertTrue(all(v['input_index'] is None for v in out['physical_union_bindings'].values()))
 def test_source_identity_does_not_join_equal_values_and_preserved_payloads_required(self):
  for field,value in [('original_raw_value','999'),('original_selector',{})]:
   source,m=fixture();m['physical_cells'][0]['members'][0][field]=value
   with self.assertRaises(ValueError):c.compile_sources(source,m)
 def test_nonbool_numeric_flag_is_not_silently_marker(self):
  source,m=fixture();source['A']['BSIS']['targets'][0]['parsed_numeric']=1
  with self.assertRaises(ValueError):c.compile_sources(source,m)
 def test_legacy_currency_formatted_precision_preserves_unknown_metadata(self):
  t={'target_id':'OLD','raw_value':'$ (1.20)','legacy_facts':[{'scale':1000,'scope':'inferred'}]};actual=c.legacy_numeric_target(t)
  self.assertEqual(actual['raw_value'],t['raw_value']);self.assertEqual(actual['rounding_radius_printed'],'0.005');self.assertEqual(actual['numeric_value_printed'],'-1.20');self.assertEqual(actual['metadata_verifiability'],{});self.assertEqual(actual['legacy_facts'],t['legacy_facts'])
 def test_actual_source_contract_compiles_counts_without_reading_any_predictions(self):
  base=Path(__file__).resolve().parents[1]/'references/aggregate-source-v1';manifest=json.loads((base/'source-contract-manifest-v1.json').read_bytes());sources={part:{pop:json.loads(Path(ref['path']).read_bytes()) for pop,ref in data.items() if pop in {'BSIS','EPSRE'}} for part,data in manifest['source_populations'].items()};compiled=c.compile_sources(sources,json.loads(Path(manifest['physical_map']['path']).read_bytes()))
  self.assertEqual(len(compiled['physical_targets']),3478);self.assertEqual(Counter(t['parsed_numeric'] for t in compiled['physical_targets']),{True:3382,False:96});self.assertEqual(len(compiled['populations']['BSIS']['targets']),3034);self.assertEqual(len(compiled['populations']['EPSRE']['targets']),508)
  cases=[x for pop in compiled['populations'].values() for x in pop['cases']];self.assertEqual(Counter((x['family'],x['endpoint']) for x in cases),{('BS','primary'):102,('IS','primary'):818,('IS','auxiliary'):60,('EPS','primary'):100,('EPS','quarterly_supplement'):38,('RE','primary'):50})

class FiscalMetadataTests(unittest.TestCase):
 def test_bare_fiscal_year_name_no_calendar_endpoint_conflict(self):
  r={'period':'2022-01-30/2023-01-28','column_label':'Fiscal year 2022'};before=deepcopy(r);d=p.emitted_dates_without_fiscal_name_inference(r);self.assertEqual(d['end'],'2023-01-28');self.assertNotIn('year',d['conflicts']);self.assertEqual(r,before)
 def test_full_explicit_dates_not_erased_even_beside_fiscal_name(self):
  for header in ['Fiscal year 2022 ended December 31, 2022','Fiscal year 2022-12-31','FY2022; 2022-12-31']:
   d=p.emitted_dates_without_fiscal_name_inference({'period':'2022-01-30/2023-01-28','column_label':header});self.assertTrue(d['conflicts'],header)
 def test_raw_clean_unrelated_roles_identical_fiscal_date_projection(self):
  a={'period':'2022-01-30/2023-01-28','column_label':'FY2022','roles':{'row_role':'Data'}};b=deepcopy(a);b['roles']['row_role']='Header';self.assertEqual(p.emitted_dates_without_fiscal_name_inference(a),p.emitted_dates_without_fiscal_name_inference(b))
 def test_period_bare_fiscal_name_does_not_invent_or_conflict_with_header_date(self):
  r={'period':'FY2022','column_label':'January 28, 2023'};before=deepcopy(r);d=p.emitted_dates_without_fiscal_name_inference(r);self.assertNotIn('year',d['conflicts']);self.assertEqual(r,before)
  self.assertIsNone(c.screens.actual_period(r)) # Actual-context remains unknown.

if __name__=='__main__':unittest.main()
