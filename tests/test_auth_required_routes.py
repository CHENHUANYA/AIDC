import unittest

from app_context import FeedbackRequest, IngestTextRequest
from auth import (
    CreateUserRequest,
    api_create_user,
    api_list_sessions,
    api_list_users,
)
from issues import CreateIssue, UpdateIssue, api_create_issue, api_escalate_issue, api_issue_stats, api_list_issues, api_update_issue
from routes.chat_lookup_routes import lookup_alarm
from routes.ingest_routes import get_all_ingest_log, ingest_text, list_documents
from routes.static_reference_routes import action_numbers
from routes.stats_routes import alarm_stats, error_stats, feedback_stats, query_stats, save_feedback
from work_orders import (
    CreateWorkOrder,
    UpdateWorkOrder,
    api_create_order,
    api_delete_order,
    api_get_order,
    api_get_order_history,
    api_list_orders,
    api_order_stats,
    api_update_order,
    api_work_order_archive,
)


UNAUTHENTICATED_ACTOR = {"user_id": "", "role": "", "line_scope": [], "team": ""}


class AuthRequiredRouteTests(unittest.IsolatedAsyncioTestCase):
    def assert_not_authenticated(self, payload):
        self.assertEqual("error", payload["status"])
        self.assertEqual("Not authenticated", payload["message"])

    async def test_issue_list_and_stats_require_login(self):
        self.assert_not_authenticated(await api_list_issues(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_issue_stats(actor=UNAUTHENTICATED_ACTOR))

    async def test_work_order_list_stats_and_archive_require_login(self):
        self.assert_not_authenticated(await api_list_orders(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_order_stats(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_work_order_archive(actor=UNAUTHENTICATED_ACTOR))

    async def test_feedback_write_requires_login(self):
        result = await save_feedback(
            FeedbackRequest(query="q", collection="808d", feedback="good"),
            actor=UNAUTHENTICATED_ACTOR,
        )

        self.assert_not_authenticated(result)

    async def test_admin_user_routes_distinguish_unauthenticated(self):
        self.assert_not_authenticated(await api_list_users(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_list_sessions(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_create_user(
            CreateUserRequest(user_id="new-admin", password="secret123"),
            actor=UNAUTHENTICATED_ACTOR,
        ))

    async def test_mutation_routes_distinguish_unauthenticated(self):
        self.assert_not_authenticated(await api_create_issue(
            CreateIssue(machine_id="M1", description="Alarm"),
            actor=UNAUTHENTICATED_ACTOR,
        ))
        self.assert_not_authenticated(await api_update_issue(
            "ISS-missing",
            UpdateIssue(status="open"),
            actor=UNAUTHENTICATED_ACTOR,
        ))
        self.assert_not_authenticated(await api_escalate_issue("ISS-missing", actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_create_order(
            CreateWorkOrder(alarm_code="3000"),
            actor=UNAUTHENTICATED_ACTOR,
        ))
        self.assert_not_authenticated(await api_update_order(
            "WO-missing",
            UpdateWorkOrder(status="in_progress"),
            actor=UNAUTHENTICATED_ACTOR,
        ))
        self.assert_not_authenticated(await api_delete_order("WO-missing", actor=UNAUTHENTICATED_ACTOR))

    async def test_admin_stats_and_ingest_routes_distinguish_unauthenticated(self):
        self.assert_not_authenticated(await alarm_stats(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await feedback_stats(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await query_stats(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await error_stats(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await ingest_text(
            "808d",
            IngestTextRequest(text="hello"),
            actor=UNAUTHENTICATED_ACTOR,
        ))

    async def test_data_get_routes_distinguish_unauthenticated(self):
        self.assert_not_authenticated(await api_get_order("missing", actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await api_get_order_history("missing", actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await lookup_alarm("808d", "3000", actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await get_all_ingest_log(actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await list_documents("808d", actor=UNAUTHENTICATED_ACTOR))
        self.assert_not_authenticated(await action_numbers("808d", actor=UNAUTHENTICATED_ACTOR))


if __name__ == "__main__":
    unittest.main()
