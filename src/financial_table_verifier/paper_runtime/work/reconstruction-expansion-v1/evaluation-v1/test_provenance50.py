from copy import deepcopy
import unittest
import sys,tempfile,json
import provenance50 as p

class ProtectedTests(unittest.TestCase):
 def record(self):return {'schema_id':'S','raw_fields':['Revenue','unknown','100','2023-12-31'],'roles':{'row_label':'Revenue','row_role':'unknown','printed_value':'100','period':'2023-12-31'},'raw_value':'100','period':'2023-12-31','row_id':'actual-r1','source_cell_id':'actual-cell','metadata_issues':[]}
 def test_only_actual_generated_role_index_ignored_not_numeric_or_date_fields(self):
  a=self.record();b=deepcopy(a);b['roles']['row_role']='Data';b['raw_fields'][1]='Data';self.assertEqual(p.protected_records([a],{'S':1}),p.protected_records([b],{'S':1}))
  for field,value in [('raw_value','101'),('period','2022-12-31'),('row_id','other')]:
   c=deepcopy(b);c[field]=value;self.assertNotEqual(p.protected_records([a],{'S':1}),p.protected_records([c],{'S':1}))
 def test_amount_field_cannot_be_ignored_by_claiming_wrong_role_index(self):
  with self.assertRaises(ValueError):p.protected_records([self.record()],{'S':2})
 def test_raw_field_and_role_date_change_detected_even_if_projection_unchanged(self):
  a=self.record();b=deepcopy(a);b['raw_fields'][3]='2022-12-31';b['roles']['period']='2022-12-31';self.assertNotEqual(p.protected_records([a],{'S':1}),p.protected_records([b],{'S':1}))

class ActualCannedReplayTests(unittest.TestCase):
 def setUp(self):
  sys.path.insert(0,str(p.OLD/'csv-first-v1/tests'))
  from test_csv_first import fixture,Canned,plan_data,generated_prompt,table
  from ftv_csv_reconstruction.preparation import prepare_document
  from ftv_csv_reconstruction.pipeline import extract_document
  from ftv_csv_reconstruction.artifacts import write_json
  self.temp=tempfile.TemporaryDirectory();self.root=p.Path(self.temp.name);self.config=fixture(self.root);self.path=self.root/'config.json';write_json(self.path,self.config.model_dump(mode='json'));self.ref={'path':str(self.path),'sha256':p.sha(self.path)};prepared=prepare_document(self.config,Canned([plan_data(),generated_prompt]));extract_document(self.config,prepared,Canned([{'tables':[table()]}]*3))
 def tearDown(self):self.temp.cleanup()
 def test_actual_unchanged_loader_reconstructs_canned_source_schema_requests_context_and_cleaning(self):
  before={str(f):p.sha(f) for f in self.root.rglob('*') if f.is_file()};out=p.load_pair(self.ref);self.assertTrue(out['raw_clean_protected_fields_unchanged']);self.assertEqual(len(out['records']['raw']),3);self.assertEqual(out['records']['clean'][0]['roles']['row_role'],'Data');self.assertEqual(before,{str(f):p.sha(f) for f in self.root.rglob('*') if f.is_file()})
 def test_coordinated_actual_request_and_all_local_hashes_rewrite_cannot_replace_source_inputs(self):
  from ftv_csv_reconstruction.artifacts import read_json,write_json,digest,file_hash
  directory=self.config.output/'pages/p0001';request=read_json(directory/'call/request.json');request['messages'][0]['content']='TAMPERED';write_json(directory/'call/request.json',request);call=read_json(directory/'call/call.json');call.update(request_body_sha256=digest(request),request_file_sha256=file_hash(directory/'call/request.json'));write_json(directory/'call/call.json',call);record=read_json(directory/'page-record.json');record['request_body_sha256']=digest(request);record['call'].update(request_body_sha256=digest(request),request_file_sha256=file_hash(directory/'call/request.json'));write_json(directory/'page-record.json',record)
  with self.assertRaisesRegex(ValueError,'reconstructed'):p.load_pair(self.ref)
 def test_config_without_all_source_images_rejects_before_renderer_or_loader(self):
  from ftv_csv_reconstruction.artifacts import read_json,write_json
  data=read_json(self.path);data['selected_images'].pop('1');write_json(self.path,data);self.ref['sha256']=p.sha(self.path)
  with self.assertRaisesRegex(ValueError,'pinned source image'):p.load_pair(self.ref)

if __name__=='__main__':unittest.main()
