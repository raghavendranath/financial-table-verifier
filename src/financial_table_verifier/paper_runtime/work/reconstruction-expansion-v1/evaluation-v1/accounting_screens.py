"""Stateless uniform50 screens, separate from source association and accuracy.
No file/network/model reads. Operands are supplied from already unique bindings.
Source context supplies declared roles, signs, precision and conditional units;
model-metadata screens use ONLY actual emitted metadata and own balance dates.
"""
from decimal import Decimal,localcontext
from datetime import date,timedelta,datetime
import calendar,re
from collections import Counter
from pathlib import Path
import sys
OLD=Path(__file__).resolve().parents[2]/'reconstruction-v1'
sys.path.insert(0,str(OLD/'csv-evaluation-v1'))
from numeric_format import numeric,project_number,presentation_compatible

SCALES={1,1000,1000000,1000000000}
UNKNOWN={'','unknown','null','none','na','notavailable','notprovided','notapplicable','unspecified','unknownscope','unknownperiod','unknownentity','unknowncurrency','unknownunit','unknownscale','unknownquantity','unk','xxx'}
def norm(value):return re.sub(r'[^a-z0-9]','',str(value or '').casefold())
def placeholder(value):return norm(value) in UNKNOWN

def actual_period(record,*,balance=False):
 """Full explicit ISO duration/instant, or a full printed balance-row date only.
 No fiscal start/year inference, reference dates, or source-bound block filling.
 """
 period=record.get('period');label=record.get('row_label') or '';result=None
 if balance and re.search(r'\bbalances?\b',label,re.I):
  dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',label)
  english_dates=re.findall(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b',label)
  for month,day,year in english_dates:
   for fmt in ('%B %d %Y','%b %d %Y'):
    try:dates.append(datetime.strptime(f'{month} {day} {year}',fmt).date().isoformat());break
    except ValueError:pass
  dates=list(dict.fromkeys(dates))
  if len(dates)==1:
   try:return {'kind':'instant','end':date.fromisoformat(dates[0]).isoformat(),'origin':'own_emitted_balance_row_date'}
   except ValueError:return None
  if dates or english_dates:return None
 if not isinstance(period,str):return None
 instant=re.fullmatch(r'(\d{4}-\d{2}-\d{2})',period.strip());duration=re.fullmatch(r'(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})',period.strip())
 try:
  if instant:return {'kind':'instant','end':date.fromisoformat(instant[1]).isoformat(),'origin':'own_emitted_period'}
  if duration:
   start,end=map(date.fromisoformat,duration.groups())
   if start<=end:return {'kind':'duration','start':start.isoformat(),'end':end.isoformat(),'origin':'own_emitted_period'}
 except ValueError:pass
 return None

def _period_key(value):return {k:v for k,v in value.items() if k!='origin'} if value else None

def header_date_conflict(record,period):
 """Only own unambiguous FULL dates; never fiscal-year-name inference.
 Explicit RE balance-row dates intentionally override a generic table header.
 """
 if period is None or period.get('origin')=='own_emitted_balance_row_date':return None
 text=record.get('column_label')
 if not isinstance(text,str):return None
 direct=actual_period({'period':text})
 if direct:
  if direct['end']!=period['end'] or (direct['kind']=='duration' and period['kind']=='duration' and direct['start']!=period['start']):return 'unambiguous_actual_period_header_full_date_conflict'
  return None
 dates=[];invalid=False
 for token in re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text):
  try:dates.append(date.fromisoformat(token).isoformat())
  except ValueError:invalid=True
 for month,day,year in re.findall(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b',text):
  parsed=None
  for fmt in ('%B %d %Y','%b %d %Y'):
   try:parsed=datetime.strptime(f'{month} {day} {year}',fmt).date().isoformat();break
   except ValueError:pass
  if parsed:dates.append(parsed)
  else:invalid=True
 if invalid:return 'invalid_explicit_full_date_in_actual_header'
 dates=set(dates)
 if len(dates)==1 and next(iter(dates))!=period['end']:return 'unambiguous_actual_period_header_full_date_conflict'
 # Multiple visible full dates are ambiguous, not selected by agreement with
 # period/source. A bare fiscal-year name is not an end-date assertion.
 return None

def precision(target):
 try:value=Decimal(str(target['rounding_radius_printed']))
 except Exception:raise ValueError('Explicit source printed precision required') from None
 if not value.is_finite() or value<0:raise ValueError('Explicit finite nonnegative source printed precision required')
 return value

def operands(case):
 if case['family']=='EPS':return [case.get('numerator_target_id'),case.get('shares_target_id'),case.get('EPS_target_id')]
 if case['family']=='RE':return [case.get('end_target_id'),case.get('begin_target_id')]+[t['target_id'] for t in case['terms']]
 return [case['lhs_target_id']]+[t['target_id'] for t in case['terms']]

def validate_case_reference(case,targets):
 if case.get('family') not in {'BS','IS','EPS','RE'} or type(case.get('source_applicable')) is not bool or type(case.get('numeric_eligible')) is not bool:raise ValueError('Explicit source family/applicability/numeric eligibility required')
 if not case['source_applicable'] or not case['numeric_eligible']:return
 ids=operands(case)
 if len(set(ids))!=len(ids):raise ValueError('Source case repeats a physical operand; no double counting')
 if any(i not in targets for i in ids):raise ValueError('Eligible source case references missing target')
 for i in ids:
  if targets[i].get('parsed_numeric') is not True:raise ValueError('Eligible source case contains nonnumeric source marker')
  precision(targets[i])
 if case['family']=='EPS':
  for name in ('monetary_multiplier','share_multiplier'):
   try:value=Decimal(str(case.get(name)))
   except Exception:raise ValueError('Eligible EPS needs explicit source multiplier') from None
   if not value.is_finite() or value<=0:raise ValueError('Eligible EPS needs explicit positive source multipliers')
 else:
  for term in case['terms']:
   coefficient=Decimal(str(term['coefficient']))
   if not coefficient.is_finite() or coefficient==0:raise ValueError('Explicit finite nonzero source component coefficient required')
  if case['family']=='BS' and len(case['terms'])<2:raise ValueError('Primary capital identity requires complete component bridge')
  if case['family']=='IS' and case.get('endpoint')=='primary' and len(case['terms'])<2:raise ValueError('Single-component IS restatement belongs in auxiliary endpoint')
  if case['family']=='RE' and (case.get('source_complete_bridge') is not True or not case.get('completeness_evidence') or case.get('dividend_policy') not in {'reported','source_verified_absent'} or (case.get('dividend_policy')=='source_verified_absent' and not case.get('source_verified_absent_attestation'))):raise ValueError('Eligible RE requires explicit complete bridge/dividend evidence')

def linear(values,coefficients,radii):
 residual=values[0]-sum((c*v for c,v in zip(coefficients,values[1:])),Decimal(0));bound=radii[0]+sum((abs(c)*r for c,r in zip(coefficients,radii[1:])),Decimal(0))
 return {'status':'arithmetic_pass' if abs(residual)<=bound else 'arithmetic_fail','signed_residual_printed':str(residual),'printed_precision_bound':str(bound),'conditional_pass':abs(residual)<=bound}

def quotient(values,radii,numerator_multiplier,share_multiplier):
 nm,sm=Decimal(str(numerator_multiplier)),Decimal(str(share_multiplier))
 if not nm.is_finite() or not sm.is_finite() or nm<=0 or sm<=0:raise ValueError('Explicit positive source/model multipliers required')
 n,s,e=values;rn,rs,reps=radii;ni=[(n-rn)*nm,(n+rn)*nm];si=[(s-rs)*sm,(s+rs)*sm]
 if si[0]<=0:return {'status':'unavailable_nonpositive_or_zero_crossing_shares','conditional_pass':None}
 with localcontext() as ctx:
  ctx.prec=50;ratios=[x/y for x in ni for y in si];lo,hi=min(ratios),max(ratios);elo,ehi=e-reps,e+reps;passed=max(lo,elo)<=min(hi,ehi)
  return {'status':'arithmetic_pass' if passed else 'arithmetic_fail','conditional_pass':passed,'numerator_interval':[str(x) for x in ni],'shares_interval':[str(x) for x in si],'quotient_interval':[str(lo),str(hi)],'reported_EPS_interval':[str(elo),str(ehi)],'point_quotient':str(n*nm/(s*sm)),'source_printed_precision_not_point_tolerance':True}

def _typed_context(rows,expected_quantities):
 contexts=[]
 for r,q in zip(rows,expected_quantities):
  quantity=r.get('quantity');scale=r.get('scale');currency=r.get('currency');scope=r.get('scope')
  if quantity!=q or type(scale) is not int or scale not in SCALES or (quantity=='per_share' and scale!=1):return None,'model_quantity_or_scale_unknown_invalid'
  if quantity!='shares' and (not isinstance(currency,str) or not re.fullmatch(r'[A-Z]{3}',currency) or placeholder(currency)):return None,'model_currency_unknown_invalid'
  if quantity=='shares' and currency is not None:return None,'model_shares_currency_not_applicable'
  if not isinstance(scope,str) or placeholder(scope):return None,'model_scope_unknown'
  projection=project_number(r.get('raw_value'))
  if not presentation_compatible(projection,quantity=quantity,currency=currency):return None,'numeric_presentation_conflicts_with_actual_model_metadata'
  contexts.append({'quantity':quantity,'scale':scale,'currency':currency,'scope':norm(scope)})
 if len({c['scope'] for c in contexts})!=1:return None,'model_scope_inconsistent'
 if len({c['currency'] for c in contexts if c['quantity']!='shares'})!=1:return None,'model_currency_inconsistent'
 return contexts,None

def model_metadata_screen(case,rows,radii):
 """Internal commensurability, NOT source-metadata/attribution accuracy."""
 family=case['family'];q=['money','shares','per_share'] if family=='EPS' else ['money']*len(rows);contexts,reason=_typed_context(rows,q)
 if reason:return {'status':'skipped','reason':reason,'gold_metadata_inserted':False}
 periods=[actual_period(r,balance=family=='RE' and i<2) for i,r in enumerate(rows)]
 if any(p is None for p in periods):return {'status':'skipped','reason':'model_full_period_or_balance_date_unknown','gold_metadata_inserted':False}
 conflicts=[header_date_conflict(r,p) for r,p in zip(rows,periods)]
 if any(conflicts):return {'status':'skipped','reason':'model_actual_period_header_full_date_conflict','operand_conflicts':conflicts,'gold_metadata_inserted':False}
 if family=='RE':
  closing,opening=periods[:2]
  if closing['kind']!='instant' or opening['kind']!='instant':return {'status':'skipped','reason':'model_RE_balance_instants_unknown','gold_metadata_inserted':False}
  interval={'kind':'duration','start':(date.fromisoformat(opening['end'])+timedelta(days=1)).isoformat(),'end':closing['end']}
  if interval['start']>interval['end'] or any(p['end']>interval['end'] or p.get('start',p['end'])<interval['start'] for p in periods[2:]):return {'status':'skipped','reason':'model_RE_movement_period_outside_own_balance_dates','gold_metadata_inserted':False}
  if any(_period_key(p)!=interval for p in periods[2:]):return {'status':'skipped','reason':'unsupported_RE_subperiod_timeline','timeline_status':'contained_subperiod_or_point_movements_not_validated','actual_periods':periods,'gold_metadata_inserted':False}
 else:
  if any(_period_key(p)!=_period_key(periods[0]) for p in periods):return {'status':'skipped','reason':'model_period_inconsistent','gold_metadata_inserted':False}
  if family in {'BS','IS'} and periods[0]['kind']!=('instant' if family=='BS' else 'duration'):return {'status':'skipped','reason':'model_period_kind_conflicts_with_financial_family','gold_metadata_inserted':False}
  if family=='EPS' and periods[0]['kind']!='duration':return {'status':'skipped','reason':'model_EPS_requires_explicit_duration','gold_metadata_inserted':False}
 nums=[numeric(r.get('raw_value')) for r in rows]
 if any(v is None for v in nums):return {'status':'skipped','reason':'model_operand_unparsed','gold_metadata_inserted':False}
 if family=='EPS':result=quotient(nums,radii,contexts[0]['scale'],contexts[1]['scale'])
 else:
  if len({c['scale'] for c in contexts})!=1:return {'status':'skipped','reason':'model_linear_operands_mixed_scales','gold_metadata_inserted':False}
  if family in {'BS','IS'}:return {'status':'same_emitted_context','interpretation':'Status only; this is not a joint-fact accuracy or arithmetic outcome','actual_contexts':contexts,'actual_periods':periods,'gold_metadata_inserted':False}
  result=linear(nums,[Decimal(1)]+[Decimal(str(t['coefficient'])) for t in case['terms']],radii)
 return {**result,'actual_contexts':contexts,'actual_periods':periods,'gold_metadata_inserted':False,'interpretation':'Actual model metadata + source-declared operand roles/eligibility/printed precision; shareholder attribution not established'}

def score_case(case,targets,bindings,records):
 validate_case_reference(case,targets)
 ident=case.get('relation_id',case.get('case_id'));family=case['family'];row={'case_id':ident,'document_id':case['document_id'],'family':family,'endpoint':case.get('endpoint','primary'),'source_applicable':case.get('source_applicable') is True,'source_numeric_eligible':case.get('numeric_eligible') is True,'operand_target_ids':operands(case),'selection_uses_values':False}
 if case.get('source_applicable') is not True:return {**row,'status':'source_excluded','reason':case.get('ineligible_reasons',case.get('ineligible_reason')),'actual_model_metadata':{'status':'source_excluded'}}
 if case.get('numeric_eligible') is not True:return {**row,'status':'source_arithmetic_unavailable','reason':case.get('ineligible_reasons',case.get('ineligible_reason')),'actual_model_metadata':{'status':'source_arithmetic_unavailable'}}
 ids=row['operand_target_ids']
 if any(i not in targets for i in ids):raise ValueError('Source case references missing target')
 if any(bindings[i]['input_index'] is None for i in ids):return {**row,'status':'unavailable_missing_or_ambiguous_operand','actual_model_metadata':{'status':'unavailable_missing_or_ambiguous_operand'}}
 rows=[records[bindings[i]['input_index']] for i in ids];nums=[numeric(r.get('raw_value')) for r in rows];radii=[precision(targets[i]) for i in ids]
 if any(v is None for v in nums):return {**row,'status':'unavailable_unparsed_operand','actual_model_metadata':{'status':'unavailable_unparsed_operand'}}
 if family=='EPS':
  if case.get('monetary_multiplier') is None or case.get('share_multiplier') is None:raise ValueError('Eligible EPS source has missing explicit multiplier')
  conditional=quotient(nums,radii,case['monetary_multiplier'],case['share_multiplier'])
 elif family in {'BS','IS','RE'}:
  coefficients=([Decimal(1)] if family=='RE' else [])+[Decimal(str(t['coefficient'])) for t in case['terms']];conditional=linear(nums,coefficients,radii)
 else:raise ValueError('Unsupported source accounting family')
 row.update(conditional);row['actual_model_metadata']=model_metadata_screen(case,rows,radii);row['conditional_context']='Source-bound roles/signs/grouping/eligibility/units/precision, never expected amounts or metadata insertion into model records'
 if family=='BS':
  bound=abs(nums[0])*Decimal('.005');res=Decimal(conditional['signed_residual_printed']);row['original_BS_0_5_percent_sensitivity']={'status':'pass' if abs(res)<=bound else 'fail','tolerance_of_actual_predicted_assets_printed':str(bound),'source_aware_capital_bridge':True}
 return row

def summary(rows):
 return {'fixed_candidates':len(rows),'fixed_source_applicable':sum(r['source_applicable'] for r in rows),'fixed_source_numeric_eligible':sum(r['source_numeric_eligible'] and r['source_applicable'] for r in rows),'statuses':dict(Counter(r['status'] for r in rows)),'actual_model_metadata_statuses':dict(Counter(r['actual_model_metadata']['status'] for r in rows))}
