"""Prospective uniform50 source-only CSV association. NEVER reads numeric values.
Caller must first replay actual CSV inputs with the unchanged provenance loader.
Only physical page/table/column ordering, literal labels and declared visible
context can bind. Raw/clean row-role changes never change an association.
"""
from collections import Counter,defaultdict
from copy import deepcopy
import re,unicodedata

POLICY='CSV50-uniform-source-only-structural-binding-v1'
ANOMALY_DOCUMENT='1814287-0001814287-24-000028'
ANOMALY_FAMILIES={80:('balance_sheet','income_statement'),82:('income_statement','equity'),83:('equity','cash_flow')}

def key(text):
 return re.sub(r'[^\w]','',unicodedata.normalize('NFKC',text or '').casefold())

def label_keys(text):
 if not isinstance(text,str):return []
 return sorted({key(text),key(re.sub(r'\s*\(\d+\)\s*$','',text))})

def families(text):
 if not isinstance(text,str):return set()
 t=key(text).replace('_','');out=set()
 if 'balancesheet' in t or 'financialposition' in t:out.add('balance_sheet')
 if any(s in t for s in ('incomestatement','statementsofincome','statementofincome','statementsofoperations','statementofoperations','statementsofearnings','statementofearnings','comprehensiveincome','statementsofrevenuesandexpenses')):out.add('income_statement')
 if any(s in t for s in ('earningpershare','earningspershare','losspershare','incomepercommonshare','epsnote')):out.add('EPS_note')
 if any(s in t for s in ('stockholder','shareholder','changesinequity','equitystatement','statementofequity','statementsofequity')):out.add('equity')
 if 'cashflow' in t:out.add('cash_flow')
 # Canonical source/generated archetype names are also actual emitted text.
 if t=='incomestatement':out.add('income_statement')
 if t=='equity':out.add('equity')
 return out

def inventory_pages(inventory):
 """No implicit completeness inferred from target-containing tables alone."""
 pages=[]
 if 'pages' in inventory:pages=inventory['pages']
 else:
  grouped=defaultdict(list)
  for t in inventory.get('tables',[]):grouped[(t['document_id'],t['page'])].append(t)
  complete={(p['document_id'],p['page']) for p in inventory.get('complete_pages',[]) if p.get('complete_for_page') is True}
  for (doc,page),tables in grouped.items():
   pages.append({'document_id':doc,'page':page,'complete_for_page':(doc,page) in complete or all(t.get('page_inventory_complete') is True for t in tables),'tables':[{'ordinal':t['source_physical_table_ordinal'],'table_id':t['source_table_id'],'numeric_columns':t['numeric_column_count'],'line_range':t['table_line_range'],'family':t.get('family')} for t in tables]})
 result={}
 for p in pages:
  ident=(p['document_id'],p['page'])
  if ident in result:raise ValueError('Duplicate source page inventory')
  tables=p['tables'];orders=[t['ordinal'] for t in tables]
  if p.get('complete_for_page') is not True:raise ValueError('Explicit complete physical source-table inventory required')
  if sorted(orders)!=list(range(len(tables))) or len(set(t['table_id'] for t in tables))!=len(tables):raise ValueError('Complete contiguous source table ordinals/unique IDs required')
  for t in tables:
   if type(t['ordinal']) is not int or (t['numeric_columns'] is not None and (type(t['numeric_columns']) is not int or t['numeric_columns']<0)):raise ValueError('Invalid source physical table/column counts')
   if len(t['line_range'])!=2 or t['line_range'][0]>t['line_range'][1]:raise ValueError('Source table bounds missing or reversed')
  result[ident]={t['ordinal']:deepcopy(t) for t in tables}
 return result

def _anomalies(data):
 result={}
 for a in (data or {}).get('anomalies',[]):
  doc,page=a['document_id'],a['physical_page'];pair=ANOMALY_FAMILIES.get(page)
  if doc!=ANOMALY_DOCUMENT or pair is None or a['semantic_statement_family']!=pair[0] or a['source_literal_title_family']!=pair[1] or set(a['allowed_title_families'])!=set(pair) or a.get('title_family_accuracy_verifiable') is not False or a['source_physical_table_inventory'].get('complete_for_page') is not True:raise ValueError('Only the three predeclared exact source-title conflicts are admissible')
  if (doc,page) in result:raise ValueError('Duplicate source-title anomaly')
  result[(doc,page)]=a
 return result

