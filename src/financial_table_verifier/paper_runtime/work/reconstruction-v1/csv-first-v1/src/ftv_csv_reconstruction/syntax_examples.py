"""Isolated prospective CSV syntax guidance. No source input or live integration.

The caller supplies an already validated generated schema plan. Examples illustrate
serialization only; toy values are fixed constants, never source answers.
"""
import csv
import io
import json

FORMAT_NOTICE = (
    'FORMAT ONLY — NOT SOURCE DATA. These fixed toy rows demonstrate CSV escaping '
    'and field positions only. Do not copy their labels, values, dates, units, '
    'currency, paths, or row roles into an extraction.'
)
HEADER_ROW = {
    'row_label': 'FORMAT ONLY, "section"',
    'ancestor_path': '[]',
    'row_role': 'Header',
    'source_column_label': '',
    'period': '',
    'printed_value': '',
    'quantity_kind': 'unknown',
    'scale': '',
    'currency': '',
    'source_column_ordinal': '',
}
DATA_ROW = {
    'row_label': 'FORMAT ONLY, "quoted" label',
    'ancestor_path': json.dumps(['FORMAT ONLY, "ancestor"'], ensure_ascii=False),
    'row_role': 'Data',
    'source_column_label': 'FORMAT ONLY, "column" header',
    'period': '1999-12-31',
    'printed_value': '1,234.50',
    'quantity_kind': 'money',
    'scale': '1',
    'currency': 'USD',
    'source_column_ordinal': '0',
}


def syntax_examples(plan):
    """Return one {schema_id,csv} example per archetype in original order.

    Accept a validated SchemaPlan or its JSON-mode dictionary. Column names/order
    and schema IDs pass through unchanged. Extra uninterpreted columns are empty;
    no field is dropped or padded from an actual model/source record.
    """
    data = plan.model_dump(mode='json') if hasattr(plan, 'model_dump') else plan
    examples = []
    for archetype in data['archetypes']:
        columns = archetype['columns']
        output = io.StringIO(newline='')
        writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\n')
        writer.writerow([column['name'] for column in columns])
        writer.writerow([HEADER_ROW.get(column['role'], '') for column in columns])
        writer.writerow([DATA_ROW.get(column['role'], '') for column in columns])
        examples.append({'schema_id': archetype['schema_id'], 'csv': output.getvalue()})
    return examples
