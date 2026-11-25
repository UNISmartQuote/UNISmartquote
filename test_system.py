import base64
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import Mock, patch
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw

import ai_reader
from ai_reader import (
    ask_openai_compatible,
    ask_ollama,
    normalize,
    normalize_quote_metadata,
    validate_candidate,
)
import migrate_sqlite_to_postgres
import security_store
from pricing_logic import (
    allocate_profit_by_cost,
    calculate_quote,
    validate_pdf,
)


class AIReaderTests(unittest.TestCase):
    def test_openai_compatible_request_uses_secrets_and_parses_json(self):
        requests = []
        response_body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "direction": "export",
                                "fees": [{"name": "Ocean Freight", "rate": 300}],
                            }
                        )
                    }
                }
            ]
        }

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return io.BytesIO(json.dumps(response_body).encode("utf-8"))

        fake_opener = Mock()
        fake_opener.open.side_effect = fake_urlopen
        with patch(
            "ai_reader.urllib.request.build_opener",
            return_value=fake_opener,
        ) as build_opener:
            result = ask_openai_compatible(
                "Pricing text",
                [],
                model="vision-model",
                base_url="https://ai.example.test/v1/",
                api_key="not-a-real-test-key",
            )

        self.assertTrue(
            any(
                handler is ai_reader._NoRedirectHandler
                for handler in build_opener.call_args.args
            )
        )
        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(
            request.full_url,
            "https://ai.example.test/v1/chat/completions",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer not-a-real-test-key")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["model"], "vision-model")
        self.assertEqual(timeout, 180)
        self.assertEqual(result["fees"][0]["rate"], 300)

    def test_openai_compatible_failure_does_not_expose_api_key(self):
        from urllib.error import HTTPError

        api_key = "not-a-real-test-key"
        error = HTTPError(
            "https://ai.example.test/v1/chat/completions",
            429,
            "rate limited",
            {},
            io.BytesIO(b""),
        )
        fake_opener = Mock()
        fake_opener.open.side_effect = error
        with patch(
            "ai_reader.urllib.request.build_opener",
            return_value=fake_opener,
        ):
            with self.assertRaises(RuntimeError) as raised:
                ask_openai_compatible(
                    "Pricing text",
                    [],
                    model="vision-model",
                    base_url="https://ai.example.test/v1",
                    api_key=api_key,
                )

        self.assertIn("429", str(raised.exception))
        self.assertNotIn(api_key, str(raised.exception))
        self.assertTrue(error.fp.closed)

    def test_openai_compatible_image_request_uses_inline_png(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(image_buffer, format="PNG")
        requests = []
        response_body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"table_row_count": 0, "warnings": [], "fees": []}
                        )
                    }
                }
            ]
        }

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps(response_body).encode("utf-8"))

        fake_opener = Mock()
        fake_opener.open.side_effect = fake_urlopen
        with patch(
            "ai_reader.urllib.request.build_opener",
            return_value=fake_opener,
        ):
            result = ask_openai_compatible(
                "",
                [image_buffer.getvalue()],
                model="vision-model",
                base_url="https://ai.example.test/v1",
                api_key="not-a-real-test-key",
            )

        content = requests[0]["messages"][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )
        self.assertEqual(result["fees"], [])

    def test_quote_metadata_only_uses_explicit_source_values(self):
        fields, warnings = normalize_quote_metadata(
            {
                "customer": "Customer A",
                "pol": "Ho Chi Minh City",
                "pod": "Incheon",
                "incoterm": "FOB",
                "valid_until": "2026-12-01",
                "commodity": "Coffee",
                "carrier": "KMTC",
                "container_type": "40'HC",
                "container_quantity": 2,
            }
        )

        self.assertEqual(fields["quote_customer"], "Customer A")
        self.assertEqual(fields["quote_pol"], "Ho Chi Minh City")
        self.assertEqual(fields["quote_pod"], "Incheon")
        self.assertEqual(fields["quote_incoterm"], "FOB")
        self.assertEqual(fields["quote_container"], "40HC")
        self.assertEqual(fields["quote_qty"], 2)
        self.assertEqual(fields["quote_valid_until"].isoformat(), "2026-12-01")
        self.assertEqual(fields["quote_commodity"], "Coffee")
        self.assertEqual(fields["quote_carrier"], "KMTC")
        self.assertEqual(warnings, [])

    def test_missing_quote_metadata_stays_blank_and_is_flagged(self):
        fields, warnings = normalize_quote_metadata({})

        self.assertEqual(fields["quote_customer"], "")
        self.assertEqual(fields["quote_pol"], "")
        self.assertIsNone(fields["quote_qty"])
        self.assertIsNone(fields["quote_valid_until"])
        self.assertTrue(any("khách hàng" in item for item in warnings))
        self.assertTrue(any("số container" in item for item in warnings))

    def test_export_direction_with_context_does_not_raise_false_warning(self):
        _, warnings = normalize({"direction": "Vietnam export", "fees": []})
        _, import_warnings = normalize({"direction": "import to Vietnam", "fees": []})

        self.assertFalse(any("hướng vận chuyển" in item for item in warnings))
        self.assertTrue(
            any("hướng vận chuyển" in item for item in import_warnings)
        )

    def test_incomplete_and_non_usd_fees_are_preserved_for_review(self):
        candidate, warnings = normalize(
            {
                "direction": "export",
                "fees": [
                    {
                        "name": "Ocean Freight",
                        "rate": None,
                        "unit": "CONT",
                        "quantity": None,
                        "currency": "USD",
                        "group": "OCEAN FREIGHT",
                    },
                    {
                        "name": "Origin Handling",
                        "rate": 100,
                        "unit": "HBL",
                        "quantity": 2,
                        "currency": "VND",
                        "group": "ORIGIN LOCAL CHARGES",
                    },
                    {
                        "name": "Free Documentation",
                        "rate": 0,
                        "unit": "HBL",
                        "quantity": 1,
                        "currency": "USD",
                        "group": "ORIGIN LOCAL CHARGES",
                    },
                ],
            }
        )

        self.assertEqual(len(candidate), 3)
        self.assertTrue(pd.isna(candidate.iloc[0]["Giá mua (USD)"]))
        self.assertTrue(pd.isna(candidate.iloc[0]["Số lượng"]))
        self.assertTrue(pd.isna(candidate.iloc[1]["Giá mua (USD)"]))
        self.assertIn("Giá nguồn: 100", candidate.iloc[1]["Ghi chú"])
        self.assertEqual(candidate.iloc[2]["Giá mua (USD)"], 0)
        self.assertTrue(any("chưa quy đổi sang USD" in item for item in warnings))
        self.assertTrue(validate_candidate(candidate))

        candidate.loc[0, "Giá mua (USD)"] = 200
        candidate.loc[0, "Số lượng"] = 1
        candidate.loc[1, "Giá mua (USD)"] = 150
        self.assertEqual(validate_candidate(candidate), [])

    def test_explicit_dollar_symbol_is_normalized_as_usd(self):
        candidate, warnings = normalize(
            {
                "direction": "export",
                "fees": [
                    {
                        "name": "THC",
                        "rate": 200,
                        "unit": "CONT",
                        "quantity": 1,
                        "currency_mark": "$",
                        "group": "CHƯA PHÂN LOẠI",
                    }
                ],
            }
        )

        self.assertEqual(candidate.iloc[0]["Giá mua (USD)"], 200)
        self.assertFalse(any("đơn vị tiền" in item for item in warnings))

    def test_at_cost_text_is_not_mistaken_for_a_numeric_rate(self):
        candidate, warnings = normalize(
            {
                "direction": "export",
                "fees": [
                    {
                        "name": "Import Duties and tax",
                        "rate": 70,
                        "unit": "SHPT",
                        "quantity": 1,
                        "currency": "USD",
                        "group": "CHƯA PHÂN LOẠI",
                        "note": "at cost",
                    }
                ],
            }
        )

        self.assertTrue(pd.isna(candidate.iloc[0]["Giá mua (USD)"]))
        self.assertEqual(candidate.iloc[0]["Ghi chú"], "at cost")
        self.assertTrue(any("thiếu/không hợp lệ giá mua" in item for item in warnings))

    def test_image_extraction_uses_focused_prompt_and_checks_row_count(self):
        image = Image.new("RGB", (300, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 30, 250, 150), outline="black", width=2)
        draw.line((50, 90, 250, 90), fill="black", width=2)
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")

        model_result = {
            "table_row_count": 2,
            "warnings": [],
            "fees": [
                {
                    "name": "THC",
                    "rate": 0,
                    "unit": "CONT",
                    "currency": "USD",
                }
            ],
        }
        response = io.BytesIO(
            json.dumps(
                {"message": {"content": json.dumps(model_result)}}
            ).encode("utf-8")
        )
        with patch("ai_reader.urllib.request.urlopen", return_value=response) as call:
            result = ask_ollama("", [image_bytes.getvalue()])

        payload = json.loads(call.call_args.args[0].data)
        self.assertIn("buying-price column", payload["messages"][0]["content"])
        self.assertIn("every visible fee row", payload["messages"][1]["content"])
        processed_image = Image.open(
            io.BytesIO(
            base64.b64decode(
                    payload["messages"][1]["images"][0]
                )
            )
        )
        self.assertGreater(processed_image.width, image.width)
        self.assertTrue(
            any("Ảnh có 2 dòng" in item for item in result["warnings"])
        )

    def test_image_extraction_verifies_currency_on_ambiguous_fee_rows(self):
        image = Image.new("RGB", (300, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 270, 150), outline="black", width=2)
        draw.line((30, 90, 270, 90), fill="black", width=2)
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")

        initial_result = {
            "table_row_count": 2,
            "fees": [
                {
                    "name": "AMS",
                    "rate": 35,
                    "unit": "HBL",
                    "currency": None,
                    "note": "at cost",
                },
                {
                    "name": "DOCS",
                    "rate": 40,
                    "unit": "HBL",
                    "currency": "USD",
                    "note": None,
                },
            ],
        }
        responses = [
            io.BytesIO(
                json.dumps(
                    {"message": {"content": json.dumps(initial_result)}}
                ).encode("utf-8")
            ),
            io.BytesIO(
                json.dumps(
                    {"message": {"content": json.dumps({"price_text": "$35"})}}
                ).encode("utf-8")
            ),
        ]
        with patch(
            "ai_reader.urllib.request.urlopen", side_effect=responses
        ) as urlopen:
            result = ask_ollama("", [image_bytes.getvalue()])

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(result["fees"][0]["rate"], 35)
        self.assertEqual(result["fees"][0]["currency"], "USD")
        self.assertEqual(result["fees"][0]["currency_mark"], "$")
        self.assertEqual(result["fees"][0]["note"], "")