def selector_view(target,pages,anomalies):
 s=target['csv_selector'];doc=s['document_id'];page=s['physical_page'];ordinal=s['source_physical_table_ordinal'];table=pages.get((doc,page),{}).get(ordinal)
 if target['document_id']!=doc or table is None or s['source_table_id']!=table['table_id'] or s['table_line_range']!=table['line_range']:raise ValueError('Target/page/table identity or bounds differ from complete inventory')
 column=s['source_numeric_column_ordinal']
 if type(column) is not int or type(table['numeric_columns']) is not int or not 0<=column<table['numeric_columns']:raise ValueError('Target numeric column outside known physical source table columns')
 aliases=s['alias_occurrence_selectors']
 if not aliases or len({a['alias_key'] for a in aliases})!=len(aliases):raise ValueError('Explicit unique per-alias occurrence selectors required')
 for a in aliases:
  count=a['source_numeric_row_label_count'];occ=a['numeric_row_occurrence_1based'];indices=a['source_numeric_row_indices']
  if key(a['alias'])!=a['alias_key'] or type(count) is not int or type(occ) is not int or not 1<=occ<=count or count!=len(indices) or len(set(indices))!=len(indices) or indices!=sorted(indices) or any(not table['line_range'][0]<=i<=table['line_range'][1] for i in indices):raise ValueError('Invalid per-alias source multiplicity/occurrence declaration')
  ownline=target.get('source_selector',{}).get('source_text_line_index')
  if ownline is not None and indices[occ-1]!=ownline:raise ValueError('Per-alias source occurrence does not identify target physical row')
 allowed={s['title_family']};anomaly=anomalies.get((doc,page))
 if anomaly:
  if target.get('source_pdf_sha256',target.get('source_binding',{}).get('source_pdf_sha256_verified'))!=anomaly['source_pdf_sha256'] or target.get('source_image_sha256')!=anomaly['source_image_sha256'] or s['title_family']!=anomaly['semantic_statement_family']:raise ValueError('Exact source-title anomaly PDF/image/family binding differs')
  entries=anomaly['source_physical_table_inventory']['tables'];other=next((t for t in entries if t['ordinal']==ordinal),None)
  if other is None or other['table_id']!=table['table_id'] or other['source_line_range']!=table['line_range'] or other['numeric_columns']!=table['numeric_columns']:raise ValueError('Source-title anomaly table inventory differs')
  allowed=set(anomaly['allowed_title_families'])
 return {'document_id':doc,'page':page,'table_ordinal':ordinal,'column_index':column,'title_family':s['title_family'],'allowed_title_families':allowed,'title_accuracy_NA_source_conflict':anomaly is not None,'aliases':deepcopy(aliases),'required_context':list(s.get('verified_local_context') or []),'excluded_context_keys':list(s.get('excluded_context_keys') or [])}

def _candidate_view(record):
 # This allowlist deliberately omits raw_value, numbers, periods, units, scope,
 # concepts, row_role and the roles payload. No gold/predicted value can select.
 return {k:deepcopy(record.get(k)) for k in ('document_id','page','table_ordinal','row_id','record_ordinal','column_index','row_label','table_title','statement','row_context','preceding_headings')}

def _context(view):
 # Only actual model-emitted hierarchy/heading text; no source context inserted.
 result=[]
 for field in ('row_context','preceding_headings'):
  value=view.get(field)
  if isinstance(value,list):result.extend(key(x) for x in value if isinstance(x,str))
 return result

