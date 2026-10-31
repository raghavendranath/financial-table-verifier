"""Source-only union adapter; never opens predictions or chooses by values.
Physical identity is from the independently reviewed source map. Values are
parsed/asserted only after identity. Legacy payloads remain attached verbatim;
legacy inferred metadata never becomes source-verifiable transcription truth.
"""
from copy import deepcopy
from collections import Counter
from decimal import Decimal
from pathlib import Path
import hashlib,json
import structural_binding as binding
import accounting_screens as screens
from score_population import project_bound_population

def load_source_contract(manifest_ref):
 """Hash-bound source-only loader, not a prediction/provenance loader."""
 def verified(ref):
  raw=Path(ref['path']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Frozen source dependency hash differs')
  return json.loads(raw)
 manifest=verified(manifest_ref)
 if manifest.get('schema')!='CSV50-source-contract-manifest-v1' or manifest.get('source_only') is not True or manifest.get('new_primary_outputs_read') is not False:raise ValueError('Pre-output source contract required')
 for path,expected in manifest['files_sha256'].items():
  if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected:raise ValueError('Frozen physical source/reference dependency differs')
 sources={part:{pop:verified(ref) for pop,ref in refs.items() if pop in {'BSIS','EPSRE'}} for part,refs in manifest['source_populations'].items()}
 compiled=compile_sources(sources,verified(manifest['physical_map']));compiled['source_contract_ref']=deepcopy(manifest_ref)
 return compiled,verified(manifest['complete_inventory']),verified(manifest['source_title_anomalies'])

def legacy_numeric_target(target):
 result=deepcopy(target);projection=screens.project_number(target['raw_value'])
 if projection['status']!='parsed':raise ValueError('Legacy physical EPS/RE source token must be numeric')
 value=Decimal(projection['value']);radius=Decimal(10)**value.as_tuple().exponent/2
 result.update(parsed_numeric=True,numeric_value_printed=str(value),rounding_radius_printed=str(radius),printed_quantum=str(radius*2),metadata={},metadata_verifiability={},legacy_metadata_not_promoted_to_source_truth=True)
 return result

def old_cases(source):
 mapping=source['fact_to_target'];result=[]
 def target(f):return mapping[f['id']] if f is not None else None
 for filename,cases in source['legacy_cases_unchanged'].items():
  for old in cases:
   re=filename=='retained-earnings-primary-ledgers.json';eligible=old['eligibility']['status']=='eligible'
   doc=next((t['document_id'] for t in source['targets'] if old['case_id'].startswith(t['document_id']+':')),None)
   if doc is None:raise ValueError('Legacy case document identity missing')
   row={'case_id':old['case_id'],'document_id':doc,'family':'RE' if re else 'EPS','endpoint':'quarterly_supplement' if 'quarter' in filename else 'primary','source_applicable':eligible,'numeric_eligible':eligible,'legacy_source_case_unchanged':deepcopy(old),'ineligible_reason':old['eligibility'].get('reason'),'conditional_source_unit_basis':'Preserved legacy source-case facts; not local printed-metadata truth'}
   if re:
    row.update(begin_target_id=target(old['beginning']),end_target_id=target(old['ending']),terms=[{'target_id':target(m['fact']),'coefficient':m['direction'],'role':m['kind']} for m in old['movements']],source_complete_bridge=old['movements_complete'] is True and old['other_movements_complete'] is True,completeness_evidence=deepcopy(old['completeness_evidence']),dividend_policy=old['dividend_policy'],source_verified_absent_attestation=deepcopy(old['dividend_absence_evidence']) if old['dividend_policy']=='source_verified_absent' else None)
   else:
    row.update(variant=old['variant'],numerator_target_id=target(old['numerator']),shares_target_id=target(old['shares']),EPS_target_id=target(old['reported_eps']),monetary_multiplier=old['numerator'].get('scale') if old['numerator'] else None,share_multiplier=old['shares'].get('scale') if old['shares'] else None)
   result.append(row)
 return result

def compile_sources(source_populations,physical_map):
 """Inputs are already hash-verified source JSON objects, never predictions."""
 populations={p:{'targets':[],'cases':[]} for p in ('BSIS','EPSRE')};lookup={}
 for partition,data in source_populations.items():
  for pop,source in data.items():
   if pop not in populations:continue
   for target in source['targets']:
    row=legacy_numeric_target(target) if partition=='old10' and pop=='EPSRE' else deepcopy(target)
    if type(row.get('parsed_numeric')) is not bool:raise ValueError('Strict source numeric/marker boolean required')
    key=(partition,pop,row['target_id'])
    if key in lookup:raise ValueError('Duplicate source population identity')
    lookup[key]=row;populations[pop]['targets'].append(row)
   if partition=='old10' and pop=='EPSRE':cases=old_cases(source)
   elif partition=='old10':
    endpoints={rid:k for k,v in source['endpoints'].items() for rid in v['relation_ids']}
    cases=[]
    for c in source['relations']:
     row=deepcopy(c);endpoint=endpoints[c['relation_id']];row.update(family='BS' if row['statement']=='balance_sheet' else 'IS',endpoint='auxiliary' if endpoint.endswith(('auxiliary','_aux')) else 'primary');cases.append(row)
   else:cases=deepcopy(source.get('cases',source.get('candidates',[])))
   populations[pop]['cases'].extend(cases)
 physical=[];membership={};seen=set()
 for cell in physical_map['physical_cells']:
  ident=cell['physical_id']
  if ident in seen:raise ValueError('Duplicate physical union identity')
  seen.add(ident);representative=None;values=[]
  for member in cell['members']:
   key=(member['partition'],member['population'],member['target_id'])
   if key not in lookup or key in membership:raise ValueError('Source membership missing or repeated')
   row=lookup[key]
   if row['raw_value']!=member['original_raw_value'] or row['csv_selector']!=member['original_selector']:raise ValueError('Preserved source member payload differs')
   if row['parsed_numeric'] is not cell['parsed_numeric']:raise ValueError('Physical numeric/marker declarations disagree')
   membership[key]=ident;representative=representative or row
   if row['parsed_numeric']:values.append(Decimal(row['numeric_value_printed']))
  if representative is None or len(set(values))>1:raise ValueError('Structurally mapped physical source values disagree')
  row=deepcopy(representative);row['target_id']=ident;row['csv_selector']=deepcopy(cell['canonical_csv_selector']);physical.append(row)
 if set(membership)!=set(lookup):raise ValueError('Physical map must cover every source population member exactly')
 for pop,data in populations.items():
  targets={t['target_id']:t for t in data['targets']}
  if len(targets)!=len(data['targets']):raise ValueError('Duplicate within-population target IDs')
  for case in data['cases']:screens.validate_case_reference(case,targets)
 return {'populations':populations,'physical_targets':physical,'membership':membership,'source_populations':source_populations}

def score_union(records,compiled,inventory,*,source_anomalies=None):
 bound,states=binding.bind_targets(records,compiled['physical_targets'],inventory,source_anomalies=source_anomalies);results={}
 for pop,data in compiled['populations'].items():
  projected={}
  for (partition,population,target_id),physical_id in compiled['membership'].items():
   if population==pop:projected[target_id]={**deepcopy(bound[physical_id]),'target_id':target_id,'physical_union_target_id':physical_id}
  results[pop]=project_bound_population(records,data,projected,states)
 return {'populations':results,'physical_union_bindings':bound,'physical_union_record_disposition':states,'one_binding_per_source_physical_cell':True,'overlapping_population_denominators_not_summed':True}