class AIQuotationWorkflowTests(unittest.TestCase):
    def test_customer_pdf_contains_the_company_logo(self):
        script = """
import fitz
import pandas as pd
import app_cu

fields = app_cu._new_quote_defaults()
fields["quote_valid_until"] = fields["quote_date"]
rows = pd.DataFrame(columns=app_cu.FEE_COLUMNS + ["Thành tiền bán"])
pdf_data = app_cu.create_pdf(rows, fields, {"total_sell": 0})
with fitz.open(stream=pdf_data, filetype="pdf") as pdf:
    print(len(pdf[0].get_images(full=True)))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.path.dirname(__file__),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertGreater(int(result.stdout.strip()), 0)

    def test_pricing_upload_creates_and_saves_a_draft_automatically(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        sample_result = {
            "direction": "HCM - SYDNEY",
            "pol": "HCM",
            "pod": "Incheon",
            "incoterm": "FOB",
            "valid_until": "2026-12-01",
            "container_type": "20GP",
            "container_quantity": 1,
            "fees": [
                {
                    "name": "Ocean Freight",
                    "rate": 300,
                    "unit": "CONT",
                    "quantity": 1,
                    "currency": "USD",
                    "group": "OCEAN FREIGHT",
                }
            ],
        }
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                with (
                    patch(
                        "ai_reader.read_source",
                        return_value=("pricing", [], []),
                    ),
                    patch(
                        "ai_reader.ask_ollama",
                        return_value=sample_result,
                    ),
                ):
                    app = AppTest.from_file(app_path)
                    app.session_state["auth_user_id"] = user_id
                    app.session_state["sales_navigation"] = "quotes"
                    app.run(timeout=15)
                    app.file_uploader[0].upload(
                        "pricing.txt", b"pricing", "text/plain"
                    )
                    app.run(timeout=15)
                    next(
                        button
                        for button in app.button
                        if button.label
                        == "🤖 ĐỌC FILE VÀ TẠO BÁO GIÁ NHÁP"
                    ).click()
                    app.run()

                self.assertEqual(list(app.exception), [])
                quotes = security_store.list_quotes(user_id)
                self.assertEqual(len(quotes), 1)
                saved = security_store.get_quote(user_id, quotes[0]["id"])
                self.assertEqual(
                    saved["payload"]["fields"]["quote_customer"],
                    "",
                )
                self.assertIsNone(
                    saved["payload"]["fees"][0]["Profit (USD)"]
                )
                self.assertTrue(
                    saved["payload"]["fees"][0]["Hiển thị PDF"]
                )
                self.assertTrue(
                    any(
                        "hướng vận chuyển" in warning
                        for warning in app.session_state["ai_warnings"]
                    )
                )
                self.assertTrue(
                    any("khách hàng" in warning.value for warning in app.warning)
                )
                billable_editor = next(
                    editor
                    for editor in app.data_editor
                    if editor.key.startswith("billable_editor_")
                )
                app.session_state[billable_editor.key] = {
                    "edited_rows": {0: {"Profit (USD)": 130.0}},
                    "added_rows": [],
                    "deleted_rows": [],
                }
                app.run()
                self.assertEqual(list(app.exception), [])
                self.assertEqual(
                    app.session_state["edited_fees"].iloc[0]["Profit (USD)"],
                    130,
                )
                self.assertEqual(
                    app.session_state["edited_fees"].iloc[0]["Giá bán (USD)"],
                    430,
                )
                next(
                    button
                    for button in app.button
                    if button.label == "💾 LƯU BẢN NHÁP"
                ).click()
                app.run()
                saved = security_store.get_quote(user_id, quotes[0]["id"])
                self.assertEqual(
                    saved["payload"]["fees"][0]["Profit (USD)"], 130
                )
                self.assertEqual(
                    saved["payload"]["fees"][0]["Giá bán (USD)"], 430
                )
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path


    def test_cloud_ai_requires_consent_before_calling_provider(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        sample_result = {
            "direction": "export",
            "pol": "HCM",
            "pod": "Incheon",
            "incoterm": "FOB",
            "valid_until": "2026-12-01",
            "container_type": "20GP",
            "container_quantity": 1,
            "fees": [
                {
                    "name": "Ocean Freight",
                    "rate": 300,
                    "unit": "CONT",
                    "quantity": 1,
                    "currency": "USD",
                    "group": "OCEAN FREIGHT",
                }
            ],
        }
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "cloudsales",
                    "Cloud Sales Test",
                    "Cloud-sales-test-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                with (
                    patch(
                        "app_config.ai_settings",
                        return_value={
                            "provider": "openai_compatible",
                            "base_url": "https://ai.example.test/v1",
                            "model": "vision-model",
                            "api_key": "not-a-real-test-key",
                        },
                    ),
                    patch(
                        "ai_reader.read_source",
                        return_value=("pricing", [], []),
                    ),
                    patch(
                        "ai_reader.ask_openai_compatible",
                        return_value=sample_result,
                    ) as ask_ai,
                ):
                    app = AppTest.from_file(app_path)
                    app.session_state["auth_user_id"] = user_id
                    app.session_state["sales_navigation"] = "quotes"
                    app.run(timeout=15)
                    app.file_uploader[0].upload(
                        "pricing.txt", b"pricing", "text/plain"
                    )
                    app.run(timeout=15)

                    button = next(
                        button
                        for button in app.button
                        if button.label
                        == "🤖 ĐỌC FILE VÀ TẠO BÁO GIÁ NHÁP"
                    )
                    self.assertTrue(button.disabled)
                    self.assertEqual(ask_ai.call_count, 0)
                    consent = next(
                        checkbox
                        for checkbox in app.checkbox
                        if checkbox.label
                        == "Tôi cho phép gửi tệp này tới AI Cloud để trích xuất."
                    )
                    app.session_state[consent.key] = True
                    app.run(timeout=15)
                    next(
                        button
                        for button in app.button
                        if button.label
                        == "🤖 ĐỌC FILE VÀ TẠO BÁO GIÁ NHÁP"
                    ).click()
                    app.run(timeout=15)

                self.assertEqual(list(app.exception), [])
                self.assertEqual(ask_ai.call_count, 1)
                self.assertEqual(len(security_store.list_quotes(user_id)), 1)
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path


class DashboardNavigationTests(unittest.TestCase):
    def test_admin_workspace_matches_shared_toolbar_and_banners(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                admin_id = security_store.create_bootstrap_admin(
                    "admin",
                    "Test Admin",
                    "Admin-password-123",
                )
                sales_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Sales-password-123",
                    role="sales",
                )

                app = AppTest.from_file(
                    os.path.join(os.path.dirname(__file__), "app_cu.py")
                )
                app.session_state["auth_user_id"] = admin_id
                app.session_state["admin_navigation"] = "accounts"
                app.run(timeout=15)

                self.assertEqual(list(app.exception), [])
                markup = "\n".join(str(item.value) for item in app.markdown)
                self.assertIn("ui-admin-hero", markup)
                self.assertTrue(
                    any(button.key == "admin-menu" for button in app.button)
                )
                self.assertTrue(
                    any(button.key == "admin-notifications" for button in app.button)
                )
                self.assertTrue(
                    any(button.key == "admin-settings" for button in app.button)
                )
                self.assertTrue(
                    any(button.key == "admin-profile" for button in app.button)
                )

                app.text_input(key="admin-account-search").set_value(
                    "Sales"
                ).run(timeout=15)
                self.assertTrue(
                    any(
                        "Sales Test" in str(item.value)
                        for item in app.markdown
                    )
                )

                app.button(key="admin-settings").click().run(timeout=15)
                self.assertEqual(
                    app.session_state["admin_navigation"],
                    "settings",
                )
                markup = "\n".join(str(item.value) for item in app.markdown)
                self.assertIn("Mô hình Ollama", markup)
                self.assertTrue(
                    any(
                        item.label == "Tên mô hình Ollama"
                        for item in app.text_input
                    )
                )
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_sales_dashboard_shows_owned_quotes_without_ollama_input(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                security_store.save_quote(
                    user_id,
                    None,
                    "QT-TEST-1",
                    "Customer A",
                    {"fields": {}, "fees": []},
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.run(timeout=15)

                self.assertEqual(list(app.exception), [])
                self.assertTrue(
                    any(item.label == "Tổng báo giá" for item in app.metric)
                )
                self.assertFalse(
                    any(item.label == "Tên mô hình Ollama" for item in app.text_input)
                )
                self.assertTrue(
                    any(
                        item.label == "Tổng báo giá" and item.value == "1"
                        for item in app.metric
                    )
                )
                dashboard_markup = "\n".join(
                    str(item.value) for item in app.markdown
                )
                self.assertIn("ui-dashboard-hero", dashboard_markup)
                self.assertIn("ui-dashboard-recent-table", dashboard_markup)
                self.assertTrue(
                    any(item.key == "dashboard-menu" for item in app.button)
                )
                self.assertTrue(
                    any(item.key == "dashboard-notifications" for item in app.button)
                )
                self.assertTrue(
                    any(item.key == "dashboard-settings" for item in app.button)
                )
                self.assertTrue(
                    any(item.key == "dashboard-profile" for item in app.button)
                )

                app.button(key="dashboard-menu").click().run(timeout=15)
                self.assertTrue(app.session_state["dashboard_sidebar_collapsed"])
                app.button(key="dashboard-menu").click().run(timeout=15)
                self.assertFalse(app.session_state["dashboard_sidebar_collapsed"])

                app.button(key="dashboard-profile").click().run(timeout=15)
                self.assertIn(
                    "Sales Test",
                    "\n".join(str(item.value) for item in app.markdown),
                )
                app.button(key="dashboard-profile").click().run(timeout=15)

                app.text_input(key="dashboard-search").set_value(
                    "not-a-customer"
                ).run(timeout=15)
                self.assertTrue(
                    any(
                        "Không tìm thấy báo giá" in item.value
                        for item in app.info
                    )
                )

                app.button(key="dashboard-notifications").click().run(timeout=15)
                self.assertIn(
                    "Thông báo",
                    "\n".join(str(item.value) for item in app.markdown),
                )
                app.button(key="dashboard-notifications-history").click().run(
                    timeout=15
                )
                self.assertEqual(app.session_state["sales_navigation"], "history")
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_account_controls_and_profile_edit_are_functional(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "account"
                app.run(timeout=15)

                self.assertEqual(list(app.exception), [])
                self.assertTrue(
                    any(button.key == "dashboard-menu" for button in app.button)
                )
                self.assertTrue(
                    any(
                        item.label == "Giao diện"
                        for item in app.selectbox
                    )
                )
                self.assertIn(
                    "ui-sales-hero",
                    "\n".join(str(item.value) for item in app.markdown),
                )

                app.button(key="sales_profile_edit_button").click().run(
                    timeout=15
                )
                app.text_input(key="sales_profile_display_name").set_value(
                    "Updated Sales"
                )
                next(
                    button
                    for button in app.button
                    if button.label == "LƯU THÔNG TIN"
                ).click().run(timeout=15)
                self.assertEqual(
                    security_store.get_user(user_id)["display_name"],
                    "Updated Sales",
                )

                app.toggle(key="sales_notifications_enabled").set_value(
                    False
                ).run(timeout=15)
                self.assertFalse(
                    app.session_state["sales_notifications_enabled"]
                )
                app.selectbox(key="sales_theme").select("Tối").run(timeout=15)
                self.assertIn(
                    "#10243a",
                    "\n".join(str(item.value) for item in app.markdown),
                )
                self.assertEqual(list(app.exception), [])
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_sales_sidebar_switches_between_quotes_and_account(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "account"
                app.run(timeout=15)
                self.assertEqual(list(app.exception), [])
                self.assertTrue(
                    any(item.value == "Đổi mật khẩu" for item in app.subheader)
                )

                app.session_state["sales_navigation"] = "quotes"
                app.run(timeout=15)
                self.assertEqual(list(app.exception), [])
                self.assertEqual(len(app.data_editor), 2)
                self.assertTrue(
                    any(
                        button.key == "dashboard-settings"
                        for button in app.button
                    )
                )
                self.assertIn(
                    "ui-sales-hero",
                    "\n".join(str(item.value) for item in app.markdown),
                )
                self.assertTrue(
                    any(button.key == "reset_quote_workspace" for button in app.button)
                )
                app.button(key="dashboard-settings").click().run(timeout=15)
                self.assertEqual(app.session_state["sales_navigation"], "account")
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_opening_saved_quote_returns_to_quote_editor(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                quote_id = security_store.save_quote(
                    user_id,
                    None,
                    "QT-OPEN-1",
                    "Customer A",
                    {"fields": {}, "fees": []},
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "history"
                app.run(timeout=15)
                self.assertEqual(list(app.exception), [])

                app.checkbox(key=f"history_selected_{quote_id}").check().run(
                    timeout=15
                )
                app.button(key="open_quote_from_history").click().run(timeout=15)

                self.assertEqual(list(app.exception), [])
                self.assertEqual(
                    app.session_state["sales_navigation"], "quotes"
                )
                self.assertEqual(app.session_state["current_quote_id"], quote_id)
                self.assertEqual(len(app.data_editor), 2)
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_sales_can_delete_a_quote_after_confirming(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                quote_id = security_store.save_quote(
                    user_id,
                    None,
                    "QT-DELETE-1",
                    "Customer A",
                    {"fields": {}, "fees": []},
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "history"
                app.run(timeout=15)
                self.assertEqual(list(app.exception), [])

                app.checkbox(key=f"history_selected_{quote_id}").check().run(
                    timeout=15
                )
                next(
                    button
                    for button in app.button
                    if button.key == "request_quote_delete"
                ).click()
                app.run(timeout=15)
                next(
                    button
                    for button in app.button
                    if button.key == "confirm_quote_delete_button"
                ).click()
                app.run(timeout=15)

                self.assertEqual(list(app.exception), [])
                self.assertEqual(security_store.list_quotes(user_id), [])
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path


class SecurityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
            self.temp_dir.name, "quotes.sqlite3"
        )
        security_store.initialize()
        self.admin_id = security_store.create_bootstrap_admin(
            "admin", "Test Admin", "Admin-password-123"
        )
        self.alice_id = security_store.create_user(
            "alice", "Alice Sales", "Alice-password-123", role="sales"
        )
        self.bob_id = security_store.create_user(
            "bob", "Bob Sales", "Bob-password-1234", role="sales"
        )

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("UI_QUOTE_DB_PATH", None)
        else:
            os.environ["UI_QUOTE_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    def test_password_authentication_and_owner_scoped_quotes(self):
        self.assertIsNotNone(
            security_store.authenticate("alice", "Alice-password-123")
        )
        self.assertIsNone(
            security_store.authenticate("alice", "incorrect-password")
        )

        quote_id = security_store.save_quote(
            self.alice_id,
            None,
            "Q-001",
            "Customer A",
            {"notes": "private"},
        )
        self.assertEqual(len(security_store.list_quotes(self.alice_id)), 1)
        self.assertEqual(security_store.list_quotes(self.bob_id), [])
        self.assertEqual(security_store.list_quotes(self.admin_id), [])
        self.assertIsNone(security_store.get_quote(self.bob_id, quote_id))
        self.assertFalse(security_store.delete_quote(self.bob_id, quote_id))
        self.assertEqual(
            security_store.get_quote(self.alice_id, quote_id)["payload"],
            {"notes": "private"},
        )

    def test_ollama_model_setting_is_admin_only_and_persistent(self):
        self.assertEqual(security_store.get_ollama_model(), "qwen2.5vl:3b")
        with self.assertRaises(PermissionError):
            security_store.set_ollama_model(self.alice_id, "qwen2.5vl:7b")

        security_store.set_ollama_model(self.admin_id, "qwen2.5vl:7b")

        self.assertEqual(security_store.get_ollama_model(), "qwen2.5vl:7b")

    def test_sales_can_update_display_name_without_changing_login(self):
        security_store.update_display_name(self.alice_id, "Alice Updated")

        updated = security_store.get_user(self.alice_id)
        self.assertEqual(updated["display_name"], "Alice Updated")
        self.assertEqual(updated["username"], "alice")
        with self.assertRaises(ValueError):
            security_store.update_display_name(self.alice_id, "  ")

    def test_bulk_quote_delete_is_owner_scoped_and_all_or_none(self):
        own_ids = [
            security_store.save_quote(
                self.alice_id, None, f"ALICE-{index}", "Customer A", {}
            )
            for index in range(2)
        ]
        other_id = security_store.save_quote(
            self.bob_id, None, "BOB-1", "Customer B", {}
        )

        self.assertEqual(
            security_store.delete_quotes(
                self.alice_id, [own_ids[0], other_id]
            ),
            [],
        )
        self.assertEqual(len(security_store.list_quotes(self.alice_id)), 2)

        self.assertEqual(
            security_store.delete_quotes(self.alice_id, own_ids),
            own_ids,
        )
        self.assertEqual(security_store.list_quotes(self.alice_id), [])
        self.assertEqual(len(security_store.list_quotes(self.bob_id)), 1)


class PostgreSQLConnectionTests(unittest.TestCase):
    def test_postgres_connection_sets_schema_owner_context_and_ssl(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []
                self.committed = False
                self.closed = False

            def execute(self, query, parameters=()):
                self.calls.append((query, parameters))

            def commit(self):
                self.committed = True

            def rollback(self):
                raise AssertionError("Unexpected transaction rollback.")

            def close(self):
                self.closed = True

        fake_connection = FakeConnection()
        with (
            patch(
                "security_store.app_config.database_url",
                return_value=(
                    "postgresql://app@db.example.test/quotes?sslmode=verify-full"
                ),
            ),
            patch(
                "security_store.psycopg.connect",
                return_value=fake_connection,
            ) as connect,
        ):
            with security_store._connection(actor_user_id=42) as connection:
                connection.execute(
                    "SELECT id FROM quotes WHERE owner_id = ?", (42,)
                )

        self.assertEqual(connect.call_args.kwargs["sslmode"], "verify-full")
        self.assertTrue(
            any(
                query == "SELECT set_config('app.current_user_id', %s, TRUE)"
                and parameters == ("42",)
                for query, parameters in fake_connection.calls
            )
        )
        self.assertTrue(
            any(
                query == "SELECT id FROM quotes WHERE owner_id = %s"
                and parameters == (42,)
                for query, parameters in fake_connection.calls
            )
        )
        self.assertTrue(fake_connection.committed)
        self.assertTrue(fake_connection.closed)


class SQLiteMigrationTests(unittest.TestCase):
    def _create_source(self, path, usernames=("alice",)):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    must_change_password INTEGER NOT NULL,
                    failed_attempts INTEGER NOT NULL,
                    locked_until REAL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE quotes (
                    id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    quote_number TEXT NOT NULL,
                    customer TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            for index, username in enumerate(usernames, start=1):
                connection.execute(
                    """
                    INSERT INTO users
                        (id, username, display_name, password_salt,
                         password_hash, role, active, must_change_password,
                         failed_attempts, locked_until, created_at)
                    VALUES (?, ?, ?, ?, ?, 'sales', 1, 0, 0, NULL, ?)
                    """,
                    (
                        index,
                        username,
                        "Sample Sales",
                        b"sample-salt",
                        b"sample-hash",
                        "2026-01-01 00:00:00",
                    ),
                )
            connection.execute(
                """
                INSERT INTO quotes
                    (id, owner_id, quote_number, customer, updated_at, payload)
                VALUES ('sample-quote', 1, 'Q-SAMPLE', 'Sample customer',
                        '2026-01-01 00:00:00', '{}')
                """
            )
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('ollama_model', 'sample-model', '2026-01-01 00:00:00')
                """
            )

    def test_dry_run_counts_only_fake_source_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.sqlite3"
            self._create_source(source)

            counts = migrate_sqlite_to_postgres.inspect_source(source)

        self.assertEqual(
            counts,
            {"users": 1, "quotes": 1, "app_settings": 1},
        )

    def test_dry_run_rejects_case_insensitive_duplicate_usernames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.sqlite3"
            self._create_source(source, usernames=("alice", "ALICE"))

            with self.assertRaisesRegex(
                ValueError, "duplicate case-insensitive usernames"
            ):
                migrate_sqlite_to_postgres.inspect_source(source)


