import unittest
from ftv_csv_reconstruction.auxiliary import score_html, teds_provenance


def html(rows):
    return "<html><body><table>"+"".join("<tr>"+"".join("<td>"+v+"</td>" for v in row)+"</tr>" for row in rows)+"</table></body></html>"


class OfficialHTMLTests(unittest.TestCase):
    def test_exact_official_metric_and_revision(self):
        source = html([["A", "1"], ["B", "2"]])
        result = score_html(source, source)
        self.assertEqual(result["teds"], 1.0)
        self.assertEqual(result["teds_structure"], 1.0)
        self.assertEqual(teds_provenance()["revision"], "8ffde9024bd331f5a61c3c549fdf30dd3c3e43a5")

    def test_partial_cells_get_normal_tree_edit_score_not_fullpage_gate(self):
        result = score_html(html([["A", "1"], ["B"]]), html([["A", "1"], ["B", "2"]]))
        self.assertGreater(result["teds"], 0)
        self.assertLess(result["teds"], 1)
        self.assertGreater(result["teds_structure"], 0)
        self.assertLess(result["teds_structure"], 1)

    def test_value_changes_affect_content_not_structure(self):
        result = score_html(html([["A", "999"]]), html([["A", "1"]]))
        self.assertLess(result["teds"], 1)
        self.assertEqual(result["teds_structure"], 1)

    def test_no_table_and_missing_prediction_score_zero(self):
        source = html([["A", "1"]])
        self.assertEqual(score_html("", source)["teds"], 0)
        self.assertEqual(score_html("<html><body>unavailable</body></html>", source)["teds"], 0)


if __name__ == "__main__":
    unittest.main()
