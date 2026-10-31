"""Prospective CSV source binding and accounting screen. No replay/provenance claim.
Caller supplies actual CSV parse_page/results records after root-owned reply replay.
All627numeric cells fixed, including discrepancies/auxiliary/ineligible components.
"""
from pathlib import Path
import json,hashlib,re,unicodedata,datetime,calendar
from decimal import Decimal
from collections import Counter,defaultdict
from numeric_format import numeric,project_number,presentation_compatible
O=Path(__file__).resolve().parent
DECLARATIONS_SHA256='7351b61782d979aec3b9ae6c93688d4c00874ed65bae0e40b25783b37320c6ed'
def key(s):return re.sub(r'[^\w]','',unicodedata.normalize('NFKC',s or '').casefold())
def label_keys(text):
 raw=key(text);stripped=key(re.sub(r'\s*\(\d+\)\s*$','',text or ''));return sorted({raw,stripped})
def family(text):
 s=key(text)
 if 'balancesheet' in s or 'financialposition' in s:return 'balance_sheet'
 if any(x in s for x in ['incomestatement','statementsofincome','statementofincome','statementsofoperations','statementofoperations','statementsofearnings','statementofearnings','comprehensiveincome']):return 'income_statement'
 return None
def load_declarations():
 p=O/'target-declarations.json';assert hashlib.sha256(p.read_bytes()).hexdigest()==DECLARATIONS_SHA256,'declaration hash mismatch';d=json.loads(p.read_text());assert d['numeric_denominator']==627 and d['marker_denominator']==15;return d

def emitted_period(raw):
 """Parse only emitted text; never source-fill date/start/unit."""
 if not isinstance(raw,str) or not raw.strip():return {'raw':raw,'end':None,'year':None,'duration_months':None,'key':None}
 text=unicodedata.normalize('NFKC',raw).strip();dates=[]
 for x in re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text):
  try:dates.append(datetime.date.fromisoformat(x).isoformat())
  except ValueError:pass
 if not dates:
  for month,day,year in re.findall(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b',text):
   try:dates.append(datetime.date(int(year),list(calendar.month_name).index(month.title()),int(day)).isoformat())
   except ValueError:pass
 end=dates[0] if len(dates)==1 else dates[-1] if len(dates)==2 and re.search(r'/|\bto\b|through|\s[-–]\s',text) else None
 years=re.findall(r'\b(?:19|20)\d{2}\b',text);year=int(end[:4]) if end else int(years[0]) if len(set(years))==1 else None
 low=text.casefold();duration=None
 for token,n in [('three',3),('six',6),('nine',9),('twelve',12),('3',3),('6',6),('9',9),('12',12)]:
  if re.search(r'\b'+token+r'\s+months?\b',low):duration=n;break
 if re.search(r'\byears?\s+end|\bfiscal\s+year|\bannual\b',low):duration=12
 return {'raw':raw,'end':end,'year':year,'duration_months':duration,'key':key(text)}

UNKNOWN_KEYS={'','unknown','null','none','na','notavailable','notprovided','notapplicable','unspecified','unknownscope','unknownperiod','unknownentity','unknowncurrency','unknownunit','unknownscale','unknownquantity','unk','xxx'}
def placeholder(value):return key(value).replace('_','') in UNKNOWN_KEYS
def emitted_date_context(record):
 """Merge ONLY actual period+actual column header; preserve contradictions."""
 p=emitted_period(record.get('period'));h=emitted_period(record.get('column_label'));conflicts=[]
 for field,tag in [('period','invalid_actual_ISO_date'),('column_label','invalid_actual_header_ISO_date')]:
  for token in re.findall(r'\b\d{4}-\d{2}-\d{2}\b',record.get(field) or ''):
   try:datetime.date.fromisoformat(token)
   except ValueError:conflicts.append(tag)
 # Calendar-aligned emitted ISO intervals identify their OWN duration; fiscal
 # week calendars are not guessed. Derive before comparing the actual header.
 raw=record.get('period') or '';match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})',raw)
 if match:
  try:
   a,b=map(datetime.date.fromisoformat,match.groups())
   if a>b:raise ValueError('reversed interval')
   if a.day==1 and b.day==calendar.monthrange(b.year,b.month)[1]:
    months=(b.year-a.year)*12+b.month-a.month+1
    if months in (3,6,9,12):p['duration_months']=months
  except ValueError:
   conflicts.append('invalid_actual_ISO_interval');p.update(end=None,year=None,duration_months=None)
 for name in ('end','year','duration_months'):
  if p[name] is not None and h[name] is not None and p[name]!=h[name]:conflicts.append(name)
 result={name:(p[name] if p[name] is not None else h[name]) for name in ('end','year','duration_months')}
 result.update(conflicts=conflicts,actual_period=p,actual_column_header=h)
 result['meaningful']=not conflicts and (result['end'] is not None or (result['duration_months']==12 and result['year'] is not None))
 return result