def bind_targets(records,targets,inventory,*,source_anomalies=None):
 pages=inventory_pages(inventory);anomalies=_anomalies(source_anomalies);views=[_candidate_view(r) for r in records]
 if len(set(t['target_id'] for t in targets))!=len(targets):raise ValueError('Duplicate physical source target IDs')
 identities=Counter((v['document_id'],v['page'],v['table_ordinal'],v['row_id']) for v in views if isinstance(v['document_id'],str) and type(v['page']) is int and type(v['table_ordinal']) is int and isinstance(v['row_id'],str));states=[];index=defaultdict(list)
 for i,v in enumerate(views):
  reason=None
  if not isinstance(v['document_id'],str) or type(v['page']) is not int or v['page']<1 or not isinstance(v['row_id'],str) or not v['row_id'] or type(v['record_ordinal']) is not int or v['record_ordinal']<1:reason='invalid_record_identity_or_order'
  elif type(v['table_ordinal']) is not int or v['table_ordinal']<0:reason='unknown_or_invalid_physical_table_ordinal'
  elif identities[(v['document_id'],v['page'],v['table_ordinal'],v['row_id'])]>1:reason='duplicate_record_identity'
  elif type(v['column_index']) is not int or v['column_index']<0:reason='unknown_or_invalid_numeric_column_ordinal'
  elif not isinstance(v['row_label'],str):reason='invalid_row_label'
  if reason is None:
   for alias in label_keys(v['row_label']):index[(v['document_id'],v['page'],v['table_ordinal'],v['column_index'],alias)].append(i)
  states.append({'input_index':i,'row_id':v['row_id'],'status':reason or 'binding_candidate'})
 bound={}
 for target in targets:
  s=selector_view(target,pages,anomalies);selections=set();all_ids=set();issues=set();alternatives=[];incomplete_present_alias=False
  for alias in s['aliases']:
   ids=index.get((s['document_id'],s['page'],s['table_ordinal'],s['column_index'],alias['alias_key']),[]);all_ids.update(ids)
   if not ids:alternatives.append({'alias_key':alias['alias_key'],'status':'no_emitted_alias'});continue
   # Unknown titles never erase a unique transcription. A recognized conflicting
   # source family remains an association failure, except exact frozen anomalies.
   # Source aliases declare TABLE-GLOBAL multiplicity/occurrence. Context is
   # only a guard on that fixed occurrence, never a prefilter selecting another
   # row or shrinking the count (e.g Revenue/Product vs Cost/Product).
   if len(ids)!=alias['source_numeric_row_label_count']:
    incomplete_present_alias=True;issues.add('source_model_alias_multiplicity_mismatch');alternatives.append({'alias_key':alias['alias_key'],'status':'multiplicity_mismatch','candidate_input_indices':ids});continue
   ordered=sorted(ids,key=lambda i:(views[i]['record_ordinal'],i))
   if len({views[i]['record_ordinal'] for i in ordered})!=len(ordered):incomplete_present_alias=True;issues.add('duplicate_emitted_record_order');continue
   chosen=ordered[alias['numeric_row_occurrence_1based']-1];v=views[chosen];f=families(v['table_title']);ctx=_context(v);guard=None
   if f and not f&s['allowed_title_families']:guard='recognized_title_family_conflict'
   elif any(any(key(x) in c for c in ctx) for x in s['excluded_context_keys']):guard='source_declared_excluded_context'
   elif not all(key(x) in ctx for x in s['required_context']):guard='required_context_missing_at_fixed_source_occurrence'
   if guard:
    incomplete_present_alias=True;issues.add(guard);alternatives.append({'alias_key':alias['alias_key'],'status':'fixed_occurrence_guard_failed','input_index':chosen});continue
   selections.add(chosen);alternatives.append({'alias_key':alias['alias_key'],'status':'structural_match','input_index':chosen})
  chosen=next(iter(selections)) if len(selections)==1 and not incomplete_present_alias else None
  status='matched_unique' if chosen is not None else 'ambiguous_alias_alternatives' if len(selections)>1 else 'ambiguous_or_incomplete_source_association' if all_ids else 'missing_source_selector'
  title=families(views[chosen]['table_title']) if chosen is not None else set()
  bound[target['target_id']]={'target_id':target['target_id'],'status':status,'input_index':chosen,'candidate_input_indices':sorted(all_ids),'alias_alternatives':alternatives,'issues':sorted(issues),'title_family_diagnostic':'N/A_conflicting_source_title' if s['title_accuracy_NA_source_conflict'] else 'unavailable_no_unique_record' if chosen is None else 'unknown_emitted_title_family' if not title else 'agree' if s['title_family'] in title else 'source_literal_alternative','selection_uses_amount_date_unit_scope_or_row_role':False}
 assignments=Counter(b['input_index'] for b in bound.values() if b['input_index'] is not None)
 for b in bound.values():
  if b['input_index'] is not None and assignments[b['input_index']]>1:b.update(status='ambiguous_record_reused_for_multiple_source_cells',input_index=None)
 used={b['input_index'] for b in bound.values() if b['input_index'] is not None}
 for i,state in enumerate(states):
  if state['status']=='binding_candidate':state['status']='matched_source_target' if i in used else 'extra_or_unmatched_record'
 return bound,states
