"""Independent source/gold-free contract probes. All transports are canned."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_csv_first import Canned, fixture, generated_prompt, plan_data, table
from ftv_csv_reconstruction.artifacts import file_hash
from ftv_csv_reconstruction.auxiliary import extract_html
from ftv_csv_reconstruction.cleaning import clean_page
from ftv_csv_reconstruction.contracts import ImageAsset, SchemaPlan
from ftv_csv_reconstruction.csv_io import parse_csv, parse_page, serialize
from ftv_csv_reconstruction.pipeline import extract_document
from ftv_csv_reconstruction.preparation import prepare_document


def replace_role(table_value, plan, role, value):
    output=deepcopy(table_value)
    rows=[r['fields'] for r in parse_csv(output['csv'])]
    index=next(i for i,c in enumerate(plan.archetypes[0].columns) if c.role==role)
    rows[1][index]=value
    output['csv']=serialize(rows)
    return output


class IndependentCSVAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.plan=SchemaPlan.model_validate(plan_data())

    def test_oversized_ordinal_is_local_unknown_not_page_exception(self):
        raw=replace_role(table(),self.plan,'source_column_ordinal','9'*5000)
        parsed=parse_page({'tables':[raw,table(value='42')]},self.plan,document_id='synthetic',page=1)
        self.assertEqual([r['raw_value'] for r in parsed['records']],['$ (1,234)','42'])
        self.assertIsNone(parsed['records'][0]['column_index'])
        self.assertTrue(parsed['records'][0]['metadata_issues'])

    def test_excessively_nested_ancestry_is_local_unknown_not_page_exception(self):
        raw=replace_role(table(),self.plan,'ancestor_path','['*10000+'0'+']'*10000)
        parsed=parse_page({'tables':[raw,table(value='42')]},self.plan,document_id='synthetic',page=1)
        self.assertEqual(len(parsed['records']),2)
        self.assertEqual(parsed['records'][0]['raw_value'],'$ (1,234)')
        self.assertIn('invalid_ancestor_path',parsed['records'][0]['metadata_issues'])

    def test_arbitrary_generated_names_and_order_are_actual_header_contract(self):
        value=plan_data()
        columns=value['archetypes'][0]['columns']
        for i,c in enumerate(columns):c['name']=f'任意 Generated {i}, "field"'
        value['archetypes'][0]['columns']=list(reversed(columns))
        plan=SchemaPlan.model_validate(value)
        original=parse_page({'tables':[table()]},self.plan,document_id='x',page=1)['records'][0]['roles']
        fields=[original[c.role] for c in plan.archetypes[0].columns]
        emitted={'schema_id':'generated-other','title':'source','scope':None,
                 'csv':serialize([[c.name for c in plan.archetypes[0].columns],fields])}
        parsed=parse_page({'tables':[emitted]},plan,document_id='x',page=1)
        self.assertEqual(parsed['records'][0]['raw_value'],'$ (1,234)')
        self.assertEqual(parsed['records'][0]['quantity'],'money')
        self.assertTrue(parsed['tables'][0]['header_matches_schema'])

    def test_bad_optional_metadata_never_changes_printed_strings_during_cleaning(self):
        raw=table(value='−€ (1,234.50)ᵃ\nwrapped',label='Total source amount',scale='3',period='not-an-ISO-period')
        raw['title']={'invalid':'title'}
        raw['scope']=['invalid scope']
        raw=replace_role(raw,self.plan,'quantity_kind','a numeric amount 999')
        original=deepcopy(raw)
        cleaned,_,audit=clean_page({'tables':[raw]},self.plan,document_id='x',page=1)
        parsed=parse_page(cleaned,self.plan,document_id='x',page=1)
        self.assertEqual(raw,original)
        self.assertEqual(parsed['records'][0]['raw_value'],'−€ (1,234.50)ᵃ\nwrapped')
        self.assertIsNone(parsed['records'][0]['scale'])
        self.assertIn('invalid_quantity_kind',parsed['records'][0]['metadata_issues'])
        self.assertTrue(audit['protected_fields_unchanged'])

    def test_blank_records_and_invalid_neighbors_are_preserved_locally(self):
        raw=table(extra='\r\nshort,record\r\n')
        parsed=parse_page({'tables':[False,{'schema_id':'x','csv':7},raw]},self.plan,document_id='x',page=1)
        self.assertEqual(len(parsed['records']),1)
        self.assertEqual([r['fields'] for r in parsed['tables'][2]['records'][-2:]],[[],['short','record']])
        self.assertEqual(len(parsed['tables']),3)

    def test_auxiliary_rejects_different_image_before_any_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config=fixture(root,selected=(1,2))
            prepared=prepare_document(config,Canned([plan_data(),generated_prompt]))
            extract_document(config,prepared,Canned([{'tables':[table()]},{'tables':[table(value='42')]}]))
            substitute=root/'different-image.png';substitute.write_bytes(b'different source image')
            assets=dict(config.selected_images);assets[2]=ImageAsset(path=substitute,sha256=file_hash(substitute))
            aux=config.model_copy(update={'output':root/'auxiliary','selected_pages':[2],'selected_images':assets})
            transport=Canned([{'tables':[{'title':'source','html':'<html><body><table><tr><td>x</td></tr></table></body></html>'}]}])
            with self.assertRaises(ValueError):extract_html(aux,config.output,transport)
            self.assertEqual(transport.requests,[])


if __name__=='__main__':unittest.main()