def _context(record):
 path=record.get('row_context');return [key(x) for x in path] if isinstance(path,list) and all(isinstance(x,str) for x in path) else []
def _local_context_ok(selector,record):
 context=_context(record);required=selector['verified_local_context'];excluded=selector['excluded_context_keys']
 if any(any(x in c for x in excluded) for c in context):return False
 return bool(required) and all(any(key(x)==c for c in context) for x in required)
def _excluded(selector,record):return any(any(x in c for x in selector['excluded_context_keys']) for c in _context(record))

def bind_targets(records,declarations):
 """Fixed structural match before inspecting any emitted amount/date/unit."""
 index=defaultdict(list);record_states=[];identitycounts=Counter((r.get('document_id'),r.get('page'),r.get('table_ordinal'),r.get('row_id')) for r in records)
 for i,r in enumerate(records):
  f=family(r.get('table_title'));sf=family(r.get('statement'));ordinal=r.get('column_index');state={'input_index':i,'row_id':r.get('row_id'),'status':'binding_candidate'}
  if identitycounts[(r.get('document_id'),r.get('page'),r.get('table_ordinal'),r.get('row_id'))]>1:state['status']='duplicate_record_identity'
  elif type(ordinal) is not int or ordinal<0:state['status']='unknown_or_invalid_numeric_column_ordinal'
  elif f is None:state['status']='unknown_title_family'
  elif sf and sf!=f:state['status']='title_statement_family_conflict'
  if state['status']=='binding_candidate':
   for labelkey in label_keys(r.get('row_label')):index[(r.get('document_id'),r.get('page'),f,ordinal,labelkey)].append(i)
  record_states.append(state)
 known_contexts=defaultdict(set)
 for t in declarations['targets']:
  ss=t['csv_selector']
  if ss['verified_local_context']:
   for alias in ss['row_label_alias_keys']:known_contexts[(ss['document_id'],ss['physical_page'],ss['title_family'],ss['source_numeric_column_ordinal'],alias)].update(key(x) for x in ss['verified_local_context'])
 bound={};used=set()
 for t in declarations['targets']:
  s=t['csv_selector'];ids=[]
  for alias in s['row_label_alias_keys']:ids.extend(index.get((s['document_id'],s['physical_page'],s['title_family'],s['source_numeric_column_ordinal'],alias),[]))
  ids=sorted(set(ids));groups=defaultdict(list)
  for i in ids:groups[records[i].get('table_ordinal')].append(i)
  candidates=[];group_issues=[]
  for group in groups.values():
   local=[i for i in group if _local_context_ok(s,records[i])];nonexcluded=[i for i in group if not _excluded(s,records[i])]
   # Explicit verified group context may identify one repeated source row. For
   # otherwise unique labels, all duplicates remain ambiguous, even if values agree.
   recognized=set().union(*(known_contexts.get((s['document_id'],s['physical_page'],s['title_family'],s['source_numeric_column_ordinal'],a),set()) for a in s['row_label_alias_keys']))
   has_recognized_context=any(set(_context(records[i]))&recognized for i in group)
   if s['verified_local_context'] and has_recognized_context:
    if len(local)==1:candidates+=local
    else:group_issues.append('missing_or_duplicate_explicit_local_context')
    continue
   if len(group)!=s['source_numeric_row_label_count']:group_issues.append('source_model_label_multiplicity_mismatch');continue
   # Duplicated complete role payloads cannot masquerade as distinct occurrences.
   payloads=[json.dumps(records[i].get('roles'),sort_keys=True) for i in group if records[i].get('roles')]
   if len(payloads)!=len(set(payloads)):group_issues.append('repeated_identical_role_payload_indicator; source occurrence order retained only at exact declared multiplicity')
   ordered=sorted(group,key=lambda i:(records[i].get('record_ordinal',i),i));i=ordered[s['numeric_row_occurrence_1based']-1]
   if i in nonexcluded:candidates.append(i)
  status='matched_unique' if len(candidates)==1 else 'ambiguous_multiple_tables_or_records' if len(candidates)>1 else 'ambiguous_or_incomplete_label_occurrences' if ids else 'missing_source_selector'
  b={'target_id':t['target_id'],'status':status,'candidate_input_indices':ids,'issues':sorted(set(group_issues)),'input_index':candidates[0] if len(candidates)==1 else None}
  if b['input_index'] is not None:used.add(b['input_index'])
  bound[t['target_id']]=b
 # One model record must not satisfy two source physical cells, including alias collisions.
 assignments=Counter(b['input_index'] for b in bound.values() if b['input_index'] is not None)
 for b in bound.values():
  if b['input_index'] is not None and assignments[b['input_index']]>1:b['status']='ambiguous_record_reused_for_multiple_source_cells';b['input_index']=None
 used={b['input_index'] for b in bound.values() if b['input_index'] is not None}
 for i,st in enumerate(record_states):
  if st['status']=='binding_candidate':st['status']='matched_source_target' if i in used else 'extra_or_unmatched_record'
 return bound,record_states

