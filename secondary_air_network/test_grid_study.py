import copy
import json
import unittest
import tempfile
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

from engine import CalculationError, solve
from grid_study import compare_levels, compare_values, grid_study
from model import ModelError, rotating_sample, sample
from test_engine import network


class GridStudyTests(unittest.TestCase):
    def test_rotating_grids_preserve_input_and_compare_actual_results(self):
        m=rotating_sample(); before=copy.deepcopy(m)
        r=grid_study(m)
        self.assertEqual(m,before)
        self.assertEqual(r["status"],"completed")
        self.assertEqual([l["cells"]["radial"] for l in r["levels"]],[48,96,192])
        self.assertEqual(r["assessment"],"within_tolerance")
        row=next(v for v in r["metrics"] if v["id"]=="radial" and v["field"]=="mass_flow_kg_s")
        self.assertEqual(row["values"],[l["result"]["edges"][0]["mass_flow_kg_s"] for l in r["levels"]])
        self.assertLess(row["relative_percent"],.1)
        self.assertGreater(row["relative_percent"],0)
        self.assertEqual(r["levels"][0]["result"],solve(m))
        self.assertTrue(all(l["result"]["model"]["edges"][1]["kind"]=="orifice" for l in r["levels"]))
        json.dumps(r,allow_nan=False)

    def test_thermal_network_and_strict_comparison(self):
        m=sample();m["edges"][0].update(cells=8,htc_w_m2k=500)
        r=grid_study(m,.000001)
        self.assertEqual(r["status"],"completed")
        self.assertEqual(r["assessment"],"review_required")
        self.assertTrue(any(v["status"]=="review_required" for v in r["metrics"]))
        self.assertTrue(all(l["result"]["summary"]["heat_w"]!=0 for l in r["levels"]))

    def test_mixed_base_counts_and_no_silent_clipping(self):
        m=sample()
        for e in m["edges"]:
            if e["kind"]=="pipe":e["cells"]=64
        r=grid_study(m)
        self.assertTrue(all(v==256 for v in r["levels"][2]["cells"].values()))
        m["edges"][0]["cells"]=65
        with self.assertRaisesRegex(ModelError,"64"):grid_study(m)

    def test_nondiscretized_and_bad_tolerances_rejected(self):
        with self.assertRaises(ModelError):grid_study(network())
        for value in (0,-1,11,True,None,"0.1",float("nan"),float("inf")):
            with self.subTest(value=value),self.assertRaises(ModelError):grid_study(sample(),value)

    def test_absolute_floors_zero_signed_and_unknown(self):
        z=compare_values([0.,0.,0.],1e-8,.1)
        self.assertEqual(z["status"],"within_tolerance")
        self.assertIsNone(z["relative_percent"])
        near=compare_values([0,0,1e-10],1e-8,.1)
        self.assertEqual(near["status"],"within_tolerance")
        signed=compare_values([-3,-2,-1],1e-8,.1)
        self.assertEqual(signed["relative_percent"],50)
        self.assertEqual(signed["status"],"review_required")
        self.assertEqual(compare_values([None,None,None],.01,.1)["status"],"unavailable")
        self.assertEqual(compare_values([1,None,1],.01,.1)["status"],"unavailable")
        self.assertEqual(compare_values([1,2,1],.01,.1)["trend"],"oscillating")
        self.assertEqual(compare_values([1,2,4],.01,.1)["trend"],"growing")

    def test_state_changes_and_oscillations_require_review_even_with_loose_tolerance(self):
        result=solve(network())
        levels=[{"status":"converged","result":copy.deepcopy(result)} for _ in range(3)]
        levels[1]["result"]["edges"][0]["choked"]=not result["edges"][0]["choked"]
        levels[2]["result"]["edges"][0]["mass_flow_kg_s"]*=-1
        rows,changes,status=compare_levels(levels,10)
        self.assertEqual(status,"review_required")
        self.assertEqual({c["field"] for c in changes},{"choked","flow_direction"})
        levels=[{"status":"converged","result":copy.deepcopy(result)} for _ in range(3)]
        for l,p in zip(levels,(500000,500010,500005)):
            l["result"]["nodes"][0]["pressure_pa"]=p
        self.assertEqual(compare_levels(levels,10)[2],"review_required")

    def test_failed_grid_is_not_passed_or_saved_and_other_levels_run(self):
        original=solve(sample())
        with patch("grid_study.solve",side_effect=[original,CalculationError("test failure"),original]) as runner:
            report=grid_study(sample())
        self.assertEqual(runner.call_count,3)
        self.assertEqual(report["status"],"incomplete")
        self.assertEqual(report["assessment"],"incomplete")
        self.assertEqual(report["levels"][1]["error"],"test failure")
        self.assertNotIn("result",report["levels"][1])
        self.assertEqual(report["metrics"],[])

    def test_cli_incomplete_report_is_written_with_failure_exit(self):
        import cli
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/"input.json";dst=Path(tmp)/"report.json"
            src.write_text(json.dumps(sample()),encoding="utf-8")
            report={"status":"incomplete","assessment":"incomplete","levels":[{"status":"failed","error":"diagnostic"}]}
            with patch("sys.argv",["cli.py","--input",str(src),"--output",str(dst),"--grid-study"]),patch("cli.grid_study",return_value=report),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    cli.main()
            self.assertEqual(exit.exception.code,2)
            self.assertEqual(json.loads(dst.read_text(encoding="utf-8")),report)

    def test_cli_invalid_request_preserves_existing_output(self):
        import cli
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/"input.json";dst=Path(tmp)/"report.json"
            src.write_text(json.dumps(network()),encoding="utf-8")
            dst.write_text("preserve",encoding="utf-8")
            with patch("sys.argv",["cli.py","--input",str(src),"--output",str(dst),"--grid-study"]),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:cli.main()
            self.assertEqual(exit.exception.code,2)
            self.assertEqual(dst.read_text(encoding="utf-8"),"preserve")


if __name__=="__main__":unittest.main()
