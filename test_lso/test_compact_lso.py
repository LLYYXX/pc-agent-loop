from __future__ import annotations
import inspect, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
if str(MEMORY_ROOT) not in sys.path: sys.path.insert(0, str(MEMORY_ROOT))

import local_semantic_overlay as lso
from local_semantic_overlay import ga_multiagent, overlay, runner, search
from local_semantic_overlay import document_extract
from local_semantic_overlay import select as selector
from local_semantic_overlay.verify_lines import measure

class CompactLsoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(); self.scope = self._td.name
        self._patch = mock.patch.object(overlay, "OVERLAY_DIR", Path(self._td.name) / "overlays"); self._patch.start()
    def tearDown(self) -> None:
        self._patch.stop(); self._td.cleanup()
    def _file(self, rel: str, text: str) -> str:
        p = Path(self.scope) / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8"); return str(p)
    def _zip(self, rel: str, files: dict[str, str]) -> str:
        import zipfile
        p = Path(self.scope) / rel; p.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p, "w") as z:
            for name, text in files.items(): z.writestr(name, text)
        return str(p)

    def test_line_budgets_and_no_unit_control_plane(self):
        m = measure(); self.assertLess(m["runner"], 300); self.assertLess(m["final"], 600); self.assertGreater(m["search"], 0)
        source = inspect.getsource(runner)
        for term in ("unit_roots", "anchor_refine", "split_children", "_neighborhood", "scope_census"):
            self.assertNotIn(term, source)

    def test_selector_kernel_discovers_and_filters_bucketed_candidates(self):
        readme = self._file("proj/README.md", "Project overview.")
        recent = self._file("proj/notes.md", "Recent user-facing notes.")
        maintained = self._file("proj/maintained.md", "Long maintained notes.")
        ambiguous = self._file("proj/trace-template.md", "User-authored trace template.")
        win_recent = self._file("docs/win-recent.md", "Opened through Windows Recent.")
        noise = self._file("proj/app.log", "software generated log")
        def fake_search(query: str, scope: str | None = None, limit: int = 50, **kw):
            if query.startswith("maintained:"): return [{"path": maintained}]
            if query.startswith("d"): return [{"path": recent}, {"path": ambiguous}, {"path": noise}]
            if query == "readme": return [{"path": readme}]
            return []
        with mock.patch.object(selector, "search_rows", side_effect=fake_search), \
             mock.patch.object(selector, "_windows_recent", return_value=[win_recent]), \
             mock.patch.object(selector, "_maintained", return_value=[maintained]), \
             mock.patch.object(selector, "flat_parents_too_large", side_effect=lambda parents, threshold: {selector.norm_path(p): False for p in parents}):
            candidates = lso.discover_candidates(self.scope, query="project", limit=10)
        paths = {Path(c["path"]).name: c for c in candidates}
        self.assertIn("README.md", paths); self.assertIn("notes.md", paths); self.assertNotIn("app.log", paths)
        self.assertIn("trace-template.md", paths)
        self.assertEqual(paths["README.md"]["reason"], "project_marker")
        self.assertEqual(paths["win-recent.md"]["reason"], "windows_recent")
        self.assertEqual(paths["maintained.md"]["reason"], "long_maintained")

    def test_discovery_union_is_not_globally_truncated_after_bucket_recall(self):
        files = [self._file(f"d/{i}.md", str(i)) for i in range(3)]
        found = [{"path": p, "signals": ["docs"]} for p in files]
        with mock.patch.object(selector, "discover_paths", return_value=found), \
             mock.patch.object(selector, "flat_parents_too_large", side_effect=lambda parents, threshold: {selector.norm_path(p): False for p in parents}):
            out = selector.discover_candidates(self.scope, seeds=[str(REPO_ROOT / "agentmain.py")], limit=1)
        self.assertEqual({x["path"] for x in out}, {str(Path(p).resolve()) for p in files})

    def test_flat_dense_parent_is_cold_start_downgrade_only(self):
        cold = self._file("dump/a.md", "bulk")
        cold2 = self._file("dump/b.md", "bulk")
        seed = self._file("dump/valuable.md", "explicit hit")
        with mock.patch.object(selector, "discover_paths", return_value=[{"path": cold, "signals": ["docs"]}, {"path": cold2, "signals": ["docs"]}]), \
             mock.patch.object(selector, "flat_parents_too_large", side_effect=lambda parents, threshold: {selector.norm_path(p): True for p in parents}) as flat:
            self.assertEqual(selector.discover_candidates(self.scope), [])
            self.assertEqual(flat.call_count, 1)
            self.assertEqual([x["path"] for x in selector.select_for_read([seed], seeds=[seed], limit=None)], [str(Path(seed).resolve())])

    def test_large_document_survives_as_metadata_only_evidence(self):
        p = Path(self.scope) / "report.pdf"; p.write_bytes(b"x" * (selector.SIZE_CAP_BYTES + 1))
        out = selector.select_for_read([str(p)], source_signals={str(p): ["docs"]}, limit=None)
        self.assertEqual(out[0]["evidence_kind"], "metadata_only")
        raw = Path(self.scope) / "1.bin"; raw.write_bytes(b"x")
        self.assertEqual(selector.select_for_read([str(raw)], source_signals={str(raw): ["recent"]}, limit=None)[0]["evidence_kind"], "filename_only")

    def test_document_extract_is_lightweight_office_evidence_channel(self):
        docx = self._zip("a.docx", {"word/document.xml": "<document><t>Invoice Alpha</t></document>"})
        pptx = self._zip("b.pptx", {"ppt/slides/slide1.xml": "<sld><t>Roadmap Slide</t></sld>"})
        xlsx = self._zip("c.xlsx", {"xl/sharedStrings.xml": "<sst><si><t>Budget Cell</t></si></sst>", "xl/worksheets/sheet1.xml": "<ws><c t='s'><v>0</v></c></ws>"})
        self.assertIn("Invoice Alpha", document_extract.extract_text(docx)["text"])
        self.assertIn("Roadmap Slide", document_extract.extract_text(pptx)["text"])
        self.assertIn("Budget Cell", document_extract.extract_text(xlsx)["text"])
        legacy = Path(self.scope) / "legacy.doc"; legacy.write_bytes(b"old office")
        self.assertEqual(document_extract.extract_text(str(legacy))["error"], "legacy_office_unsupported")

    def test_search_rows_unbounded_is_plain_es_result(self):
        class FakeRun:
            returncode = 0
            stdout = "F:/docs/a.txt\nF:/docs/b.pdf\n"
        with mock.patch.object(search, "ensure_search_ready", return_value={"ok": True, "es_path": "D:/Everything/es.exe"}), \
             mock.patch("local_semantic_overlay.search.subprocess.run", return_value=FakeRun()) as run, \
             mock.patch.object(search, "_info", side_effect=AssertionError("stat path should not run")):
            rows = search.search_rows("file:*", scope="F:/", limit=None, with_info=False)
        self.assertEqual(len(rows), 2); self.assertNotIn("-n", run.call_args.args[0])

    def test_search_tool_discovery_is_remembered_without_duplicate_sections(self):
        p = Path(self.scope) / "global_mem.txt"; p.write_text("## [LOCAL_TOOLS]\nes.exe=old\n", encoding="utf-8")
        with mock.patch.object(search, "GLOBAL_MEM_PATH", p):
            search._remember("es.exe", "D:/Everything/es.exe"); search._remember("Everything.exe", "D:/Everything/Everything.exe")
        text = p.read_text(encoding="utf-8")
        self.assertEqual(text.count("## [LOCAL_TOOLS]"), 1)
        self.assertIn("es.exe=D:/Everything/es.exe", text); self.assertIn("Everything.exe=D:/Everything/Everything.exe", text)

    def test_windows_recent_and_maintained_are_es_backed_signals(self):
        target = str(Path(self.scope) / "doc.md")
        with mock.patch.dict("local_semantic_overlay.select.os.environ", {"APPDATA": "C:/Users/u/AppData/Roaming"}), \
             mock.patch.object(selector, "search_rows", return_value=[{"path": "C:/Users/u/AppData/Roaming/Microsoft/Windows/Recent/doc.lnk"}]), \
             mock.patch.object(selector, "resolve_lnk", return_value=[target, "C:/outside.txt"]):
            self.assertEqual(selector._windows_recent(self.scope, 50), [str(Path(target).resolve())])
        short_lived = str(Path(self.scope) / "old-but-not-maintained.md")
        maintained = [{"path": short_lived}, {"path": target}]
        stats = {
            short_lived: mock.Mock(st_ctime=0, st_mtime=31 * 86400),
            target: mock.Mock(st_ctime=0, st_mtime=366 * 86400),
        }
        with mock.patch.object(selector, "iter_column_rows", return_value=iter(maintained)) as rows, \
             mock.patch.object(selector.os, "stat", side_effect=lambda p: stats[p]):
            self.assertEqual(selector._maintained(self.scope, 5, "maintained:180d"), [target])
        rows.assert_called_once_with("*", scope=self.scope, columns=[], limit=None, sort="date-modified-descending", files_only=True)

    def test_discovery_queries_use_value_axis_sorting(self):
        with mock.patch.object(selector, "search_rows", return_value=[]) as rows:
            selector._discover("dm:last14days", self.scope, 10)
            self.assertEqual(rows.call_args.kwargs["sort"], "date-modified-descending")
            self.assertTrue(rows.call_args.kwargs["files_only"]); self.assertFalse(rows.call_args.kwargs["with_info"])
            selector._discover("dc:last14days", self.scope, 10)
            self.assertEqual(rows.call_args.kwargs["sort"], "date-created-descending")

    def test_candidate_batches_then_independent_compressor(self):
        readme = self._file("proj/README.md", "Project overview.")
        note = self._file("docs/report.md", "Readable report.")
        noise = self._file("docs/generated.tmp", "generated")
        candidates = [
            {"path": readme, "signals": ["project_marker"], "evidence_kind": "content"},
            {"path": note, "signals": ["docs"], "evidence_kind": "document"},
            {"path": noise, "signals": ["recent_modified"], "evidence_kind": "content"},
        ]
        with mock.patch.object(runner, "discover_candidates", return_value=candidates), \
             mock.patch.dict(runner.CONFIG, {"selector_batch_size": 2}):
            prep = lso.prepare(self.scope, question="build common-file map", reset=True)
            active = lso.prepare(self.scope, question="must not overwrite")
            self.assertEqual(active["error"], "build_in_progress")
            self.assertEqual(prep["task"]["batch"], {"offset": 0, "size": 2, "total": 3})
            self.assertNotIn("census", prep); self.assertNotIn("unit_roots", prep["task"])
            incomplete = lso.apply_stage(self.scope, {"role": "selector", "retained": [{"path": readme, "reason": "project marker"}]})
            self.assertEqual(incomplete["error"], "candidate_batch_not_consumed")
            bad_discard = lso.apply_stage(self.scope, {"role": "selector", "retained": [
                {"path": readme, "reason": "high organization signal"}
            ], "discarded": [{"path": note, "reason": "low value"}]})
            self.assertEqual(bad_discard["error"], "discard_missing_noise_evidence")
            first = lso.apply_stage(self.scope, {"role": "selector", "retained": [
                {"path": readme, "reason": "high organization signal"}, {"path": note, "reason": "user document"}
            ], "discarded": []})
            self.assertTrue(first["ok"]); self.assertEqual(first["next_task"]["batch"]["offset"], 2)
            second = lso.apply_stage(self.scope, {"role": "selector", "retained": [], "discarded": [
                {"path": noise, "reason": "software-generated temporary file", "noise_evidence": "generated temporary suffix"}
            ]})
        self.assertTrue(second["ok"]); self.assertEqual(second["next_task"]["role"], "compressor")
        low_retention = next(x for x in lso.coverage_audit(self.scope)["issues"] if x["type"] == "retained_below_target")
        self.assertEqual(low_retention["retained"], 2)
        leaves = overlay.load(self.scope)["leaves"]; by_name = {Path(v["path"]).name: k for k, v in leaves.items()}
        comp = lso.apply_stage(self.scope, {"role": "compressor", "targets": [{
            "target_id": "project_node", "target_type": "directory", "label": "Project",
            "boundary": str(Path(readme).parent), "brief": "README supports a cohesive project boundary.",
            "supporting_paths": [readme],
        }], "standalone_leaf_ids": [by_name["report.md"]]})
        self.assertTrue(comp["ok"]); self.assertEqual(comp["next_task"]["role"], "tagger")
        node = overlay.load(self.scope)["nodes"]["project_node"]
        self.assertEqual(node["supporting_leaf_ids"], [by_name["README.md"]])
        self.assertEqual(node["boundary"], str(Path(readme).parent))

    def test_role_pipeline_builds_multilayer_queryable_overlay(self):
        p1 = self._file("svc/routing.md", "Routing tables guide service ownership.")
        p2 = self._file("svc/deploy.md", "Deployment review uses routing tables.")
        runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "selector", "question": "", "selector_offset": 0})
        runner._rw(self.scope, "candidate_pool.json", {"count": 2, "items": [{"path": p1}, {"path": p2}]})
        runner._rw(self.scope, "selector_ledger.json", {"batches": []})
        sel = lso.apply_stage(self.scope, {"role": "selector", "retained": [
            {"path": p1, "reason": "routing evidence"}, {"path": p2, "reason": "deployment evidence"}
        ], "discarded": []})
        ids = list(overlay.load(self.scope)["leaves"])
        comp = lso.apply_stage(self.scope, {"role": "compressor", "targets": [{
            "target_id": "svc_target", "label": "Service routing notes", "supporting_leaf_ids": ids
        }], "standalone_leaf_ids": []})
        self.assertTrue(comp["ok"])
        self.assertEqual(comp["next_task"]["tag_targets"], {"leaf_ids": [], "node_ids": ["svc_target"]})
        self.assertEqual(lso.apply_stage(self.scope, {"role": "tagger", "claims": [
            {"leaf_id": ids[0], "tag": "wrong target", "evidence": "Routing tables", "source": "agent_read"}
        ]})["error"], "tag_target_invalid")
        tag = lso.apply_stage(self.scope, {"role": "tagger", "claims": [
            {"node_id": "svc_target", "tag": "routing tables", "evidence": "Routing tables", "source": "agent_read"},
            {"node_id": "svc_target", "tag": "deployment review", "evidence": "Deployment review", "source": "agent_read"},
            {"node_id": "svc_target", "tag": "service routing", "evidence": "Routing tables guide service ownership.", "source": "file_content"},
        ]})
        self.assertTrue(tag["ok"])
        agg = lso.apply_stage(self.scope, {"role": "aggregator", "facet_nodes": [{
            "node_id": "routing_facet", "label": "routing tables", "tags": ["routing tables"], "derived_from_ids": [ids[0], "svc_target"], "layer": "facet",
        }], "semantic_nodes": [{
            "label": "Operational ownership", "tags": ["routing tables", "deployment review"],
            "derived_from_ids": ["routing_facet"], "layer": "semantic",
        }]})
        self.assertTrue(agg["ok"]); self.assertTrue(lso.query(self.scope, "routing")["hits"])
        self.assertEqual(set(overlay.load(self.scope)["nodes"]["routing_facet"]["supporting_leaf_ids"]), set(ids))
        semantic_hit = next(x for x in lso.query(self.scope, "Operational")["hits"] if x["hit_type"] == "node")
        self.assertEqual(semantic_hit["derived_from_ids"], ["routing_facet"])
        final = lso.apply_stage(self.scope, {"role": "auditor", "verdict": "PASS", "evidence": "independent review"})
        self.assertTrue(final["ok"]); self.assertEqual(runner._rw(self.scope, "build_state.json")["stage"], "complete")

    def test_ga_adapter_uses_cli_file_io_for_all_roles(self):
        p = self._file("note.md", "Readable evidence.")
        tasks = lso.role_tasks(self.scope, paths=[p], question="build overlay")
        self.assertEqual(set(tasks), set(overlay.ROLES)); self.assertEqual(tasks["selector"]["candidates"][0]["path"], p)
        self.assertTrue(tasks["tagger"]["optional_document_extract"]["module"].endswith("document_extract.py"))
        comp_rule = tasks["compressor"]["rule"]
        self.assertIn("entry file", comp_rule); self.assertIn("project structure", comp_rule)
        self.assertIn("concrete name", comp_rule); self.assertIn("same directory", comp_rule)
        tag_rule = tasks["tagger"]["rule"]
        self.assertIn("multiple semantic facets", tag_rule); self.assertIn("avoid single generic tags", tag_rule)
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "temp") as raw:
            task_name, out = Path(raw).name, Path(raw); info = lso.write_task_dir(task_name, "compressor", tasks["compressor"])
            self.assertTrue((out / "input.txt").is_file()); self.assertTrue((out / "context.json").is_file())
            self.assertEqual(info["command"], f"python agentmain.py --task {task_name} --verbose")
            (out / "artifact.json").write_text('{"role":"auditor","verdict":"PASS"}', encoding="utf-8")
            self.assertEqual(lso.read_artifact(out)["role"], "auditor")
            runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "auditor"})
            (out / "artifact.json").write_text('{"role":"auditor","verdict":"PASS","checks":[]}', encoding="utf-8")
            rejected = lso.apply_task_artifact(self.scope, out)
            self.assertEqual(rejected["error"], "unknown_fields"); self.assertTrue((out / "reply.txt").is_file())
            self.assertIn("checks", (out / "artifact.json").read_text(encoding="utf-8"))
            (out / "artifact.json").write_text('{"role":"auditor","verdict":"PASS","evidence":"subagent rewrite"}', encoding="utf-8")
            self.assertTrue(lso.apply_task_artifact(self.scope, out)["ok"]); self.assertFalse((out / "reply.txt").exists())
            (out / "_history.json").write_text("[]", encoding="utf-8"); (out / "_stop").write_text("", encoding="utf-8")
            lso.write_task_dir(task_name, "compressor", tasks["compressor"])
            self.assertFalse((out / "artifact.json").exists()); self.assertFalse((out / "_history.json").exists()); self.assertFalse((out / "_stop").exists())
        with mock.patch.object(ga_multiagent.os, "kill") as kill:
            lso.close_task("123")
        self.assertEqual(kill.call_args.args[0], 123)
        with mock.patch.object(ga_multiagent.os, "kill", side_effect=ProcessLookupError): lso.close_task(123)

    def test_source_driver_serializes_roles_and_keeps_corrections_in_subagent(self):
        selector_task = {"role": "selector", "batch": {"offset": 0, "size": 1, "total": 1}}
        compressor_task = {"role": "compressor"}
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "temp") as raw:
            task_name, root = Path(raw).name, Path(raw)
            with mock.patch.object(runner, "prepare", return_value={"ok": True, "task": selector_task}), \
                 mock.patch.object(ga_multiagent, "launch_task", side_effect=[101, 202]) as launch, \
                 mock.patch.object(ga_multiagent, "wait_artifact", side_effect=[1.0, 2.0, 3.0]) as wait, \
                 mock.patch.object(ga_multiagent, "close_task") as close, \
                 mock.patch.object(runner, "apply_task_artifact", side_effect=[
                     {"ok": False, "correction_required": True, "error": "unknown_fields"},
                     {"ok": True, "next_task": compressor_task},
                     {"ok": True, "next_task": None},
                 ]) as apply:
                result = lso.run_build(self.scope, task_name=task_name, reset=True,
                                       progress_path=root / "progress.json",
                                       timings_path=root / "timings.json",
                                       log_path=root / "driver.log")
            self.assertEqual(result["status"], "complete")
            self.assertEqual([x["role"] for x in result["roles_done"]], ["selector", "compressor"])
            self.assertEqual(launch.call_count, 2); self.assertEqual(wait.call_count, 3)
            self.assertEqual([x.args[0] for x in close.call_args_list], [101, 202])
            self.assertEqual(apply.call_count, 3)
            self.assertEqual(json.loads((root / "progress.json").read_text(encoding="utf-8"))["status"], "complete")

    def test_contract_failures_and_candidate_focused_audit(self):
        p = self._file("note.md", "Readable evidence.")
        overlay.apply_artifact(self.scope, {"role": "selector", "retained": [{"path": p, "reason": "evidence"}]})
        leaf_id = next(iter(overlay.load(self.scope)["leaves"]))
        runner._rw(self.scope, "compressor_artifact.json", {"standalone_leaf_ids": [leaf_id], "targets": []})
        runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "tagger"})
        self.assertEqual(lso.apply_stage(self.scope, {"role": "tagger", "tags": []})["error"], "unknown_fields")
        self.assertEqual(lso.validate_artifact({"role": "tagger"})["error"], "missing_payload")
        self.assertEqual(lso.apply_stage(self.scope, {"role": "aggregator", "facet_nodes": []})["error"], "wrong_stage")
        partial = lso.apply_stage(self.scope, {"role": "tagger", "claims": [
            {"leaf_id": leaf_id, "tag": "valid", "evidence": "Readable evidence", "source": "file_content"},
            {"leaf_id": "missing", "tag": "invalid", "evidence": "none", "source": "file_content"},
        ]})
        self.assertEqual(partial["error"], "tag_target_invalid")
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "temp") as raw:
            out = Path(raw); bad = {"role": "tagger", "claims": [{"leaf_id": "missing", "tag": "invalid", "evidence": "none", "source": "file_content"}]}
            (out / "artifact.json").write_text(json.dumps(bad), encoding="utf-8")
            rejected = lso.apply_task_artifact(self.scope, out)
            self.assertEqual(rejected["error"], "tag_target_invalid"); self.assertTrue((out / "reply.txt").is_file())
            self.assertEqual(json.loads((out / "artifact.json").read_text(encoding="utf-8")), bad)
        self.assertEqual(overlay.load(self.scope)["leaves"][leaf_id]["tags"], [])
        invalid_aggregate = overlay.apply_artifact(self.scope, {"role": "aggregator", "facet_nodes": [
            {"node_id": "bypass", "label": "Bypass", "supporting_leaf_ids": [leaf_id]}
        ]})
        self.assertTrue(invalid_aggregate["rejected"]); self.assertNotIn("bypass", overlay.load(self.scope)["nodes"])
        runner._rw(self.scope, "candidate_pool.json", {"count": 1, "items": [{"path": p}]})
        runner._rw(self.scope, "selector_ledger.json", {"batches": []})
        facts = lso.coverage_audit(self.scope, probes=["definitelyabsent"])
        self.assertEqual({x["type"] for x in facts["issues"]}, {"unclassified_candidates", "untagged_targets", "probe_miss"})
        runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "auditor"})
        reviewed = lso.apply_stage(self.scope, {"role": "auditor", "verdict": "PASS", "evidence": "auditor accepts the facts"}, probes=["definitelyabsent"])
        self.assertTrue(reviewed["ok"]); self.assertIn("probe_miss", {x["type"] for x in reviewed["coverage_audit"]["issues"]})

    def test_empty_semantic_outputs_reach_independent_auditor(self):
        p = self._file("noise.tmp", "generated")
        runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "selector", "mode": "cold_start", "selector_offset": 0})
        runner._rw(self.scope, "candidate_pool.json", {"count": 1, "items": [{"path": p}]})
        runner._rw(self.scope, "selector_ledger.json", {"batches": []})
        retained_result = lso.apply_stage(self.scope, {"role": "selector", "retained": [], "discarded": [{"path": p, "reason": "generated", "noise_evidence": "temporary suffix"}]})
        self.assertEqual(retained_result["next_task"]["role"], "auditor")
        self.assertFalse(any(p in str(x) for x in overlay.load(self.scope)["events"]))
        runner._rw(self.scope, "build_state.json", {"scope": self.scope, "stage": "aggregator", "question": ""})
        empty_agg = lso.apply_stage(self.scope, {"role": "aggregator", "facet_nodes": [], "semantic_nodes": []})
        self.assertTrue(empty_agg["ok"]); self.assertEqual(empty_agg["next_task"]["role"], "auditor")

    def test_incremental_prepare_only_recalls_new_files_and_auditor_can_rework(self):
        old = self._file("old.md", "Already covered."); new = self._file("new.md", "New ES hit.")
        overlay.apply_artifact(self.scope, {"role": "selector", "retained": [{"path": old, "reason": "existing"}]})
        old_id = next(iter(overlay.load(self.scope)["leaves"]))
        overlay.apply_artifact(self.scope, {"role": "compressor", "targets": [{"target_id": "old_compressed", "label": "Old docs", "supporting_leaf_ids": [old_id]}]})
        overlay.apply_artifact(self.scope, {"role": "tagger", "claims": [{"leaf_id": old_id, "tag": "old tag", "evidence": "Already covered", "source": "file_content"}]})
        overlay.apply_artifact(self.scope, {"role": "aggregator", "facet_nodes": [{"node_id": "old_facet", "label": "Old facet", "derived_from_ids": ["old_compressed"]}]})
        with mock.patch.object(runner, "discover_candidates", side_effect=AssertionError("seeded incremental must not run cold discovery")):
            prep = lso.prepare(self.scope, question="miss query", seeds=[{"path": old}, {"path": new}, {"path": str(REPO_ROOT / "agentmain.py")}], reset=False)
        self.assertEqual([x["path"] for x in prep["candidate_pool"]["items"]], [new])
        lso.apply_stage(self.scope, {"role": "selector", "retained": [{"path": new, "reason": "new hit"}], "discarded": []})
        new_id = next(k for k, v in overlay.load(self.scope)["leaves"].items() if v["path"] == str(Path(new).resolve()))
        lso.apply_stage(self.scope, {"role": "compressor", "targets": [{"target_id": "new_compressed", "label": "New docs", "supporting_leaf_ids": [new_id]}], "standalone_leaf_ids": []})
        lso.apply_stage(self.scope, {"role": "tagger", "claims": [{"node_id": "new_compressed", "tag": "new tag", "evidence": "New ES hit", "source": "file_content"}]})
        lso.apply_stage(self.scope, {"role": "aggregator", "facet_nodes": [{"node_id": "new_facet", "label": "New facet", "derived_from_ids": ["new_compressed"]}], "semantic_nodes": []})
        result = lso.apply_stage(self.scope, {"role": "auditor", "verdict": "FAIL", "evidence": "tags need repair", "rework_role": "tagger"})
        self.assertFalse(result["ok"]); self.assertEqual(result["next_task"]["role"], "tagger")
        self.assertEqual(result["next_task"]["audit_evidence"], "tags need repair")
        data = overlay.load(self.scope)
        self.assertEqual(set(data["nodes"]), {"old_compressed", "old_facet", "new_compressed"})
        self.assertEqual(data["leaves"][old_id]["tags"], ["old tag"]); self.assertEqual(data["leaves"][new_id]["tags"], [])

if __name__ == "__main__":
    unittest.main()