def metadata_agreement(target,record):
 v=target['metadata_verifiability'];m=target['metadata'];p=emitted_date_context(record);expected=m['period'];result={}
 def compare(name,verifiable,actual,wanted):result[name]='N/A_unverifiable' if not verifiable else 'unknown_emitted' if actual is None else 'agree' if actual==wanted else 'disagree'
 compare('quantity',v['quantity_verifiable'],record.get('quantity') if record.get('quantity') not in (None,'unknown','') else None,'money')
 compare('scale',v['printed_scale_verifiable'],record.get('scale'),m['scale'])
 compare('period_end',v['period_end_verifiable'],p['end'] if 'end' not in p['conflicts'] else None,expected['end'])
 compare('printed_year',v['period_end_verifiable'],p['year'] if 'year' not in p['conflicts'] else None,int(expected['end'][:4]))
 compare('duration_heading_months',v['duration_heading_verifiable'],p['duration_months'] if 'duration_months' not in p['conflicts'] else None,expected['duration_months'])
 result['actual_period_header_conflicts']=p['conflicts']
 if any(x.startswith('invalid_actual_') for x in p['conflicts']):
  result['period_end']='conflicting_actual_metadata' if v['period_end_verifiable'] else 'N/A_unverifiable'
 for name,dimension in [('end','period_end'),('year','printed_year'),('duration_months','duration_heading_months')]:
  if name in p['conflicts'] and result[dimension]!='N/A_unverifiable':result[dimension]='conflicting_actual_metadata'
 compare('exact_start',False,None,None);compare('ISO_currency',False,None,None)
 scope=record.get('scope');scopeflag=True if isinstance(scope,str) and 'consolidat' in scope.casefold() else False if isinstance(scope,str) and re.search(r'\bparent\b|\bstandalone\b',scope.casefold()) else None
 compare('consolidation_scope',v['scope_consolidation_verifiable'],scopeflag,True)
 # Currency role is ISO, not a source-literal glyph. Its inferred code cannot
 # become a gold target. Record a literal-glyph diagnostic without requiring it.
 token=project_number(record.get('raw_value'))['currency_token'];result['literal_currency_symbol']='N/A_unverifiable' if not v['currency_symbol_verifiable'] else 'unknown_emitted' if token is None else 'agree' if token=='$' else 'disagree'
 return result

def emitted_commensurability(rows):
 """Optional same-emitted-context screen; not gold-filled semantic validity."""
 contexts=[]
 for r in rows:
  p=project_number(r.get('raw_value'));q=r.get('quantity');scale=r.get('scale');cur=r.get('currency');scope=r.get('scope');datectx=emitted_date_context(r)
  if q!='money' or type(scale) is not int or scale not in [1,1000,1000000,1000000000] or not isinstance(cur,str) or not re.fullmatch('[A-Z]{3}',cur) or placeholder(cur) or not isinstance(scope,str) or placeholder(scope) or not datectx['meaningful']:return {'status':'unknown_or_invalid_emitted_metadata','contexts':contexts}
  if not presentation_compatible(p,quantity=q,currency=cur):return {'status':'raw_currency_token_conflicts_with_emitted_metadata','contexts':contexts}
  contexts.append({'quantity':q,'scale':scale,'currency':cur,'scope':key(scope),'period':{k:datectx[k] for k in ('end','year','duration_months')}})
 return {'status':'same_emitted_context' if all(c==contexts[0] for c in contexts) else 'different_emitted_contexts','contexts':contexts}

