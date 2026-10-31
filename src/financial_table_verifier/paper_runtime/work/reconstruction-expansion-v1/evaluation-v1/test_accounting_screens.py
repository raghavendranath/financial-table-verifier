"""Synthetic accounting examples, never actual report/model outcomes."""
from copy import deepcopy
from decimal import Decimal
import unittest
import accounting_screens as a
import score_population as p
from test_structural_binding import target,record,inventory

def printed(value):return {'parsed_numeric':True,'raw_value':str(value),'numeric_value_printed':str(value),'rounding_radius_printed':'.5'}
def rows(values,*,period='2023-01-01/2023-12-31',scales=None):
 return [{'raw_value':str(v),'row_label':'Item','period':period,'scale':(scales or [1]*len(values))[i],'quantity':'money','currency':'USD','scope':'Same entity consolidated'} for i,v in enumerate(values)]
def sources(values):return {f'T{i}':printed(v) for i,v in enumerate(values)}
def bindings(values):return {f'T{i}':{'input_index':i,'status':'matched_unique'} for i in range(len(values))}
def bs():return {'case_id':'B','document_id':'D','family':'BS','endpoint':'primary','source_applicable':True,'numeric_eligible':True,'lhs_target_id':'T0','terms':[{'target_id':'T1','coefficient':1},{'target_id':'T2','coefficient':1}]}
def eps():return {'case_id':'E','document_id':'D','family':'EPS','endpoint':'primary','source_applicable':True,'numeric_eligible':True,'numerator_target_id':'T0','shares_target_id':'T1','EPS_target_id':'T2','monetary_multiplier':1,'share_multiplier':1}
def retained():return {'case_id':'R','document_id':'D','family':'RE','endpoint':'primary','source_applicable':True,'numeric_eligible':True,'begin_target_id':'T1','end_target_id':'T0','terms':[{'target_id':'T2','coefficient':1},{'target_id':'T3','coefficient':-1},{'target_id':'T4','coefficient':-1}],'source_complete_bridge':True,'completeness_evidence':'Synthetic complete parent RE column: beginning+income-dividends-other movement=end','dividend_policy':'reported'}

