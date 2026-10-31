"""Uniform50 population projection. No files/models/network or candidate selection
by any reference number/period/unit. Production caller must verify frozen inputs
and reconstruct actual requests/responses with the unchanged provenance loader.
"""
from collections import Counter,defaultdict
from decimal import Decimal
import re
from pathlib import Path
import sys
import structural_binding as binding
import accounting_screens as screens
OLD=Path(__file__).resolve().parents[2]/'reconstruction-v1'
sys.path.insert(0,str(OLD/'csv-evaluation-v1'))
from numeric_format import numeric,project_number
from score_bs_is import emitted_date_context

DIMENSIONS=('quantity','printed_scale','period_end','printed_year','duration_heading_months','exact_start','ISO_currency','consolidation_scope','literal_currency_symbol','title_family')

def emitted_dates_without_fiscal_name_inference(record):
 """A bare fiscal-year NAME is not a calendar endpoint assertion.
 Keep all actual periods/ISO/full English dates. Only an explicitly named
 fiscal-year token is omitted from the old helper's calendar-year inference.
 """
 copy=dict(record)
 for field in ('period','column_label'):
  label=copy.get(field)
  if isinstance(label,str):copy[field]=re.sub(r'\b(fiscal\s+year|FY)\s*(?:19|20)\d{2}\b(?!-\d{2}-\d{2})',r'\1',label,flags=re.I)
 return emitted_date_context(copy)

def metadata_agreement(target,record,title_diagnostic):
 flags=target.get('metadata_verifiability',{});m=target.get('metadata',{});result={};period=emitted_dates_without_fiscal_name_inference(record);source_period=m.get('period') or {};q={'monetary':'money','money':'money','shares':'shares','per_share':'per_share','ratio':'ratio'}.get(m.get('quantity'))
 def compare(name,flag,actual,expected):
  if flags.get(flag) is not True:result[name]='N/A_unverifiable';return
  if expected is None:raise ValueError('Source-verifiable metadata has no expected value: '+name)
  result[name]='unknown_emitted' if actual is None else 'agree' if actual==expected else 'disagree'
 actualq=record.get('quantity');compare('quantity','quantity_verifiable',actualq if not screens.placeholder(actualq) else None,q)
 compare('printed_scale','printed_scale_verifiable',record.get('scale'),m.get('scale'))
 compare('period_end','period_end_verifiable',period['end'],source_period.get('end'))
 end=source_period.get('end');compare('printed_year','period_end_verifiable',period['year'],int(end[:4]) if end else None)
 compare('duration_heading_months','duration_heading_verifiable',period['duration_months'],source_period.get('duration_months'))
 explicit=screens.actual_period(record);compare('exact_start','exact_start_verifiable',explicit.get('start') if explicit else None,source_period.get('start') or m.get('exact_start'))
 actualcur=record.get('currency');compare('ISO_currency','ISO_currency_verifiable',actualcur if not screens.placeholder(actualcur) else None,m.get('currency_iso'))
 scope=record.get('scope');actualconsolidation=True if isinstance(scope,str) and 'consolidat' in scope.casefold() else False if isinstance(scope,str) and ('standalone' in scope.casefold() or 'parentonly' in screens.norm(scope)) else None
 expectedconsolidation=True if 'consolidat' in str(m.get('scope','')).casefold() else None
 compare('consolidation_scope','scope_consolidation_verifiable',actualconsolidation,expectedconsolidation)
 token=project_number(record.get('raw_value'))['currency_token'];compare('literal_currency_symbol','currency_symbol_verifiable',token,m.get('currency_symbol'))
 for field,conflict in [('period_end','end'),('printed_year','year'),('duration_heading_months','duration_months')]:
  if result[field]!='N/A_unverifiable' and (conflict in period['conflicts'] or any(x.startswith('invalid_actual_') for x in period['conflicts'])):result[field]='conflicting_actual_metadata'
 result['title_family']=title_diagnostic;result['actual_period_header_conflicts']=period['conflicts'];result['entity_attribution_scope']='N/A_not_independently_verified'
 return result

