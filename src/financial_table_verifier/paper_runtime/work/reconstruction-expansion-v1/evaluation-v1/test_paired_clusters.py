"""Synthetic-only cluster analysis. No actual source/model scores."""
import unittest
import paired_clusters as p

def rows():return [{'document_id':'A1','independent_correct':0,'sequential_correct':1,'independent_total':1,'sequential_total':1},{'document_id':'A2','independent_correct':10,'sequential_correct':9,'independent_total':10,'sequential_total':10},{'document_id':'B','independent_correct':0,'sequential_correct':1,'independent_total':1,'sequential_total':1}]
MAP={'A1':'1','A2':'1','B':'2'}
class ClusterTests(unittest.TestCase):
 def test_repeated_reports_aggregate_before_bootstrap_and_sign_test(self):
  out=p.cluster_counts(rows(),MAP,expected_reports=3,expected_clusters=2);self.assertEqual(len(out),2);self.assertEqual(out[0]['report_ids'],['A1','A2']);self.assertEqual(out[0]['independent_correct'],10);self.assertEqual(out[0]['sequential_correct'],10);test=p.exact_sign(out,expected_clusters=2);self.assertEqual(test['exact_ties'],1);self.assertEqual(test['positive_issuer_clusters'],1)
 def test_bootstrap_macro_equal_cluster_micro_targetweighted_are_distinct(self):
  out=p.analyze(rows(),MAP,expected_reports=3,expected_clusters=2);self.assertAlmostEqual(out['micro_target_weighted_difference_pp'],100/12);self.assertEqual(out['macro_equal_issuer_mean_difference_pp'],50);self.assertEqual(out['bootstrap']['resamples'],10000)
 def test_missing_failed_report_cannot_be_dropped(self):
  with self.assertRaises(ValueError):p.cluster_counts(rows()[:-1],MAP,expected_reports=3,expected_clusters=2)
 def test_duplicate_or_unknown_report_or_wrong_cluster_population_refuses(self):
  for rr in [rows()+[rows()[0]],rows()[:2]+[{**rows()[2],'document_id':'UNKNOWN'}]]:
   with self.assertRaises(ValueError):p.cluster_counts(rr,MAP,expected_reports=3,expected_clusters=2)
  with self.assertRaises(ValueError):p.cluster_counts(rows(),MAP,expected_reports=3,expected_clusters=3)
 def test_all_ties_untestable_p1_no_equivalence_claim(self):
  rr=rows()
  for row in rr:row['sequential_correct']=row['independent_correct']
  out=p.exact_sign(p.cluster_counts(rr,MAP,expected_reports=3,expected_clusters=2),expected_clusters=2);self.assertFalse(out['testable']);self.assertEqual(out['raw_p'],1);self.assertEqual(out['bonferroni_six_test_p'],1)
 def test_exact_symmetric_signs(self):
  out=p.exact_sign([{'issuer_id':str(i),'independent_correct':1 if i<3 else 0,'sequential_correct':0 if i<3 else 1,'independent_total':1,'sequential_total':1} for i in range(6)],expected_clusters=6);self.assertEqual(out['raw_p'],1)
 def test_source_CIK_leading_zeros_do_not_create_fake_independent_clusters(self):
  out=p.cluster_counts(rows(),{**MAP,'A2':'000001'},expected_reports=3,expected_clusters=2);self.assertEqual(out[0]['report_ids'],['A1','A2'])
 def test_clean_has_descriptive_bootstrap_but_no_extra_hypothesis_test(self):
  out=p.analyze(rows(),MAP,expected_reports=3,expected_clusters=2,variant='clean');self.assertIsNone(out['exact_cluster_sign_test_RAW_only']);self.assertTrue(out['hypothesis_tests_not_run_for_CLEAN']);self.assertEqual(out['bootstrap']['resamples'],10000)

if __name__=='__main__':unittest.main()