class ArithmeticTests(unittest.TestCase):
 def test_balance_precision_and_separate_percentage_sensitivity(self):
  out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),rows([100,40,60],period='2023-12-31'));self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'same_emitted_context');self.assertEqual(out['original_BS_0_5_percent_sensitivity']['status'],'pass')
  out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),rows([101,40,60],period='2023-12-31'));self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['original_BS_0_5_percent_sensitivity']['status'],'fail')
 def test_correct_transcription_can_fail_source_arithmetic_and_wrong_values_can_pass(self):
  out=a.score_case(bs(),sources([100,40,70]),bindings([1,2,3]),rows([100,40,70]));self.assertEqual(out['status'],'arithmetic_fail')
  out=a.score_case(bs(),sources([100,40,70]),bindings([1,2,3]),rows([200,80,120]));self.assertEqual(out['status'],'arithmetic_pass')
 def test_metadata_unknown_or_mixed_units_never_changes_conditional_arithmetic(self):
  for field,value in [('scope','unknown'),('currency','XXX'),('scale',None),('period',None),('quantity','unknown')]:
   r=rows([100,40,60]);r[0][field]=value;out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'skipped')
 def test_model_mixed_scales_and_mixed_periods_cannot_be_gold_filled(self):
  for r in [rows([100,40,60],scales=[1000,1,1000]),rows([100,40,60])]:
   if r[0]['scale']==1:r[0]['period']='2022-01-01/2022-12-31'
   out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),r);self.assertEqual(out['actual_model_metadata']['status'],'skipped');self.assertEqual(out['status'],'arithmetic_pass')
 def test_rounded_EPS_interval_propagates_numerator_and_share_precision(self):
  t=sources([1,3,'0.33']);t['T2']['rounding_radius_printed']='.005';r=rows([1,3,'0.33']);r[1].update(quantity='shares',currency=None);r[2].update(quantity='per_share')
  out=a.score_case(eps(),t,bindings([1,2,3]),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'arithmetic_pass');self.assertTrue(out['source_printed_precision_not_point_tolerance'])
 def test_EPS_missing_source_units_remains_source_unavailable_with_all_numeric_targets(self):
  c=eps();c.update(numeric_eligible=False,monetary_multiplier=None,ineligible_reason='Unknown source global/display unit basis');out=a.score_case(c,sources([1,3,'0.33']),bindings([1,2,3]),rows([1,3,'0.33']));self.assertEqual(out['status'],'source_arithmetic_unavailable')
  c['numeric_eligible']=True
  with self.assertRaises(ValueError):a.score_case(c,sources([1,3,'0.33']),bindings([1,2,3]),rows([1,3,'0.33']))
 def test_nonpositive_predicted_shares_explicitly_unavailable_not_source_exclusion(self):
  out=a.quotient([Decimal(1),Decimal(0),Decimal(0)],[Decimal('.5')]*3,1,1);self.assertEqual(out['status'],'unavailable_nonpositive_or_zero_crossing_shares')
 def test_share_currency_none_is_NA_but_wrong_currency_prefix_conflicts(self):
  t=sources([1,3,'0.33']);t['T2']['rounding_radius_printed']='.005';r=rows([1,3,'0.33']);r[1].update(quantity='shares',currency=None);r[2].update(quantity='per_share');self.assertEqual(a.score_case(eps(),t,bindings(r),r)['actual_model_metadata']['status'],'arithmetic_pass')
  r[0]['raw_value']='EUR1';out=a.score_case(eps(),t,bindings(r),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'skipped')
 def test_RE_actual_Q2_balance_dates_and_every_movement_required(self):
  # Synthetic parent retained-earnings bridge with correct Q2, not H1, dates.
  values=[40290,47966,-6841,828,7];r=rows(values,period='2023-04-01/2023-06-30');r[0]['row_label']='Balance June 30, 2023';r[1]['row_label']='Balance March 31, 2023';out=a.score_case(retained(),sources(values),bindings(values),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'arithmetic_pass')
  r[1]['row_label']='Balance December 31, 2022';out=a.score_case(retained(),sources(values),bindings(values),r);self.assertEqual(out['actual_model_metadata']['status'],'skipped')
 def test_RE_missing_other_movement_or_absence_attestation_refuses(self):
  for change in [{'source_complete_bridge':False},{'dividend_policy':'unknown'},{'dividend_policy':'source_verified_absent','source_verified_absent_attestation':None}]:
   c={**retained(),**change}
   with self.assertRaises(ValueError):a.score_case(c,sources([40290,47966,-6841,828,7]),bindings([1]*5),rows([1]*5))
 def test_own_balance_date_missing_year_is_unknown_no_expected_year_inference(self):
  self.assertIsNone(a.actual_period({'row_label':'Balance June 30','period':'2023'},balance=True));self.assertIsNone(a.actual_period({'row_label':'Balance2023-02-30','period':'unknown'},balance=True))
 def test_explicit_arbitrary_duration_remains_duration_not_instant(self):
  x=a.actual_period({'period':'2023-01-02/2023-12-31'});self.assertEqual(x['kind'],'duration');self.assertEqual(x['start'],'2023-01-02')
 def test_BS_duration_and_IS_instant_are_metadata_conflicts_only(self):
  out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),rows([100,40,60]));self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['reason'],'model_period_kind_conflicts_with_financial_family')
  c=bs();c['family']='IS';out=a.score_case(c,sources([100,40,60]),bindings([1,2,3]),rows([100,40,60],period='2023-12-31'));self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['reason'],'model_period_kind_conflicts_with_financial_family')
 def test_actual_full_date_header_conflict_skips_metadata_not_conditional(self):
  t=sources([1,3,'0.33']);t['T2']['rounding_radius_printed']='.005';r=rows([1,3,'0.33']);r[1].update(quantity='shares',currency=None);r[2].update(quantity='per_share')
  for x in r:x['column_label']='Year ended December 31, 2022'
  out=a.score_case(eps(),t,bindings(r),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['status'],'skipped');self.assertIn('header_full_date_conflict',out['actual_model_metadata']['reason'])
 def test_fiscal_year_name_does_not_override_matching_actual_full_end_date(self):
  r={'period':'2023-02-01/2024-01-31','column_label':'Fiscal year 2023 ended January 31, 2024'};self.assertIsNone(a.header_date_conflict(r,a.actual_period(r)))
 def test_balance_row_date_precedence_over_generic_full_header_preserved(self):
  r={'row_label':'Balance March 31, 2023','period':'2023-04-01/2023-06-30','column_label':'Three months ended June 30, 2023'};self.assertIsNone(a.header_date_conflict(r,a.actual_period(r,balance=True)))
 def test_RE_contained_subperiod_or_point_timeline_unassessed_not_model_error(self):
  values=[40290,47966,-6841,828,7];r=rows(values,period='2023-04-01/2023-06-30');r[0]['row_label']='Balance June 30, 2023';r[1]['row_label']='Balance March 31, 2023';r[2]['period']='2023-04-01/2023-04-30';out=a.score_case(retained(),sources(values),bindings(values),r);self.assertEqual(out['status'],'arithmetic_pass');self.assertEqual(out['actual_model_metadata']['reason'],'unsupported_RE_subperiod_timeline')
  r[2]['period']='2022-04-01/2022-04-30';out=a.score_case(retained(),sources(values),bindings(values),r);self.assertIn('outside_own_balance_dates',out['actual_model_metadata']['reason'])
 def test_invalid_explicit_balance_date_cannot_borrow_valid_column_date(self):
  self.assertIsNone(a.actual_period({'row_label':'Balance February 30, 2023','period':'2023-06-30'},balance=True))
  actual=a.actual_period({'row_label':'Balance March 31, 2023','period':'2023-06-30'},balance=True);self.assertEqual(actual['end'],'2023-03-31');self.assertEqual(actual['origin'],'own_emitted_balance_row_date')
 def test_unparsed_amount_footnote_missing_not_zero(self):
  for value in [None,'','123(1)','10 20','not available']:
   r=rows([100,40,60]);r[0]['raw_value']=value;out=a.score_case(bs(),sources([100,40,60]),bindings([1,2,3]),r);self.assertEqual(out['status'],'unavailable_unparsed_operand')
 def test_missing_or_ambiguous_operand_does_not_become_source_ineligible(self):
  bb=bindings([1,2,3]);bb['T0']['input_index']=None;out=a.score_case(bs(),sources([100,40,60]),bb,rows([100,40,60]));self.assertEqual(out['status'],'unavailable_missing_or_ambiguous_operand');self.assertTrue(out['source_numeric_eligible'])
 def test_duplicate_source_movements_or_components_cannot_double_count(self):
  c=bs();c['terms'][1]['target_id']='T1'
  with self.assertRaises(ValueError):a.score_case(c,sources([100,40,60]),bindings([1,2,3]),rows([100,40,60]))