def score_population(records,declarations,inventory,*,source_anomalies=None):
 bound,states=binding.bind_targets(records,declarations['targets'],inventory,source_anomalies=source_anomalies)
 return project_bound_population(records,declarations,bound,states)

def project_bound_population(records,declarations,bound,states):
 """Project one population from an already bound physical UNION.
 This stateless projection does not authenticate provenance. The production
 union adapter creates these mappings once, before population projections.
 """
 targets=declarations['targets'];cases=declarations.get('cases',declarations.get('candidates',[]));T={t['target_id']:t for t in targets}
 if len(T)!=len(targets):raise ValueError('Duplicate physical source target ID')
 if set(bound)!=set(T):raise ValueError('Complete exact population binding map required')
 if any(type(t.get('parsed_numeric')) is not bool for t in targets):raise ValueError('Explicit strict source numeric/marker boolean required')
 if any(b['input_index'] is not None and (type(b['input_index']) is not int or not 0<=b['input_index']<len(records)) for b in bound.values()):raise ValueError('Binding index outside actual records')
 caseids=[c.get('case_id',c.get('relation_id')) for c in cases]
 if None in caseids or len(set(caseids))!=len(caseids):raise ValueError('Unique source accounting case IDs required')
 for case in cases:screens.validate_case_reference(case,T)
 cells=[]
 for t in targets:
  match=bound[t['target_id']];i=match['input_index'];record=records[i] if i is not None else {};actual=numeric(record.get('raw_value'));numeric_target=t['parsed_numeric'] is True
  expected=Decimal(t['numeric_value_printed']) if numeric_target else None
  if expected is not None and not expected.is_finite():raise ValueError('Source numeric value nonfinite')
  equal=(i is not None and actual is not None and actual==expected) if numeric_target else None
  cells.append({**match,'numeric_target':numeric_target,'expected_raw':t['raw_value'],'emitted_raw':record.get('raw_value'),'printed_numeric_equal':equal,'raw_marker_equal':bool(i is not None and record.get('raw_value','').strip()==t['raw_value'].strip()) if not numeric_target else None,'metadata':metadata_agreement(t,record,match['title_family_diagnostic'])})
 outcomes=[screens.score_case(c,T,bound,records) for c in cases];byendpoint=defaultdict(list)
 for out in outcomes:byendpoint[out['family']+'_'+out['endpoint']].append(out)
 numericcells=[c for c in cells if c['numeric_target']];markers=[c for c in cells if not c['numeric_target']];cellmap={c['target_id']:c for c in cells};crosstab=Counter()
 for out in outcomes:
  correct=all(cellmap[i]['printed_numeric_equal'] is True for i in out['operand_target_ids']) if all(i in cellmap for i in out['operand_target_ids']) else None;out['all_required_source_operand_values_correct']=correct
  if out['status'] in {'arithmetic_pass','arithmetic_fail'}:crosstab[('all_operands_correct' if correct else 'at_least_one_operand_incorrect',out['status'])]+=1
 return {'schema':'CSV50-stateless-population-score-v1','input_replay_provenance_verified_by_this_function':False,'numeric_transcription':{'fixed_denominator':len(numericcells),'matched_unique':sum(c['input_index'] is not None for c in numericcells),'correct_signed_printed_values':sum(c['printed_numeric_equal'] is True for c in numericcells),'fixed_denominator_recall':sum(c['printed_numeric_equal'] is True for c in numericcells)/len(numericcells) if numericcells else None,'no_full_output_precision_or_F1_claim':True},'markers':{'fixed_denominator':len(markers),'exact_raw_matches':sum(c['raw_marker_equal'] is True for c in markers)},'accounting_scopes':{name:screens.summary(rows) for name,rows in sorted(byendpoint.items())},'metadata_agreement_fixed_numeric_population':{dimension:dict(Counter(c['metadata'][dimension] for c in numericcells)) for dimension in DIMENSIONS},'consistency_vs_operand_values':[{ 'operand_correctness':correct,'conditional_arithmetic':status,'count':count} for (correct,status),count in sorted(crosstab.items())],'cell_results':cells,'case_results':outcomes,'record_disposition':states,'record_disposition_counts':dict(Counter(r['status'] for r in states)),'separate_population_overlap_must_not_be_added_as_unique':True}
