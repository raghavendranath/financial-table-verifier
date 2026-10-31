"""Source-free prospective association tests; no model-run/actual source outputs."""
from copy import deepcopy
import unittest
import structural_binding as b

def table(ordinal=0,columns=2):return {'ordinal':ordinal,'table_id':f'p1-t{ordinal}','numeric_columns':columns,'line_range':[1+ordinal*100,99+ordinal*100],'family':'balance_sheet'}
def inventory(tables=None,doc='D',page=1):return {'pages':[{'document_id':doc,'page':page,'complete_for_page':True,'tables':tables or [table()]}]}
def target(ident='T',label='Total assets',*,doc='D',page=1,tab=0,col=0,line=20,count=1,occ=1,indices=None,family='balance_sheet',aliases=None):
 a={'alias':label,'alias_key':b.key(label),'source_numeric_row_indices':indices or [line],'source_numeric_row_label_count':count,'numeric_row_occurrence_1based':occ}
 return {'target_id':ident,'document_id':doc,'source_selector':{'source_text_line_index':line},'csv_selector':{'document_id':doc,'physical_page':page,'source_physical_table_ordinal':tab,'source_table_id':f'p1-t{tab}','table_line_range':[1+tab*100,99+tab*100],'source_numeric_column_ordinal':col,'title_family':family,'alias_occurrence_selectors':aliases or [a],'verified_local_context':None,'excluded_context_keys':[]}}
def record(label='Total assets',*,doc='D',page=1,tab=0,col=0,order=1,title='Consolidated balance sheets',ident=None,raw='WRONG-VALUE'):
 return {'document_id':doc,'page':page,'table_ordinal':tab,'column_index':col,'record_ordinal':order,'row_id':ident or f'p{page}-t{tab}-r{order}','row_label':label,'table_title':title,'statement':'balance_sheet','row_context':[],'preceding_headings':[],'raw_value':raw,'quantity':'unknown','scale':None,'period':None,'currency':None,'scope':None,'roles':{'row_role':'Data','printed_value':raw}}
def bind(records,targets=None,inv=None,**kwargs):return b.bind_targets(records,targets or [target()],inv or inventory(),**kwargs)