class PopulationTests(unittest.TestCase):
 def data(self,values):
  labels=['Total assets','Total liabilities','Total equity'];ts=[];rs=[]
  for i,(label,value) in enumerate(zip(labels,values)):
   t=target(f'T{i}',label,line=20+i);t.update(printed(value),metadata={'quantity':'monetary','scale':None,'period':{'end':'2023-12-31','duration_months':None}},metadata_verifiability={'quantity_verifiable':True,'printed_scale_verifiable':False,'period_end_verifiable':True});ts.append(t);r=record(label,order=i+1,raw=str(value));r.update(quantity='money',period='2023-12-31');rs.append(r)
  return {'targets':ts,'cases':[bs()]},rs
 def test_all_fixed_missing_targets_stay_denominator_and_null_metadata_unavailable(self):
  d,r=self.data([100,40,60]);out=p.score_population([],d,inventory());self.assertEqual(out['numeric_transcription']['fixed_denominator'],3);self.assertEqual(out['numeric_transcription']['correct_signed_printed_values'],0);self.assertEqual(out['metadata_agreement_fixed_numeric_population']['printed_scale'],{'N/A_unverifiable':3})
 def test_compensating_wrong_values_cross_tab_does_not_claim_detector_accuracy(self):
  d,r=self.data([100,40,60]);r[0]['raw_value']='200';r[1]['raw_value']='80';r[2]['raw_value']='120';out=p.score_population(r,d,inventory());self.assertEqual(out['numeric_transcription']['correct_signed_printed_values'],0);self.assertEqual(out['case_results'][0]['status'],'arithmetic_pass');self.assertEqual(out['consistency_vs_operand_values'],[{'operand_correctness':'at_least_one_operand_incorrect','conditional_arithmetic':'arithmetic_pass','count':1}])
 def test_exact_correct_source_values_can_fail_arithmetic(self):
  d,r=self.data([100,40,70]);out=p.score_population(r,d,inventory());self.assertEqual(out['numeric_transcription']['correct_signed_printed_values'],3);self.assertEqual(out['case_results'][0]['status'],'arithmetic_fail');self.assertEqual(out['consistency_vs_operand_values'][0]['operand_correctness'],'all_operands_correct')
 def test_raw_clean_only_rowrole_changes_produce_exactly_equal_scores(self):
  d,r=self.data([100,40,60]);clean=deepcopy(r)
  for row in clean:row['roles']['row_role']='Header'
  self.assertEqual(p.score_population(r,d,inventory()),p.score_population(clean,d,inventory()))
 def test_source_marker_dash_is_not_assumed_zero(self):
  d,r=self.data([100,40,60]);d['targets'][0].update(parsed_numeric=False,raw_value='—',numeric_value_printed=None);d['cases'][0].update(numeric_eligible=False,ineligible_reasons=['source dash']);r[0]['raw_value']='0';out=p.score_population(r,d,inventory());self.assertEqual(out['numeric_transcription']['fixed_denominator'],2);self.assertEqual(out['markers']['fixed_denominator'],1);self.assertEqual(out['markers']['exact_raw_matches'],0);self.assertEqual(out['case_results'][0]['status'],'source_arithmetic_unavailable')
 def test_nonverifiable_currency_and_exact_start_remain_NA(self):
  d,r=self.data([100,40,60]);out=p.score_population(r,d,inventory());self.assertEqual(out['metadata_agreement_fixed_numeric_population']['ISO_currency'],{'N/A_unverifiable':3});self.assertEqual(out['metadata_agreement_fixed_numeric_population']['exact_start'],{'N/A_unverifiable':3})

if __name__=='__main__':unittest.main()