class PricingTests(unittest.TestCase):
    def test_total_profit_is_allocated_by_extended_purchase_cost(self):
        fees = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Profit (USD)": 0,
                    "Hiển thị PDF": True,
                },
                {
                    "Nhóm phí": "ORIGIN LOCAL CHARGES",
                    "Khoản phí": "Documentation",
                    "Đơn vị": "HBL",
                    "Số lượng": 2,
                    "Giá mua (USD)": 50,
                    "Profit (USD)": 0,
                    "Hiển thị PDF": True,
                },
            ]
        )

        allocated = allocate_profit_by_cost(fees, 200)

        self.assertEqual(allocated.iloc[0]["Profit (USD)"], 150)
        self.assertEqual(allocated.iloc[1]["Profit (USD)"], 25)
        self.assertEqual(
            (allocated["Profit (USD)"] * allocated["Số lượng"]).sum(),
            200,
        )

    def test_profit_allocation_rejects_missing_cost_basis(self):
        fees = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": None,
                    "Profit (USD)": None,
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "giá mua"):
            allocate_profit_by_cost(fees, 100)

    def test_profit_slider_explains_missing_fee_quantity(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "quotes"
                app.session_state["fees"] = pd.DataFrame(
                    [
                        {
                            "Nhóm phí": "OCEAN FREIGHT",
                            "Khoản phí": "Ocean Freight",
                            "Đơn vị": "CONT",
                            "Số lượng": None,
                            "Giá mua (USD)": 300,
                            "Profit (USD)": None,
                            "Giá bán (USD)": 300,
                            "Ghi chú": "",
                            "Hiển thị PDF": True,
                        }
                    ]
                )
                app.session_state["profit_markup_percent"] = 50.0
                app.run()

                self.assertEqual(list(app.exception), [])
                self.assertTrue(
                    any(
                        "thiếu số lượng" in item.value.lower()
                        for item in app.warning
                    )
                )
                self.assertEqual(
                    app.session_state["profit_last_applied_percent"], 0
                )
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_profit_slider_updates_quote_fee_rows_automatically(self):
        from streamlit.testing.v1 import AppTest

        previous_db_path = os.environ.get("UI_QUOTE_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.environ["UI_QUOTE_DB_PATH"] = os.path.join(
                    temp_dir, "quotes.sqlite3"
                )
                security_store.initialize()
                user_id = security_store.create_user(
                    "sales",
                    "Sales Test",
                    "Test-password-123",
                    role="sales",
                )
                app_path = os.path.join(
                    os.path.dirname(__file__), "app_cu.py"
                )
                app = AppTest.from_file(app_path)
                app.session_state["auth_user_id"] = user_id
                app.session_state["sales_navigation"] = "quotes"
                app.session_state["fees"] = pd.DataFrame(
                    [
                        {
                            "Nhóm phí": "OCEAN FREIGHT",
                            "Khoản phí": "Ocean Freight",
                            "Đơn vị": "CONT",
                            "Số lượng": 1,
                            "Giá mua (USD)": 300,
                            "Profit (USD)": 0,
                            "Giá bán (USD)": 300,
                            "Ghi chú": "",
                            "Hiển thị PDF": True,
                        },
                        {
                            "Nhóm phí": "ORIGIN LOCAL CHARGES",
                            "Khoản phí": "Documentation",
                            "Đơn vị": "HBL",
                            "Số lượng": 2,
                            "Giá mua (USD)": 50,
                            "Profit (USD)": 0,
                            "Giá bán (USD)": 50,
                            "Ghi chú": "",
                            "Hiển thị PDF": True,
                        },
                    ]
                )
                app.session_state["profit_markup_percent"] = 50.0
                app.run()

                self.assertEqual(list(app.exception), [])
                self.assertEqual(app.slider[0].max, 100)
                allocated = app.session_state["edited_fees"]
                self.assertEqual(allocated.iloc[0]["Profit (USD)"], 150)
                self.assertEqual(allocated.iloc[1]["Profit (USD)"], 25)
                self.assertEqual(
                    app.session_state["profit_last_applied_percent"], 50
                )
        finally:
            if previous_db_path is None:
                os.environ.pop("UI_QUOTE_DB_PATH", None)
            else:
                os.environ["UI_QUOTE_DB_PATH"] = previous_db_path

    def test_incomplete_ai_fee_values_remain_blank_and_block_pdf(self):
        rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": None,
                    "Giá mua (USD)": None,
                    "Profit (USD)": None,
                    "Hiển thị PDF": True,
                }
            ]
        )

        fees, public, _ = calculate_quote(rows)
        errors = validate_pdf(
            {
                "quote_customer": "Customer A",
                "quote_number": "Q-001",
                "quote_pol": "POL",
                "quote_pod": "POD",
                "quote_incoterm": "FOB",
                "quote_container": "20GP",
                "quote_qty": 1,
                "quote_date": pd.Timestamp("2026-01-01").date(),
                "quote_valid_until": pd.Timestamp("2026-01-08").date(),
            },
            public,
            fees,
        )

        self.assertTrue(pd.isna(fees.iloc[0]["Số lượng"]))
        self.assertTrue(pd.isna(fees.iloc[0]["Giá mua (USD)"]))
        self.assertTrue(pd.isna(fees.iloc[0]["Profit (USD)"]))
        self.assertIn("Cần xác nhận giá mua cho mọi khoản phí công khai.", errors)
        self.assertIn("Cần nhập Profit cho mọi khoản phí công khai.", errors)
        self.assertIn("Số lượng phí công khai phải lớn hơn 0.", errors)

    def test_manual_profit_is_added_to_each_unit_cost(self):
        rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Profit (USD)": 130,
                    "Hiển thị PDF": True,
                },
                {
                    "Nhóm phí": "CHI PHÍ NỘI BỘ",
                    "Khoản phí": "Commission",
                    "Đơn vị": "JOB",
                    "Số lượng": 1,
                    "Giá mua (USD)": 10,
                    "Profit (USD)": 0,
                    "Hiển thị PDF": True,
                },
            ]
        )

        fees, public, totals = calculate_quote(rows)

        self.assertEqual(fees.iloc[0]["Giá bán (USD)"], 430)
        self.assertEqual(fees.iloc[0]["Profit thành tiền"], 130)
        self.assertEqual(list(public["Khoản phí"]), ["Ocean Freight"])
        self.assertEqual(totals["total_cost"], 310)
        self.assertEqual(totals["total_sell"], 430)
        self.assertEqual(totals["net_profit"], 120)

    def test_existing_rows_keep_profit_from_saved_buy_and_sell_prices(self):
        legacy_rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Giá bán (USD)": 430,
                    "Hiển thị PDF": True,
                }
            ]
        )

        fees, _, _ = calculate_quote(legacy_rows)

        self.assertEqual(fees.iloc[0]["Profit (USD)"], 130)
        self.assertEqual(fees.iloc[0]["Giá bán (USD)"], 430)

    def test_unclassified_fee_blocks_customer_pdf(self):
        rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Profit (USD)": 130,
                    "Hiển thị PDF": True,
                },
                {
                    "Nhóm phí": "CHƯA PHÂN LOẠI",
                    "Khoản phí": "Unconfirmed charge",
                    "Đơn vị": "HBL",
                    "Số lượng": 1,
                    "Giá mua (USD)": 10,
                    "Profit (USD)": 0,
                    "Hiển thị PDF": False,
                },
            ]
        )
        fees, public, _ = calculate_quote(rows)
        errors = validate_pdf(
            {
                "quote_customer": "Customer",
                "quote_number": "Q-001",
                "quote_pol": "POL",
                "quote_pod": "POD",
                "quote_date": pd.Timestamp("2026-01-01").date(),
                "quote_valid_until": pd.Timestamp("2026-01-08").date(),
            },
            public,
            fees,
        )

        self.assertIn(
            "Còn phí chưa phân loại hoặc thiếu nhóm; hãy phân loại hoặc xóa khoản không áp dụng.",
            errors,
        )

    def test_customer_pdf_requires_a_quote_number(self):
        rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "CONT",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Profit (USD)": 130,
                    "Hiển thị PDF": True,
                }
            ]
        )
        fees, public, _ = calculate_quote(rows)
        errors = validate_pdf(
            {
                "quote_customer": "Customer",
                "quote_number": "",
                "quote_pol": "POL",
                "quote_pod": "POD",
                "quote_date": pd.Timestamp("2026-01-01").date(),
                "quote_valid_until": pd.Timestamp("2026-01-08").date(),
            },
            public,
            fees,
        )

        self.assertIn("Thiếu mã báo giá.", errors)

    def test_customer_pdf_requires_a_confirmed_billing_unit(self):
        rows = pd.DataFrame(
            [
                {
                    "Nhóm phí": "OCEAN FREIGHT",
                    "Khoản phí": "Ocean Freight",
                    "Đơn vị": "USD",
                    "Số lượng": 1,
                    "Giá mua (USD)": 300,
                    "Profit (USD)": 130,
                    "Hiển thị PDF": True,
                }
            ]
        )
        fees, public, _ = calculate_quote(rows)
        errors = validate_pdf(
            {
                "quote_customer": "Customer",
                "quote_number": "Q-001",
                "quote_pol": "POL",
                "quote_pod": "POD",
                "quote_date": pd.Timestamp("2026-01-01").date(),
                "quote_valid_until": pd.Timestamp("2026-01-08").date(),
            },
            public,
            fees,
        )

        self.assertIn(
            "Cần xác nhận đơn vị tính phí cho mọi dòng công khai.",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