class BindingTests(unittest.TestCase):
 def test_unique_binding_never_reads_or_selects_numeric_date_unit_scope(self):
  class Protected(dict):
   def get(self,k,*args):
    if k in {'raw_value','scale','period','currency','quantity','scope','roles'}:raise AssertionError('Forbidden selector input '+k)
    return super().get(k,*args)
  bound,_=bind([Protected(record())]);self.assertEqual(bound['T']['input_index'],0)
  base=record();changed={**base,'raw_value':'EXPECTED-OR-WRONG','period':'2999-01-01','scale':1000000,'currency':'XXX','quantity':'ratio','scope':'different','roles':{'row_role':'Header'}}
  self.assertEqual(bind([base]),bind([changed]))
 def test_unknown_title_family_diagnostic_preserves_unique_transcription(self):
  out,_=bind([record(title='Unrecognized title')]);self.assertEqual(out['T']['status'],'matched_unique');self.assertEqual(out['T']['title_family_diagnostic'],'unknown_emitted_title_family')
 def test_known_conflicting_title_rejects_without_matching_expected_values(self):
  out,_=bind([record(title='Statements of operations',raw='exact-source-answer')]);self.assertIsNone(out['T']['input_index']);self.assertIn('recognized_title_family_conflict',out['T']['issues'])
 def test_physical_table_ordinal_prevents_same_page_same_family_cross_table_selection(self):
  t=target(tab=1,line=120);inv=inventory([table(0),table(1)]);rows=[record(tab=0,raw='CORRECT-ANSWER'),record(tab=1,raw='WRONG-ANSWER')]
  out,_=bind(rows,[t],inv);self.assertEqual(out['T']['input_index'],1)
  out,_=bind(rows[:1],[t],inv);self.assertIsNone(out['T']['input_index'])
 def test_extra_or_missing_later_table_does_not_erase_valid_target0(self):
  inv=inventory([table(0),table(1)]);out,states=bind([record(),record(tab=2)],[target()],inv);self.assertEqual(out['T']['input_index'],0);self.assertEqual(states[1]['status'],'extra_or_unmatched_record')
 def test_incomplete_or_noncontiguous_source_inventory_refuses(self):
  for inv in [{'pages':[{'document_id':'D','page':1,'complete_for_page':False,'tables':[table()]}]},inventory([table(1)])]:
   with self.assertRaises(ValueError):bind([record()],inv=inv)
 def test_nonoperand_table_width_zero_or_unknown_does_not_erase_valid_neighbor(self):
  for width in [0,None]:
   other=table(0);other['numeric_columns']=width;inv=inventory([other,table(1)]);out,_=bind([record(tab=1)],[target(tab=1,line=120)],inv);self.assertEqual(out['T']['input_index'],0)
   with self.assertRaises(ValueError):bind([record()],[target()],inv)
 def test_source_alias_occurrence_must_be_table_local_and_identify_actual_source_row(self):
  for t in [target(count=2,occ=1,indices=[10,20],line=20),target(indices=[120]),target(col=2)]:
   with self.assertRaises(ValueError):bind([record()],[t])
 def test_full_dividend_caption_vs_core_have_separate_source_multiplicity(self):
  full='Common stock dividends, $3.52 per share';core='Common stock dividends';aliases=[{'alias':full,'alias_key':b.key(full),'source_numeric_row_indices':[20],'source_numeric_row_label_count':1,'numeric_row_occurrence_1based':1},{'alias':core,'alias_key':b.key(core),'source_numeric_row_indices':[10,20,30],'source_numeric_row_label_count':3,'numeric_row_occurrence_1based':2}];t=target(label=full,aliases=aliases)
  fullout,_=bind([record(full)],[t]);self.assertEqual(fullout['T']['input_index'],0)
  coreout,_=bind([record(core,order=i+1) for i in range(3)],[t]);self.assertEqual(coreout['T']['input_index'],1)
 def test_present_incompatible_alias_blocks_favorable_singleton_fallback(self):
  t=target(aliases=[{'alias':'Total assets','alias_key':'totalassets','source_numeric_row_indices':[20],'source_numeric_row_label_count':1,'numeric_row_occurrence_1based':1},{'alias':'Assets total','alias_key':'assetstotal','source_numeric_row_indices':[10,20,30],'source_numeric_row_label_count':3,'numeric_row_occurrence_1based':2}]);rows=[record('Total assets'),record('Assets total',order=2)]
  out,_=bind(rows,[t]);self.assertIsNone(out['T']['input_index']);self.assertIn('source_model_alias_multiplicity_mismatch',out['T']['issues'])
 def test_conflicting_aliases_are_ambiguous_and_absent_alias_harmless(self):
  t=target(aliases=[{'alias':x,'alias_key':b.key(x),'source_numeric_row_indices':[20],'source_numeric_row_label_count':1,'numeric_row_occurrence_1based':1} for x in ['Total assets','Assets total']]);rows=[record('Total assets'),record('Assets total',order=2)]
  out,_=bind(rows,[t]);self.assertEqual(out['T']['status'],'ambiguous_alias_alternatives');self.assertEqual(bind(rows[:1],[t])[0]['T']['input_index'],0)
 def test_missing_duplicate_rows_is_ambiguous_even_if_remaining_value_is_correct(self):
  t=target(count=2,occ=2,indices=[10,20]);out,_=bind([record(raw='EXACT-EXPECTED')],[t]);self.assertIsNone(out['T']['input_index'])
 def test_identical_model_amounts_do_not_resolve_duplicates(self):
  out,_=bind([record(order=1,raw='100'),record(order=2,raw='100')]);self.assertIsNone(out['T']['input_index'])
 def test_duplicate_identity_and_duplicate_order_local_failures(self):
  for rows in [[record(),record()],[record(ident='a'),record(ident='b')]]:
   out,states=bind(rows);self.assertIsNone(out['T']['input_index'])
 def test_numeric_column_does_not_shift_to_a_favorable_amount(self):
  rows=[record(col=0,order=1,raw='WRONG'),record(col=1,order=2,raw='CORRECT')];self.assertEqual(bind(rows)[0]['T']['input_index'],0)
 def test_empty_label_header_has_no_numeric_ordinal_but_valid_subtotal_remains(self):
  rows=[record('',col=None,order=1,raw='2023'),record('',col=0,order=2,raw='123')];out,states=bind(rows,[target(label='')]);self.assertEqual(out['T']['input_index'],1);self.assertEqual(states[0]['status'],'unknown_or_invalid_numeric_column_ordinal')
  # If a model incorrectly emits a second empty label in numeric column0,
  # do not use its year-looking digits or Header role to hide the ambiguity.
  rows[0]['column_index']=0;rows[0]['roles']['row_role']='Header';self.assertIsNone(bind(rows,[target(label='')])[0]['T']['input_index'])
 def test_raw_clean_rowrole_only_changes_never_change_bindings(self):
  raw=[record(),record('Section heading',order=2,col=None)];clean=deepcopy(raw)
  for r in clean:r['roles']['row_role']='Header'
  self.assertEqual(bind(raw),bind(clean))
 def test_one_record_cannot_satisfy_two_distinct_source_cells(self):
  out,_=bind([record()],[target('T1'),target('T2')]);self.assertTrue(all(x['input_index'] is None for x in out.values()))
 def test_metadata_unknown_and_malformed_neighbors_do_not_erase_valid_record(self):
  bad=record(order=2);bad['table_ordinal']={};out,states=bind([record(),bad]);self.assertEqual(out['T']['input_index'],0);self.assertEqual(states[1]['status'],'unknown_or_invalid_physical_table_ordinal')
 def test_table_global_multiplicity_precedes_required_context_guard(self):
  t=target(count=2,occ=2,indices=[10,20]);t['csv_selector']['verified_local_context']=['Continuing operations'];rows=[record(order=1),record(order=2)];rows[1]['preceding_headings']=['Continuing operations'];out,_=bind(rows,[t]);self.assertEqual(out['T']['input_index'],1)
 def test_revenue_and_cost_products_use_source_occurrence_then_context_not_prefilter(self):
  first=target('REVENUE','Product',line=10,count=2,occ=1,indices=[10,20]);second=target('COST','Product',line=20,count=2,occ=2,indices=[10,20]);first['csv_selector']['verified_local_context']=['Revenue'];second['csv_selector']['verified_local_context']=['Cost of revenue'];rows=[record('Product',order=1),record('Product',order=2)];rows[0]['row_context']=['Revenue'];rows[1]['row_context']=['Cost of revenue'];out,_=bind(rows,[first,second]);self.assertEqual(out['REVENUE']['input_index'],0);self.assertEqual(out['COST']['input_index'],1)
 def test_monetary_net_income_with_basic_diluted_occurrences_uses_full_source_count(self):
  t=target(label='Net income',line=10,count=3,occ=1,indices=[10,20,30]);t['csv_selector']['excluded_context_keys']=['Basic','Diluted'];rows=[record('Net income',order=i+1) for i in range(3)];rows[1]['row_context']=['Basic'];rows[2]['row_context']=['Diluted'];self.assertEqual(bind(rows,[t])[0]['T']['input_index'],0)
  rows[0]['row_context']=['Basic'];rows[1]['row_context']=[];out,_=bind(rows,[t]);self.assertIsNone(out['T']['input_index']);self.assertIn('source_declared_excluded_context',out['T']['issues'])
 def test_context_does_not_hide_extra_alias_or_rescue_wrong_source_order(self):
  t=target();t['csv_selector']['verified_local_context']=['Revenue'];rows=[record(order=1),record(order=2)];rows[1]['row_context']=['Revenue'];self.assertIsNone(bind(rows,[t])[0]['T']['input_index'])
 def test_exact_three_source_anomalies_allow_only_literal_or_semantic_family(self):
  for page,(semantic,literal) in b.ANOMALY_FAMILIES.items():
   tab=table();t=target(doc=b.ANOMALY_DOCUMENT,page=page,family=semantic);t.update(source_pdf_sha256='PDF-SHA',source_image_sha256='PNG-SHA');inv=inventory(doc=b.ANOMALY_DOCUMENT,page=page);a={'anomalies':[{'document_id':b.ANOMALY_DOCUMENT,'physical_page':page,'semantic_statement_family':semantic,'source_literal_title_family':literal,'allowed_title_families':[semantic,literal],'title_family_accuracy_verifiable':False,'source_pdf_sha256':'PDF-SHA','source_image_sha256':'PNG-SHA','source_physical_table_inventory':{'complete_for_page':True,'tables':[{'ordinal':0,'table_id':tab['table_id'],'source_line_range':tab['line_range'],'numeric_columns':2}]}}]}
   for family in [semantic,literal]:
    r=record(doc=b.ANOMALY_DOCUMENT,page=page,title=family);out,_=bind([r],[t],inv,source_anomalies=a);self.assertEqual(out['T']['status'],'matched_unique');self.assertEqual(out['T']['title_family_diagnostic'],'N/A_conflicting_source_title')
   a['anomalies'][0]['physical_page']=84
   with self.assertRaises(ValueError):bind([r],[t],inv,source_anomalies=a)

if __name__=='__main__':unittest.main()
