import importlib
import unittest


SCRIPT_MODULES = [
    "scripts.bootstrap_env",
    "scripts.data_maintenance",
    "scripts.local_validation_bundle",
    "scripts.model_cache",
    "scripts.n8n_workflow_check",
    "scripts.preflight_check",
    "scripts.postgresql_backup",
    "scripts.postgresql_concurrency_check",
    "scripts.postgresql_health",
    "scripts.postgresql_ha",
    "scripts.postgresql_offsite_backup",
    "scripts.postgresql_pilot_readiness",
    "scripts.postgresql_secret_rotation",
    "scripts.postgresql_pilot_load",
    "scripts.postgresql_phase4_cutover",
    "scripts.postgresql_phase4_runtime_acceptance",
    "scripts.postgresql_phase5_soak",
    "scripts.postgresql_pitr",
    "scripts.production_boundary_check",
    "scripts.rag_offline_evaluation",
    "scripts.regression_checks",
    "scripts.replay_demo_alarms",
    "scripts.role_console_smoke",
    "scripts.runtime_soak",
    "scripts.seed_week2_data",
    "scripts.smoke_test",
    "scripts.week4_acceptance",
]


class ScriptImportTests(unittest.TestCase):
    def test_maintenance_scripts_are_importable(self):
        for module_name in SCRIPT_MODULES:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
