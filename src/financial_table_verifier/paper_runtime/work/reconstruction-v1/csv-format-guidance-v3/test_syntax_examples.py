"""Format-only tests: no source values, predictions, transport or ledger."""
import copy
import csv
import io
import json
from pathlib import Path
import sys
import unittest

from syntax_examples import syntax_examples, HEADER_ROW, DATA_ROW

ROLES = ['printed_value', 'row_label', 'ancestor_path', 'row_role', 'period',
         'quantity_kind', 'scale', 'currency', 'source_column_ordinal',
         'source_column_label']


def plan(width=10, reverse=False):
    columns = [{'name': f'generated {i}, "{role}"', 'role': role}
               for i, role in enumerate(ROLES)]
    columns += [{'name': f'extra_{i}', 'role': 'uninterpreted'}
                for i in range(width-10)]
    if reverse:
        columns.reverse()
    return {'archetypes': [{'schema_id': f'format_{width}', 'columns': columns}]}


class SyntaxTests(unittest.TestCase):
    def decoded(self, example):
        return list(csv.reader(io.StringIO(example['csv'], newline=''), strict=True))

    def test_all_generated_widths_and_orders_roundtrip(self):
        for width in range(10, 17):
            for reverse in [False, True]:
                source = plan(width, reverse)
                before = copy.deepcopy(source)
                example = syntax_examples(source)[0]
                records = self.decoded(example)
                self.assertEqual(example['schema_id'], source['archetypes'][0]['schema_id'])
                self.assertEqual(len(records), 3)
                self.assertTrue(all(len(record) == width for record in records))
                columns = source['archetypes'][0]['columns']
                self.assertEqual(records[0], [c['name'] for c in columns])
                self.assertEqual(records[1], [HEADER_ROW.get(c['role'], '') for c in columns])
                self.assertEqual(records[2], [DATA_ROW.get(c['role'], '') for c in columns])
                self.assertEqual(source, before)

    def test_every_field_quoted_and_empty_fields_explicit(self):
        example = syntax_examples(plan(16))[0]
        # These fixed examples have no embedded literal newline in a field.
        for line in example['csv'].splitlines():
            self.assertRegex(line, r'^"(?:[^"]|"")*"(?:,"(?:[^"]|"")*")*$')
            # CSV parsing preserves every empty placeholder at its position.
            self.assertEqual(len(next(csv.reader([line], strict=True))), 16)
        self.assertIn(',"",', example['csv'])
        self.assertIn('""', example['csv'])

    def test_inner_quotes_comma_and_json_array_remain_single_fields(self):
        records = self.decoded(syntax_examples(plan())[0])
        row = dict(zip(ROLES, records[2]))
        self.assertEqual(row['row_label'], 'FORMAT ONLY, "quoted" label')
        self.assertEqual(json.loads(row['ancestor_path']), ['FORMAT ONLY, "ancestor"'])
        self.assertEqual(row['source_column_label'], 'FORMAT ONLY, "column" header')
        self.assertEqual(row['printed_value'], '1,234.50')
        self.assertIsInstance(row['printed_value'], str)
        self.assertEqual(row['source_column_ordinal'], '0')
        self.assertEqual(row['period'], '1999-12-31')

    def test_outer_json_dump_load_once_yields_actual_newlines(self):
        examples = syntax_examples(plan())
        envelope = {'tables': [{'schema_id': examples[0]['schema_id'],
                               'title': 'FORMAT ONLY', 'scope': None,
                               'csv': examples[0]['csv']}]}
        wire = json.dumps(envelope, ensure_ascii=False)
        decoded = json.loads(wire)
        actual_csv = decoded['tables'][0]['csv']
        self.assertEqual(actual_csv, examples[0]['csv'])
        self.assertIn('\n', actual_csv)
        self.assertNotIn('\\n', actual_csv)
        self.assertIn('\\n', wire)
        self.assertEqual(len(self.decoded({'csv': actual_csv})), 3)
        # An accidentally double-encoded CSV string does not equal the CSV text.
        self.assertNotEqual(json.loads(json.dumps(json.dumps(actual_csv))), actual_csv)

    def test_each_archetype_uses_its_own_schema_and_generated_header(self):
        one = plan(10)['archetypes'][0]
        two = plan(16, True)['archetypes'][0]
        examples = syntax_examples({'archetypes': [one, two]})
        self.assertEqual([e['schema_id'] for e in examples], ['format_10', 'format_16'])
        self.assertEqual([len(self.decoded(e)[0]) for e in examples], [10, 16])

    def test_validated_generated_plan_object_has_identical_output(self):
        study = next(p for p in Path(__file__).resolve().parents
                     if (p/'csv-first-v1/src').is_dir())
        sys.path.insert(0, str(study/'csv-first-v1/src'))
        from ftv_csv_reconstruction.contracts import SchemaPlan
        data = plan(16)
        data.update(contract_version='format-only-label', no_numeric_repair=True)
        archetype=data['archetypes'][0]
        archetype.update(statement='other',source_number_format_rules='FORMAT ONLY',hierarchy_rules='FORMAT ONLY')
        types={'ancestor_path':'json_array','row_role':'enum_string','quantity_kind':'enum_string',
               'scale':'integer_or_blank','source_column_ordinal':'integer_or_blank'}
        for c in archetype['columns']:
            c.update(data_type=types.get(c['role'],'string'),description='FORMAT ONLY')
        typed=SchemaPlan.model_validate(data)
        self.assertEqual(syntax_examples(typed),syntax_examples(typed.model_dump(mode='json')))

    def test_actual_strict_CSV_reader_accepts_all_examples(self):
        study = next(p for p in Path(__file__).resolve().parents
                     if (p/'csv-first-v1/src').is_dir())
        sys.path.insert(0, str(study/'csv-first-v1/src'))
        from ftv_csv_reconstruction.csv_io import parse_csv
        for width in range(10, 17):
            example = syntax_examples(plan(width))[0]
            records = parse_csv(example['csv'])
            self.assertEqual(len(records), 3)
            self.assertTrue(all(len(r['fields']) == width for r in records))


if __name__ == '__main__':
    unittest.main()
