"""Prospective paired issuer bootstrap; no network, files or equivalence testing."""
import random,math

SEED=20261026
RESAMPLES=10000
CONFIDENCE=.95

def _quantile(values,p):
 ordered=sorted(values);position=(len(ordered)-1)*p;lower=math.floor(position);upper=math.ceil(position)
 return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower)

def paired_issuer_counts(rows,*,expected_issuers=10):
 """Rows hold matched fixed-source counts for I/S; resample whole issuers.

Returns S-I percentage points, both source-target-weighted (micro) and equal
issuer-weighted (macro). Intervals are unadjusted percentile summaries, not
hypothesis/equivalence tests or evidence of population representativeness.
 """
 rows=sorted(rows,key=lambda r:r['issuer_id'])
 if len(rows)!=expected_issuers or len({r['issuer_id'] for r in rows})!=expected_issuers:raise ValueError('Unique paired issuer population differs')
 for row in rows:
  for field in ('independent_correct','sequential_correct','independent_total','sequential_total'):
   if type(row[field]) is not int or row[field]<0:raise ValueError('Counts must be nonnegative integers')
  if row['independent_total']<=0 or row['independent_total']!=row['sequential_total']:raise ValueError('Each issuer requires equal positive fixed denominators')
  if row['independent_correct']>row['independent_total'] or row['sequential_correct']>row['sequential_total']:raise ValueError('Correct count exceeds denominator')
 def estimates(selected):
  denom=sum(r['independent_total'] for r in selected)
  micro=100*sum(r['sequential_correct']-r['independent_correct'] for r in selected)/denom
  macro=100*sum((r['sequential_correct']-r['independent_correct'])/r['independent_total'] for r in selected)/len(selected)
  return micro,macro
 micro,macro=estimates(rows);rng=random.Random(SEED);micro_samples=[];macro_samples=[]
 for _ in range(RESAMPLES):
  chosen=[rows[rng.randrange(len(rows))] for _ in rows];mi,ma=estimates(chosen);micro_samples.append(mi);macro_samples.append(ma)
 alpha=(1-CONFIDENCE)/2
 return {'issuer_pairs':len(rows),'rows':rows,'contrast':'sequential minus independent percentage points',
  'micro_target_weighted_difference_pp':micro,'macro_equal_issuer_mean_difference_pp':macro,
  'micro_issuer_bootstrap_95ci_pp':[_quantile(micro_samples,alpha),_quantile(micro_samples,1-alpha)],
  'macro_issuer_bootstrap_95ci_pp':[_quantile(macro_samples,alpha),_quantile(macro_samples,1-alpha)],
  'bootstrap':{'resamples':RESAMPLES,'seed':SEED,'unit':'whole issuer pair with replacement','interval':'linear-interpolated percentile','multiplicity_adjustment':'none; descriptive unadjusted intervals'},
  'interpretation':'Ten convenience issuers and one run per condition; no population representativeness, equivalence, parity or universal context benefit follows. An interval crossing zero is not evidence of no difference.'}


def exact_issuer_sign_test(rows):
 """Six-test family: production invokes only primary RAW627/106 for3models."""
 if len(rows)!=10 or len({r['issuer_id'] for r in rows})!=10:raise ValueError('Sign test requires ten distinct paired issuers')
 positive=negative=ties=0
 for r in rows:
  if (type(r['independent_total']) is not int or type(r['sequential_total']) is not int or r['independent_total']<=0 or r['independent_total']!=r['sequential_total']
      or any(type(r[k]) is not int or not 0<=r[k]<=r['independent_total'] for k in ('independent_correct','sequential_correct'))):raise ValueError('Invalid fixed paired counts')
  delta=r['sequential_correct']-r['independent_correct']
  positive+=delta>0;negative+=delta<0;ties+=delta==0
 n=positive+negative;tail=sum(math.comb(n,k) for k in range(min(positive,negative)+1));p=min(1.0,2*tail/(2**n))
 return {'test':'exact two-sided issuer-paired sign test','contrast':'S minus I issuer numeric recall; exact ties dropped',
  'positive_issuers':positive,'negative_issuers':negative,'exact_ties':ties,'nonzero_issuer_pairs':n,'testable':n>0,
  'raw_p':p,'bonferroni_six_test_p':min(1.0,6*p),'prespecified_family_tests':6,
  'all_ties_note':'All ties are untestable; the formula returns1 by convention' if n==0 else None,
  'assumptions_and_limits':'Independent issuer signs under a fair-sign null; ten convenience issuers, shared preparation and single-pass model service outcomes limit inference. No cell-level independence, detector population accuracy, equivalence or parity follows.'}
