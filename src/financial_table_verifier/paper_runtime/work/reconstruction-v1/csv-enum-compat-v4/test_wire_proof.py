import tempfile
from pathlib import Path
import unittest
from replay_development import SavedOnlyTransport,contract_snapshot,ACTIVE,OLD
from ftv_csv_reconstruction.artifacts import write_json,write_bytes,file_hash,digest

class WireProofTests(unittest.TestCase):
    def test_all_four_strict_wire_schemas_and_instructions_unchanged(self):
        old,new=contract_snapshot(OLD),contract_snapshot(ACTIVE)
        self.assertEqual(old['schemas'],new['schemas']);self.assertEqual(old['prompts'],new['prompts'])
        self.assertNotEqual(old['procedure'],new['procedure'])
    def test_saved_only_transport_rejects_wrong_request_and_unexpected_network_route(self):
        t=SavedOnlyTransport([])
        with self.assertRaises(ValueError):t.request('GET','key')
        with self.assertRaises(ValueError):t.request('POST','chat/completions',{})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);request={'x':1};response={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
            write_json(p/'request.json',request);write_json(p/'response.json',response);write_bytes(p/'model-content.utf8',b'{}')
            import hashlib
            write_json(p/'call.json',{'request_file_sha256':file_hash(p/'request.json'),'request_body_sha256':digest(request),
                'response_file_sha256':file_hash(p/'response.json'),'response_sha256':digest(response),
                'model_content_sha256':hashlib.sha256(b'{}').hexdigest()})
            t=SavedOnlyTransport([p])
            with self.assertRaises(ValueError):t.request('POST','chat/completions',{'x':2})
            self.assertEqual(t.index,0)
            self.assertEqual(t.request('POST','chat/completions',request),response)
            t.complete()
            with self.assertRaises(ValueError):t.request('POST','chat/completions',request)

if __name__=='__main__':unittest.main()