def score_records(records,declarations=None):
 frozen=load_declarations()
 if declarations is not None and declarations!=frozen:raise ValueError('Unfrozen declarations cannot enter production scoring')
 d=frozen;T={t['target_id']:t for t in d['targets']};bindings,states=bind_targets(records,d);cells=[]
 for t in d['targets']:
  b=bindings[t['target_id']];i=b['input_index'];r=records[i] if i is not None else None;value=numeric(r.get('raw_value')) if r else None;expected=Decimal(t['numeric_value_printed']) if t['parsed_numeric'] else None
  cells.append({**b,'numeric_target':t['parsed_numeric'],'expected_raw':t['raw_value'],'emitted_raw':r.get('raw_value') if r else None,'numeric_parse_status':'parsed' if value is not None else 'unparsed_or_missing','printed_numeric_equal':bool(r and value is not None and expected is not None and value==expected) if t['parsed_numeric'] else None,'raw_marker_equal':r.get('raw_value','').strip()==t['raw_value'].strip() if r and not t['parsed_numeric'] else None,'metadata':metadata_agreement(t,r or {})})
 relations=[]
 for c in d['relations']:
  refs=[c['lhs_target_id']]+[q['target_id'] for q in c['terms']];bs=[bindings[x] for x in refs];row={'relation_id':c['relation_id'],'source_numeric_eligible':c['numeric_eligible'],'source_ineligible_reasons':c['ineligible_reasons'],'oracle_context_disclosure':'Conditional source-bound printed-unit screen: source supplies eligibility/signs/grouping and precision, not prediction metadata. No gold amount/date/unit used for matching.'}
  if not c['numeric_eligible']:row['status']='source_ineligible'
  elif any(b['input_index'] is None for b in bs):row['status']='not_attempted_missing_or_ambiguous_operand'
  else:
   rows=[records[b['input_index']] for b in bs];nums=[numeric(r.get('raw_value')) for r in rows]
   row['emitted_metadata_diagnostic']=emitted_commensurability(rows)
   if any(n is None for n in nums):row['status']='not_attempted_unparsed_operand'
   else:
    rhs=sum((Decimal(q['coefficient'])*n for q,n in zip(c['terms'],nums[1:])),Decimal(0));res=nums[0]-rhs;radius=Decimal(T[refs[0]]['rounding_radius_printed'])+sum((abs(Decimal(q['coefficient']))*Decimal(T[q['target_id']]['rounding_radius_printed']) for q in c['terms']),Decimal(0));row.update(status='arithmetic_pass' if abs(res)<=radius else 'arithmetic_fail',signed_residual_printed=str(res),bound_printed=str(radius),conditional_pass=abs(res)<=radius)
  relations.append(row)
 numericcells=[c for c in cells if c['numeric_target']];markers=[c for c in cells if not c['numeric_target']];scopes={}
 for name,ep in d['endpoints'].items():
  rs=[r for r in relations if r['relation_id'] in ep['relation_ids']];eligible=[r for r in rs if r['source_numeric_eligible']];attempted=[r for r in eligible if r['status'] in ('arithmetic_pass','arithmetic_fail')];passed=sum(r['status']=='arithmetic_pass' for r in attempted);scopes[name]={'fixed_candidates':len(rs),'fixed_source_eligible':len(eligible),'attempted':len(attempted),'passes':passed,'conditional_pass_rate':passed/len(attempted) if attempted else None,'passes_over_fixed_source_eligible':passed/len(eligible) if eligible else None,'statuses':dict(Counter(r['status'] for r in rs))}
 metadata_summary={name:dict(Counter(c['metadata'][name] for c in numericcells)) for name in ['quantity','scale','period_end','printed_year','duration_heading_months','exact_start','ISO_currency','consolidation_scope','literal_currency_symbol']}
 return {'metadata_agreement_fixed_numeric_population':metadata_summary,'declarations_sha256':DECLARATIONS_SHA256,'replay_provenance_verified_by_this_adapter':False,'numeric_transcription':{'fixed_denominator':len(numericcells),'matched_unique':sum(c['input_index'] is not None for c in numericcells),'correct_signed_printed_values':sum(c['printed_numeric_equal'] for c in numericcells),'fixed_denominator_recall':sum(c['printed_numeric_equal'] for c in numericcells)/len(numericcells) if numericcells else None,'no_F1_claim':'All627source supplement numeric cells fixed; model extras/duplicates reported, no full-output precision or F1 claim'},'markers':{'fixed_denominator':len(markers),'exact_raw_matches':sum(c['raw_marker_equal'] is True for c in markers)},'arithmetic_scopes':scopes,'cell_results':cells,'relation_results':relations,'record_disposition':states,'record_disposition_counts':dict(Counter(s['status'] for s in states))}

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('records_json',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=json.loads(a.records_json.read_text());records=r if isinstance(r,list) else r['raw_records'];a.output.write_text(json.dumps(score_records(records),indent=2)+'\n')
