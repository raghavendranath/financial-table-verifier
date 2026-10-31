"""Prospective50-report/45-CIK paired analysis, no files or model calls.
Resample whole source-CIK clusters, retaining both filings of repeated issuers.
Numeric populations are separate; RAW6test family only, CLEAN diagnostics.
"""
from collections import defaultdict
from pathlib import Path
import math,sys
import re
OLD=Path(__file__).resolve().parents[2]/'reconstruction-v1'
sys.path.insert(0,str(OLD/'csv-evaluation-v1'))
from paired_analysis import paired_issuer_counts as frozen_bootstrap

FIELDS=('independent_correct','sequential_correct','independent_total','sequential_total')

def cluster_counts(report_rows,source_cik_by_document,*,expected_reports=50,expected_clusters=45):
 normalized={}
 for doc,value in source_cik_by_document.items():
  if not re.fullmatch('[0-9]+',str(value)) or int(value)<=0:raise ValueError('Source-verified positive CIK required; never accession-prefix guessing')
  normalized[doc]=str(int(value))
 if len(normalized)!=expected_reports or len(set(normalized.values()))!=expected_clusters:raise ValueError('Exact full source report/CIK population required')
 if len(report_rows)!=expected_reports or {r['document_id'] for r in report_rows}!=set(source_cik_by_document):raise ValueError('All source reports required exactly once, including failed/unavailable arms')
 grouped=defaultdict(lambda:{k:0 for k in FIELDS});members=defaultdict(list)
 for r in report_rows:
  if any(type(r[k]) is not int or r[k]<0 for k in FIELDS) or r['independent_total']<=0 or r['independent_total']!=r['sequential_total'] or any(r[k]>r['independent_total'] for k in ('independent_correct','sequential_correct')):raise ValueError('Positive equal fixed report denominators and valid counts required')
  cik=normalized[r['document_id']]
  members[cik].append(r['document_id'])
  for k in FIELDS:grouped[cik][k]+=r[k]
 return [{'issuer_id':cik,'source_CIK':cik,'report_ids':sorted(members[cik]),**grouped[cik]} for cik in sorted(grouped)]

def exact_sign(rows,*,expected_clusters=45):
 if len(rows)!=expected_clusters or len({r['issuer_id'] for r in rows})!=expected_clusters:raise ValueError('Distinct source-CIK pairs required')
 positive=negative=ties=0
 for r in rows:
  if any(type(r[k]) is not int or r[k]<0 for k in FIELDS) or r['independent_total']<=0 or r['independent_total']!=r['sequential_total'] or any(r[k]>r['independent_total'] for k in ('independent_correct','sequential_correct')):raise ValueError('Invalid fixed issuer paired counts')
  delta=r['sequential_correct']-r['independent_correct'];positive+=delta>0;negative+=delta<0;ties+=delta==0
 n=positive+negative;tail=sum(math.comb(n,k) for k in range(min(positive,negative)+1));probability=min(1.,2*tail/(2**n))
 return {'test':'exact two-sided source-CIK-paired sign test','positive_issuer_clusters':positive,'negative_issuer_clusters':negative,'exact_ties':ties,'nonzero_issuer_pairs':n,'testable':n>0,'raw_p':probability,'bonferroni_six_test_p':min(1.,6*probability),'prespecified_family_tests':6,'family':'3models x2 separate overlapping RAWnumeric populations; no extra CLEAN hypothesis tests','interpretation':'Convenience reports/prior source exposure/shared guidance and one model run per condition limit generalization. Cluster signs, not cells or reports, are the independent units under the test model.'}

def analyze(report_rows,source_cik_by_document,*,expected_reports=50,expected_clusters=45,variant='raw'):
 if variant not in {'raw','clean'}:raise ValueError('Only RAW primary or CLEAN diagnostic variants')
 rows=cluster_counts(report_rows,source_cik_by_document,expected_reports=expected_reports,expected_clusters=expected_clusters)
 result=frozen_bootstrap(rows,expected_issuers=expected_clusters)
 result.update(report_pairs=expected_reports,source_CIK_cluster_pairs=expected_clusters,variant=variant,exact_cluster_sign_test_RAW_only=exact_sign(rows,expected_clusters=expected_clusters) if variant=='raw' else None,hypothesis_tests_not_run_for_CLEAN=variant=='clean',interpretation=f'{expected_reports} convenience reports from {expected_clusters} source-CIK clusters, prior study exposure and one run per condition. No cell/report-independent inference, population representativeness, equivalence or universal context benefit follows.')
 result['bootstrap']['unit']='whole source-CIK paired cluster with replacement; every filing of a repeated issuer retained together'
 return result
