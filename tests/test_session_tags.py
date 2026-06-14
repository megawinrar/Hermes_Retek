"""Tests for session_tags module."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.session_tags as st


SAMPLE_ROUTE_L3 = {
    "task_level": "L3",
    "task_type": "code_change",
    "risk_level": "medium",
    "process_plan": ["router", "supervisor", "architect", "bot1", "tester", "bot2"],
    "skill_context": {
        "selected_skills": [
            {
                "name": "hermes-developer",
                "tags": ["code", "tdd", "implementation"],
            },
            {
                "name": "hermes-tester",
                "tags": ["testing", "qa"],
            },
        ]
    },
}

SAMPLE_ROUTE_L0 = {
    "task_level": "L0",
    "task_type": "standard_task",
    "risk_level": "low",
    "process_plan": ["router", "supervisor"],
    "skill_context": {"selected_skills": []},
}

SAMPLE_ROUTE_L4_FINANCE = {
    "task_level": "L4",
    "task_type": "finance",
    "risk_level": "high",
    "process_plan": ["router", "supervisor", "architect", "bot1", "tester", "bot2", "devops_if_approved"],
    "skill_context": {
        "selected_skills": [
            {"name": "some-skill", "tags": ["analysis", "tender", "client"]},
        ]
    },
}

SAMPLE_ROUTE_L3_MIGRATION = {
    "task_level": "L3",
    "task_type": "database_migration_plan",
    "risk_level": "high",
    "process_plan": ["router", "supervisor", "architect", "bot1", "tester", "bot2"],
    "skill_context": {
        "selected_skills": [
            {"name": "hermes-architect", "tags": ["architecture", "design"]},
            {"name": "hermes-tester", "tags": ["testing"]},
        ]
    },
}

SAMPLE_ROUTE_L2_SUPPLIER = {
    "task_level": "L2",
    "task_type": "supplier_price_deadline_analysis",
    "risk_level": "high",
    "process_plan": ["router", "supervisor", "bot1", "tester", "bot2_light_if_risky"],
    "skill_context": {
        "selected_skills": [
            {"name": "hermes-analyst", "tags": ["analysis", "requirements"]},
        ]
    },
}


def setup_module(module):
    """Ensure tags table exists in temp test DB."""
    # Use /tmp for test DB
    st.get_store_path = lambda: None  # override
    # Also set env for connect
    os.environ["SUPERVISOR_STORE"] = "/tmp/test_session_tags.db"
    # Create table
    con = st.connect("/tmp/test_session_tags.db")
    con.close()


def teardown_module(module):
    """Clean up test DB."""
    try:
        os.remove("/tmp/test_session_tags.db")
        os.remove("/tmp/test_session_tags.db-wal")
        os.remove("/tmp/test_session_tags.db-shm")
    except FileNotFoundError:
        pass


class TestBuildTags:
    """Tests for tag derivation logic."""

    def test_build_tags_from_L3_code_change(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L3, "Fix bug in process")
        tag_paths = {t["tag_path"] for t in tags}
        assert "уровень/L3" in tag_paths
        assert "риск/medium" in tag_paths
        assert "core/code/code_change" in tag_paths
        assert "core/pipeline/bot2" in tag_paths
        assert "core/code/TDD" in tag_paths
        assert "core/code/testing" in tag_paths
        assert len(tags) >= 7

    def test_build_tags_from_L0(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L0, "Status check")
        tag_paths = {t["tag_path"] for t in tags}
        assert "уровень/L0" in tag_paths
        assert "риск/low" in tag_paths
        assert "process" in tag_paths
        # No code tags for L0 standard_task
        assert "core/code/code_change" not in tag_paths

    def test_build_tags_finance_high_risk(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L4_FINANCE, "Tender analysis")
        tag_paths = {t["tag_path"] for t in tags}
        assert "уровень/L4" in tag_paths
        assert "риск/high" in tag_paths
        assert "domain/finance" in tag_paths
        assert "domain/analytics" in tag_paths
        assert "domain/tender" in tag_paths
        assert "domain/client" in tag_paths
        assert "devops/deploy" in tag_paths

    def test_build_tags_for_database_migration(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L3_MIGRATION, "Plan DB migration")
        tag_paths = {t["tag_path"] for t in tags}
        assert "уровень/L3" in tag_paths
        assert "риск/high" in tag_paths
        assert "core/architecture" in tag_paths
        assert "process/planning" in tag_paths
        assert "core/code/testing" in tag_paths
        assert "core/pipeline/bot2" in tag_paths

    def test_build_tags_for_supplier_analysis(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L2_SUPPLIER, "Compare suppliers")
        tag_paths = {t["tag_path"] for t in tags}
        assert "уровень/L2" in tag_paths
        assert "риск/high" in tag_paths
        assert "domain/tender" in tag_paths
        assert "domain/analytics" in tag_paths
        assert "core/pipeline/bot2" in tag_paths

    def test_labels_are_human_readable(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L3, "Some task")
        labels = {t["tag_label"] for t in tags}
        assert "Уровень сложности: L3" in labels
        assert "Риск: medium" in labels
        assert "Изменение кода" in labels

    def test_no_duplicate_tag_paths(self):
        tags = st.build_tags_from_route(SAMPLE_ROUTE_L3, "Task")
        paths = [t["tag_path"] for t in tags]
        assert len(paths) == len(set(paths)), f"Duplicates: {paths}"


class TestSaveAndSearch:
    """Tests for persistence and search."""

    STORE = "/tmp/test_session_tags.db"

    def test_save_and_retrieve(self):
        count = st.save_session_tags(
            "test-proc-001", SAMPLE_ROUTE_L3,
            task_title="Fix pipeline bug",
            store_path=self.STORE,
        )
        assert count > 0

        results = st.search_by_tags(
            levels=["L3"], limit=10, store_path=self.STORE
        )
        matches = [r for r in results if r["task_title"] == "Fix pipeline bug"]
        assert len(matches) >= 1
        assert matches[0]["session_id"] == "test-proc-001"
        assert matches[0]["level"] == "L3"

    def test_search_by_domain(self):
        st.save_session_tags("test-proc-002", SAMPLE_ROUTE_L3, store_path=self.STORE)
        st.save_session_tags("test-proc-003", SAMPLE_ROUTE_L4_FINANCE, store_path=self.STORE)

        results = st.search_by_tags(
            domains=["domain"], limit=10, store_path=self.STORE
        )
        session_ids = {r["session_id"] for r in results}
        assert "test-proc-003" in session_ids

        results = st.search_by_tags(
            domains=["meta"], limit=10, store_path=self.STORE
        )
        session_ids = {r["session_id"] for r in results}
        assert "test-proc-001" in session_ids

    def test_search_by_multiple_criteria(self):
        st.save_session_tags(
            "test-proc-004", SAMPLE_ROUTE_L4_FINANCE,
            store_path=self.STORE,
        )

        results = st.search_by_tags(
            levels=["L4"],
            risks=["high"],
            domains=["domain"],
            limit=10,
            store_path=self.STORE,
        )
        assert any(r["session_id"] == "test-proc-004" for r in results)

    def test_search_empty(self):
        results = st.search_by_tags(
            levels=["XL"], limit=10, store_path=self.STORE
        )
        assert len(results) == 0

    def test_tags_returned_with_session(self):
        st.save_session_tags("test-proc-005", SAMPLE_ROUTE_L3, store_path=self.STORE)
        results = st.search_by_tags(
            levels=["L3"], limit=10, store_path=self.STORE
        )
        for r in results:
            if r["session_id"] == "test-proc-005":
                assert len(r["tags"]) > 0
                assert all("tag_path" in t for t in r["tags"])
                assert all("tag_label" in t for t in r["tags"])
                break
        else:
            assert False, "Session not found"


class TestTagTree:
    """Tests for tag tree utility."""

    def test_get_tag_tree_structure(self):
        tree = st.get_tag_tree()
        assert "core" in tree
        assert "core/architecture" in str(tree)
        assert "code" in tree["core"]
        assert "code_change" in tree["core"]["code"]

    def test_tag_tree_labels(self):
        tree = st.get_tag_tree()
        assert tree["core"]["_label"] == "Ядро"
        assert tree["core"]["code"]["_label"] == "Код"
        assert tree["core"]["code"]["code_change"]["_label"] == "Изменение кода"


class TestCountByDomain:
    """Tests for domain counting."""

    STORE = "/tmp/test_session_tags.db"

    def test_count_by_domain(self):
        counts = st.count_by_domain(store_path=self.STORE)
        domains = {c["domain"] for c in counts}
        assert "meta" in domains
        assert "core" in domains
        assert any(c["count"] > 0 for c in counts)
